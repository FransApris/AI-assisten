"""
chat_server.py — APRIS Web Chat Backend
========================================
Flask server yang menghubungkan Web Chat UI ke Gemini AI (APRIS).

Endpoints:
    POST /chat          — kirim pesan, dapat balasan APRIS
    GET  /history       — ambil riwayat percakapan sesi ini
    POST /clear         — hapus riwayat percakapan
    GET  /status        — cek status server & model
"""

import os
import sys
import json
import uuid
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

# Pastikan folder ini ada di sys.path agar import features/* selalu berhasil
sys.path.insert(0, str(Path(__file__).parent))

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY", "")
CHAT_MODEL        = os.getenv("GEMINI_CHAT_MODEL", "models/gemini-2.5-flash")
EMBEDDING_MODEL   = os.getenv("GEMINI_EMBEDDING_MODEL", "models/gemini-embedding-001")
# Railway set PORT otomatis; fallback ke 5052 untuk lokal
SERVER_PORT       = int(os.getenv("PORT", os.getenv("CHAT_SERVER_PORT", 5052)))
# TTL sesi (jam) dan batas ukuran file upload (byte)
SESSION_TTL_HOURS = int(os.getenv("SESSION_TTL_HOURS", 24))
MAX_FILE_BYTES    = int(os.getenv("MAX_FILE_BYTES", 10 * 1024 * 1024))  # default 10 MB

# RAG Knowledge Base config
_RAG_DEFAULT  = str(Path(__file__).resolve().parent.parent / "rag-knowledge" / "vectorstore")
RAG_DB_PATH       = os.getenv("RAG_DB_PATH", _RAG_DEFAULT)
RAG_COLLECTION    = os.getenv("RAG_COLLECTION", "apris_knowledge")
RAG_TOP_K         = int(os.getenv("RAG_TOP_K", 3))
RAG_ENABLED       = os.getenv("RAG_ENABLED", "true").lower() == "true"

# Timezone WIB (UTC+7) — fallback tanpa tzdata
try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo(os.getenv("TZ", "Asia/Jakarta"))
except Exception:
    TZ = timezone(timedelta(hours=7))  # WIB fallback

if not GEMINI_API_KEY:
    print("[ERROR] GEMINI_API_KEY tidak ditemukan di .env!")

# System prompt APRIS
SYSTEM_PROMPT = open(
    Path(__file__).parent / "system_prompt.txt", encoding="utf-8"
).read() if Path(Path(__file__).parent / "system_prompt.txt").exists() else """Anda adalah APRIS (Asisten Pribadi Sistem), asisten virtual pribadi multidisiplin berbahasa Indonesia.

§1 IDENTITAS & PERAN
Anda menguasai: produktivitas, informasi, keuangan, humaniora (sastra, filsafat Katolik, seni), gaya hidup, hukum & administrasi.

§2 GAYA BAHASA
• Terapkan economy of words — langsung ke inti, tanpa basa-basi
• DILARANG sapaan berulang: "Halo!", "Tentu saja!", "Baik, saya akan..."
• DILARANG kalimat penutup: "Semoga membantu!", "Jangan ragu bertanya!"
• Jawaban WAJIB dalam format WhatsApp: *tebal* untuk penekanan/judul, _miring_ untuk kutipan/istilah.
• Gunakan bullet points (-) untuk daftar.
• Tulis dengan nada analitis, objektif, dan logis. Tanpa emoji berlebihan.

§3 PEMBUATAN DOKUMEN GOOGLE DRIVE
Jika pengguna meminta Anda untuk membuat/menulis/menyusun dokumen, laporan, resep, atau catatan panjang, Anda HARUS menyisipkan format persis seperti ini di akhir atau sebagai keseluruhan respons Anda:
<CREATE_DOC title="Judul Dokumen">Isi dokumen (lengkap dan detail) di sini...</CREATE_DOC>
PENTING: Hanya gunakan tag ini jika pengguna benar-benar meminta untuk dibuatkan dokumen/catatan/laporan yang disimpan.

§4 PENGELOLAAN KALENDER
Jika pengguna meminta untuk membuat/menjadwalkan agenda di kalender, sisipkan format ini:
<CREATE_EVENT title="Judul Acara" start="YYYY-MM-DDTHH:MM:SS+07:00" end="YYYY-MM-DDTHH:MM:SS+07:00">Deskripsi singkat acara...</CREATE_EVENT>
Gunakan format ISO 8601 dengan zona waktu WIB (+07:00). 
Jika pengguna bertanya tentang jadwal mereka (contoh: "Apa jadwal saya hari ini?"), sisipkan:
<CHECK_CALENDAR/>

§5 PENGELOLAAN EMAIL
Jika pengguna meminta untuk mengecek, membacakan, atau merangkum email/kotak masuk terbaru mereka, sisipkan format ini persis seperti ini:
<CHECK_EMAIL/>

§6 CUACA DAN LOKASI
Jika pengguna bertanya tentang cuaca (contoh: "Bagaimana cuaca di Jakarta hari ini?"), sisipkan format ini:
<CHECK_WEATHER location="Nama Kota"/>

§7 NOTION INTEGRATION
Jika pengguna meminta untuk mencatat, menyimpan, atau menulis sesuatu ke Notion, sisipkan format ini:
<NOTION_WRITE title="Judul Catatan">Isi catatan lengkap...</NOTION_WRITE>

§8 LONG-TERM MEMORY (INGATAN PERMANEN)
Jika pengguna meminta Anda untuk mengingat sesuatu tentang mereka (contoh: "Ingat ya, saya alergi kacang"), sisipkan tag berikut ke dalam jawaban Anda:
<REMEMBER fact="Pengguna alergi kacang"/>
Jika pengguna meminta Anda untuk melupakan sesuatu yang sudah Anda ingat, sisipkan tag berikut:
<FORGET fact="alergi kacang"/>

§9 PENGINGAT MEDIS (PILL TRACKER)
Jika pengguna menyebutkan bahwa mereka harus mengonsumsi obat tertentu secara rutin pada jam tertentu, jadwalkan pengingat menggunakan tag berikut:
<ADD_MEDICINE name="NamaObat" time="HH:MM" reason="Alasan medis/Manfaat obat"/>

§10 BATASAN
• DILARANG diagnosis medis definitif — arahkan ke dokter
• DILARANG nasihat hukum mengikat — arahkan ke pengacara
• Jujur jika tidak tahu atau data tidak real-time

Zona Waktu: WIB (Asia/Jakarta) | Bahasa: Indonesia | Versi: APRIS v3.0"""

# ---------------------------------------------------------------------------
# Gemini client & session history
# ---------------------------------------------------------------------------
_genai_client  = None
_chroma_col    = None           # ChromaDB collection (lazy load)
_chat_sessions = {}             # session_id → {"messages": list, "last_access": datetime}
_sessions_lock = threading.Lock()


def get_client():
    global _genai_client
    if _genai_client is None:
        from google import genai
        _genai_client = genai.Client(api_key=GEMINI_API_KEY)
    return _genai_client


def get_chroma():
    """Buat ChromaDB connection baru (thread-safe: tiap request punya client sendiri)."""
    try:
        import chromadb
        client = chromadb.PersistentClient(path=RAG_DB_PATH)
        col = client.get_collection(RAG_COLLECTION)
        return col
    except Exception as e:
        return None


def rag_retrieve(query: str, top_k: int = None) -> str:
    """
    Cari konteks relevan dari Knowledge Base.
    Return: string konteks untuk diinjeksi ke prompt, atau string kosong.
    """
    if not RAG_ENABLED:
        return ""
    col = get_chroma()
    if col is None:
        return ""
    try:
        k   = top_k or RAG_TOP_K
        emb = get_client().models.embed_content(
            model=EMBEDDING_MODEL, contents=[query]
        )
        vec     = emb.embeddings[0].values
        results = col.query(query_embeddings=[vec], n_results=k)
        docs    = results.get("documents", [[]])[0]
        metas   = results.get("metadatas", [[]])[0]
        if not docs:
            return ""
        # Format konteks — dedup per sumber
        parts = []
        seen  = set()
        for doc, meta in zip(docs, metas):
            src = meta.get("source", "knowledge base")
            if src not in seen:
                seen.add(src)
                parts.append(f"[Sumber: {src}]\n{doc[:700]}")
        return "\n\n".join(parts)
    except Exception as e:
        print(f"[RAG] Retrieval error: {e}")
        return ""

def generate_with_retry(client, model: str, contents, config=None, max_retries: int = 3):
    """
    Panggil generate_content dengan retry otomatis saat rate limit (429).
    Fallback: tunggu sesuai 'retry after' dari pesan error.
    """
    import time, re
    for attempt in range(max_retries):
        try:
            return client.models.generate_content(model=model, contents=contents, config=config)
        except Exception as e:
            err = str(e)
            if "429" in err or "RESOURCE_EXHAUSTED" in err:
                # Coba baca waktu tunggu dari pesan error
                match = re.search(r'retry in ([\d.]+)s', err)
                wait  = float(match.group(1)) + 2 if match else (15 * (attempt + 1))
                if attempt < max_retries - 1:
                    print(f"[Rate Limit] Tunggu {wait:.0f}s (attempt {attempt+1}/{max_retries})")
                    time.sleep(wait)
                else:
                    raise
            else:
                raise


def _cleanup_sessions():
    """Hapus sesi yang tidak aktif lebih dari SESSION_TTL_HOURS jam."""
    cutoff = datetime.now(TZ) - timedelta(hours=SESSION_TTL_HOURS)
    with _sessions_lock:
        expired = [
            sid for sid, data in _chat_sessions.items()
            if data["last_access"] < cutoff
        ]
        for sid in expired:
            del _chat_sessions[sid]
    if expired:
        print(f"[Session] Dibersihkan {len(expired)} sesi expired.", flush=True)


def get_or_create_session(session_id: str) -> list:
    _cleanup_sessions()
    now = datetime.now(TZ)
    with _sessions_lock:
        if session_id not in _chat_sessions:
            _chat_sessions[session_id] = {"messages": [], "last_access": now}
        else:
            _chat_sessions[session_id]["last_access"] = now
        return _chat_sessions[session_id]["messages"]


# ---------------------------------------------------------------------------
# Flask App
# ---------------------------------------------------------------------------
app = Flask(__name__, static_folder="web_chat", static_url_path="/static")
CORS(app)


@app.route("/")
def index():
    return send_from_directory("web_chat", "index.html")

@app.route("/briefing")
def briefing():
    try:
        import google_calendar
        import google_gmail
        from features import weather
        
        cal = google_calendar.get_upcoming_events(3)
        eml = google_gmail.get_recent_emails(3)
        # Default cuaca ke Jakarta jika tidak tahu lokasi user
        wth = weather.get_weather_by_city("Jakarta")
        
        brief = (
            "☀️ **Selamat Pagi! Berikut adalah Briefing Harian Anda:**\n\n"
            f"🌤️ **Cuaca Hari Ini:**\n{wth}\n\n"
            f"📅 **Jadwal Terdekat:**\n{cal}\n\n"
            f"📧 **Email Belum Dibaca:**\n{eml}\n\n"
            "Semoga hari Anda produktif! Ada yang bisa saya bantu hari ini?"
        )
        return jsonify({"briefing": brief})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/meds")
def get_meds():
    try:
        from features import medical
        return jsonify({"meds": medical.get_all_meds()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/meds/take", methods=["POST"])
def take_meds():
    try:
        data = request.get_json()
        name = data.get("name")
        time = data.get("time")
        from features import medical
        res = medical.mark_taken(name, time)
        return jsonify({"success": res})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/health")
@app.route("/status")
def status():
    rag_ok = get_chroma() is not None
    rag_count = 0
    if rag_ok:
        try: rag_count = get_chroma().count()
        except: pass
    return jsonify({
        "status"    : "online",
        "model"     : CHAT_MODEL,
        "version"   : "APRIS v3.0",
        "timestamp" : datetime.now(TZ).isoformat(),
        "sessions"  : len(_chat_sessions),
        "env"       : "railway" if os.getenv("RAILWAY_ENVIRONMENT") else "local",
        "rag"       : {"enabled": RAG_ENABLED, "connected": rag_ok, "chunks": rag_count},
    })


@app.route("/chat", methods=["POST"])
def chat():
    data       = request.get_json(silent=True) or {}
    user_msg   = (data.get("message") or "").strip()
    session_id = data.get("session_id") or "default"

    if not user_msg:
        return jsonify({"error": "Pesan tidak boleh kosong."}), 400

    if not GEMINI_API_KEY:
        return jsonify({"error": "GEMINI_API_KEY belum dikonfigurasi."}), 500

    history = get_or_create_session(session_id)

    try:
        from google import genai
        from google.genai import types

        client = get_client()

        # Bangun contents: system prompt + history + pesan baru
        contents = []

        # Sisipkan system prompt sebagai pesan pertama user/model
        if not history:
            now_str_full = datetime.now(TZ).strftime("%A, %Y-%m-%d %H:%M:%S %z")
            dynamic_prompt = f"[SYSTEM]\nWaktu saat ini: {now_str_full}\n\n{SYSTEM_PROMPT}"
            contents.append(
                types.Content(role="user",  parts=[types.Part(text=dynamic_prompt)])
            )
            contents.append(
                types.Content(role="model", parts=[types.Part(text="Siap. Saya adalah APRIS.")])
            )

        # History percakapan sebelumnya
        for msg in history:
            contents.append(
                types.Content(
                    role  = "user"  if msg["role"] == "user" else "model",
                    parts = [types.Part(text=msg["content"])]
                )
            )

        # RAG: cari konteks relevan dari Knowledge Base
        rag_context = rag_retrieve(user_msg)
        
        # Web Scraper: cari URL di pesan user dan ekstrak isinya
        url_context = ""
        from features import web_scraper
        urls = web_scraper.extract_urls(user_msg)
        if urls:
            scraped_texts = []
            for url in urls:
                scraped_texts.append(f"--- Konten dari {url} ---\n{web_scraper.scrape_url_text(url)}")
            url_context = "\n\n".join(scraped_texts)

        # Long-Term Memory: tarik fakta pengguna
        try:
            from features import memory
            long_term_memory = memory.get_all_memories()
        except Exception:
            long_term_memory = ""

        # Medical Module: tarik status obat
        try:
            from features import medical
            medical_context = medical.get_meds_summary()
        except Exception:
            medical_context = ""

        augmented_msg = user_msg
        if rag_context or url_context or long_term_memory or medical_context:
            augmented_msg += "\n\n"
            if medical_context:
                augmented_msg += f"---\n[Konteks Medis Pengguna]:\n{medical_context}\n---\n\n"
            if long_term_memory:
                augmented_msg += f"---\n[Konteks dari Long-Term Memory]:\n{long_term_memory}\n---\n\n"
            if rag_context:
                augmented_msg += f"---\n[Konteks dari Knowledge Base APRIS]:\n{rag_context}\n---\n\n"
            if url_context:
                augmented_msg += f"---\n[Konteks dari URL yang diberikan User]:\n{url_context}\n---\n"

        # Pesan baru user (sudah diaugmentasi dengan konteks RAG)
        user_parts = [types.Part(text=augmented_msg)]
        
        file_base64 = data.get("file_base64")
        if file_base64:
            import base64
            # Validasi ukuran sebelum decode (base64 ≈ 1.33× ukuran asli)
            if len(file_base64) > MAX_FILE_BYTES * 1.4:
                return jsonify({"error": f"File terlalu besar. Batas maksimal {MAX_FILE_BYTES // (1024*1024)} MB."}), 413
            file_bytes = base64.b64decode(file_base64)
            if len(file_bytes) > MAX_FILE_BYTES:
                return jsonify({"error": f"File terlalu besar. Batas maksimal {MAX_FILE_BYTES // (1024*1024)} MB."}), 413
            mime_type = data.get("file_mime", "application/octet-stream")
            file_name = data.get("file_name", "document")

            if mime_type.startswith("image/") or mime_type.startswith("audio/"):
                user_parts.append(
                    types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
                )
            else:
                doc_text = ""
                try:
                    if "pdf" in mime_type:
                        import fitz
                        doc = fitz.open("pdf", file_bytes)
                        for page in doc:
                            doc_text += page.get_text()
                        doc.close()
                    elif "wordprocessingml" in mime_type or "msword" in mime_type:
                        from docx import Document
                        import io
                        doc = Document(io.BytesIO(file_bytes))
                        doc_text = "\n".join([para.text for para in doc.paragraphs])
                    elif "text/plain" in mime_type:
                        doc_text = file_bytes.decode('utf-8', errors='ignore')
                    
                    if doc_text:
                        augmented_msg += f"\n\n---\n[Isi Dokumen {file_name}]:\n{doc_text}\n---\n"
                        user_parts[0] = types.Part(text=augmented_msg)
                except Exception as e:
                    augmented_msg += f"\n\n---\n[Gagal membaca dokumen {file_name}: {str(e)}]\n---\n"
                    user_parts[0] = types.Part(text=augmented_msg)

        contents.append(
            types.Content(role="user", parts=user_parts)
        )

        # Aktifkan Google Search tools
        search_config = types.GenerateContentConfig(
            tools=[{"google_search": {}}]
        )

        response = generate_with_retry(client, CHAT_MODEL, contents, config=search_config)

        apris_reply = response.text.strip()
        
        # Intercept untuk fitur Google Calendar (Check)
        if "<CHECK_CALENDAR/>" in apris_reply:
            import google_calendar
            try:
                cal_data = google_calendar.get_upcoming_events(5)
                apris_reply = apris_reply.replace("<CHECK_CALENDAR/>", f"\n\n{cal_data}")
            except Exception as e:
                apris_reply = apris_reply.replace("<CHECK_CALENDAR/>", f"\n\n_Gagal membaca kalender: {e}_")
                
        # Intercept untuk fitur Google Calendar (Create)
        if "<CREATE_EVENT" in apris_reply:
            import re
            import google_calendar
            match = re.search(r'<CREATE_EVENT title="([^"]+)" start="([^"]+)" end="([^"]+)">(.*?)</CREATE_EVENT>', apris_reply, re.DOTALL)
            if match:
                title, start_t, end_t, desc = match.groups()
                apris_reply = re.sub(r'<CREATE_EVENT.*?</CREATE_EVENT>', '', apris_reply, flags=re.DOTALL).strip()
                try:
                    res = google_calendar.create_event(title, start_t, end_t, desc.strip())
                    apris_reply += f"\n\n✅ *{res}*"
                except Exception as e:
                    apris_reply += f"\n\n_Maaf, gagal membuat jadwal: {e}_"

        # Intercept untuk fitur Gmail (Check)
        if "<CHECK_EMAIL/>" in apris_reply:
            try:
                import google_gmail
                email_data = google_gmail.get_recent_emails(5)
                apris_reply = apris_reply.replace("<CHECK_EMAIL/>", f"\n\n{email_data}")
            except Exception as e:
                apris_reply = apris_reply.replace("<CHECK_EMAIL/>", f"\n\n_Gagal membaca email: {e}_")

        # Intercept untuk fitur Weather
        if "<CHECK_WEATHER" in apris_reply:
            import re
            # Handle baik />  maupun > (tanpa self-closing slash)
            match = re.search(r'<CHECK_WEATHER location="([^"]+)"\s*/?>', apris_reply)
            if match:
                loc = match.group(1)
                try:
                    from features import weather
                    w_data = weather.get_weather_by_city(loc)
                    apris_reply = re.sub(r'<CHECK_WEATHER[^>]*/?>', f"\n\n*{w_data}*", apris_reply).strip()
                except Exception as e:
                    apris_reply = re.sub(r'<CHECK_WEATHER[^>]*/?>', f"\n\n_Gagal mengecek cuaca: {e}_", apris_reply).strip()

        # Intercept untuk fitur Notion
        if "<NOTION_WRITE" in apris_reply:
            import re
            match = re.search(r'<NOTION_WRITE title="([^"]+)">(.*?)</NOTION_WRITE>', apris_reply, re.DOTALL)
            if match:
                title = match.group(1).strip()
                content = match.group(2).strip()
                apris_reply = re.sub(r'<NOTION_WRITE.*?</NOTION_WRITE>', '', apris_reply, flags=re.DOTALL).strip()
                try:
                    from features import notion_api
                    n_res = notion_api.write_to_notion(title, content)
                    apris_reply += f"\n\n✅ *{n_res}*"
                except Exception as e:
                    apris_reply += f"\n\n_Gagal menulis ke Notion: {e}_"

        # Intercept untuk fitur Long-Term Memory
        if "<REMEMBER" in apris_reply or "<FORGET" in apris_reply:
            import re
            try:
                from features import memory

                # Proses SEMUA tag <REMEMBER> (bisa lebih dari satu)
                rem_matches = re.findall(r'<REMEMBER fact="([^"]+)"\s*/?>', apris_reply)
                if rem_matches:
                    apris_reply = re.sub(r'<REMEMBER[^>]*/?>', '', apris_reply).strip()
                    for fact in rem_matches:
                        res = memory.add_memory(fact.strip())
                        apris_reply += f"\n\n🧠 *{res}*"

                # Proses SEMUA tag <FORGET> (bisa lebih dari satu)
                for_matches = re.findall(r'<FORGET fact="([^"]+)"\s*/?>', apris_reply)
                if for_matches:
                    apris_reply = re.sub(r'<FORGET[^>]*/?>', '', apris_reply).strip()
                    for fact in for_matches:
                        res = memory.remove_memory(fact.strip())
                        apris_reply += f"\n\n🧠 *{res}*"

            except Exception:
                pass  # Abaikan jika gagal

        # Intercept untuk fitur Pengingat Obat
        if "<ADD_MEDICINE" in apris_reply:
            import re
            match = re.search(r'<ADD_MEDICINE name="([^"]+)" time="([^"]+)" reason="([^"]+)"\s*/?>', apris_reply)
            if match:
                m_name = match.group(1).strip()
                m_time = match.group(2).strip()
                m_reason = match.group(3).strip()
                apris_reply = re.sub(r'<ADD_MEDICINE[^>]*/>', '', apris_reply).strip()
                try:
                    from features import medical
                    res = medical.add_medicine(m_name, m_time, m_reason)
                    apris_reply += f"\n\n💊 *{res}*"
                except Exception as e:
                    apris_reply += f"\n\n_Gagal menambahkan jadwal obat: {e}_"


        # Intercept untuk fitur Google Docs Writer
        if "<CREATE_DOC" in apris_reply:
            import re
            import google_drive
            
            # Cari tag <CREATE_DOC title="...">...</CREATE_DOC>
            match = re.search(r'<CREATE_DOC title="([^"]+)">(.*?)</CREATE_DOC>', apris_reply, re.DOTALL)
            if match:
                title = match.group(1).strip()
                content = match.group(2).strip()
                
                # Hapus tag dari balasan asli
                apris_reply = re.sub(r'<CREATE_DOC.*?</CREATE_DOC>', '', apris_reply, flags=re.DOTALL).strip()
                
                # Buat dokumen via Google Drive API
                try:
                    doc_url = google_drive.create_google_doc(title, content)
                    if "Error" in doc_url:
                        apris_reply += f"\n\n_Maaf, gagal membuat dokumen: {doc_url}_"
                    else:
                        apris_reply += f"\n\n✅ *Dokumen berhasil dibuat!*\nJudul: {title}\nBuka dokumen: {doc_url}"
                except Exception as e:
                    apris_reply += f"\n\n_Maaf, terjadi kesalahan saat membuat dokumen: {str(e)}_"

        # Simpan ke history sesi
        now_str = datetime.now(TZ).strftime("%H:%M")
        history.append({"role": "user",  "content": user_msg,    "time": now_str})
        history.append({"role": "apris", "content": apris_reply, "time": now_str})

        return jsonify({
            "reply"      : apris_reply,
            "session_id" : session_id,
            "time"       : now_str,
            "model"      : CHAT_MODEL,
            "rag_used"   : bool(rag_context),
        })

    except Exception as e:
        err_str = str(e)
        # Berikan pesan rate limit yang lebih ramah
        if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
            import re
            match = re.search(r'retry in ([\d.]+)s', err_str)
            wait  = int(float(match.group(1))) + 1 if match else 60
            return jsonify({
                "error"     : f"Terlalu banyak permintaan. Harap tunggu {wait} detik.",
                "retry_after": wait,
                "code"      : 429,
            }), 429
        return jsonify({"error": err_str}), 500


@app.route("/history")
def history():
    session_id = request.args.get("session_id", "default")
    with _sessions_lock:
        data = _chat_sessions.get(session_id, {})
        msgs = data.get("messages", []) if isinstance(data, dict) else []
    return jsonify({
        "session_id": session_id,
        "messages"  : msgs,
        "count"     : len(msgs),
    })


@app.route("/clear", methods=["POST"])
def clear():
    data       = request.get_json(silent=True) or {}
    session_id = data.get("session_id", "default")
    with _sessions_lock:
        _chat_sessions[session_id] = {"messages": [], "last_access": datetime.now(TZ)}
    return jsonify({"status": "cleared", "session_id": session_id})


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    is_railway = bool(os.getenv("RAILWAY_ENVIRONMENT"))
    print(f"\n{'='*50}")
    print(f"  APRIS Web Chat Server")
    print(f"  Model   : {CHAT_MODEL}")
    print(f"  Port    : {SERVER_PORT}")
    print(f"  Env     : {'Railway' if is_railway else 'Lokal'}")
    print(f"  RAG DB  : {RAG_DB_PATH}")
    rag_col = get_chroma()
    if rag_col:
        print(f"  RAG     : Terhubung ({rag_col.count()} chunk)")
    else:
        print(f"  RAG     : Tidak tersedia (chat tetap berjalan)")
    if not is_railway:
        print(f"  URL     : http://localhost:{SERVER_PORT}")
    print(f"{'='*50}\n")
    app.run(host="0.0.0.0", port=SERVER_PORT, debug=False)
