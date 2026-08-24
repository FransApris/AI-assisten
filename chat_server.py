"""
chat_server.py — APRIS Web Chat Backend
========================================
Flask server yang menghubungkan Web Chat UI ke Gemini AI (APRIS).

Endpoints:
    POST /auth/request-otp — minta OTP ke email terdaftar
    POST /auth/verify-otp  — verifikasi OTP, dapat auth_token
    POST /chat             — kirim pesan, dapat balasan APRIS
    GET  /history          — ambil riwayat percakapan sesi ini
    POST /clear            — hapus riwayat percakapan
    GET  /status           — cek status server & model
"""

import os
import sys
import json
import uuid
import secrets
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
# Context window: ringkas history jika melebihi batas ini
MAX_HISTORY_MSGS  = int(os.getenv("MAX_HISTORY_MSGS", 40))
SUMMARY_KEEP_MSGS = int(os.getenv("SUMMARY_KEEP_MSGS", 10))  # pesan terbaru yang tetap ada

# RAG Knowledge Base config
_RAG_DEFAULT  = str(Path(__file__).resolve().parent.parent / "rag-knowledge" / "vectorstore")
RAG_DB_PATH       = os.getenv("RAG_DB_PATH", _RAG_DEFAULT)
RAG_COLLECTION    = os.getenv("RAG_COLLECTION", "apris_knowledge")
RAG_TOP_K         = int(os.getenv("RAG_TOP_K", 3))
RAG_ENABLED       = os.getenv("RAG_ENABLED", "true").lower() == "true"

# WhatsApp / Twilio config
TWILIO_ACCOUNT_SID   = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN    = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_WA_FROM       = os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
_WA_ALLOWED_RAW      = os.getenv("WHATSAPP_ALLOWED_NUMBERS", "")
WA_ALLOWED_NUMBERS   = [n.strip() for n in _WA_ALLOWED_RAW.split(",") if n.strip()]

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

§10 PENGELOLAAN TUGAS (GOOGLE TASKS)
Jika pengguna meminta menambahkan tugas/to-do, gunakan:
<ADD_TASK title="Judul tugas" due="YYYY-MM-DD" notes="Catatan opsional"/>
Jika pengguna meminta daftar tugas:
<LIST_TASKS/>
Jika pengguna menyelesaikan tugas:
<COMPLETE_TASK title="kata kunci judul tugas"/>

§11 PENCARIAN KONTAK
Jika pengguna meminta informasi kontak seseorang dari Google Contacts:
<SEARCH_CONTACT name="Nama Orang"/>

§12 BATASAN
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
_sse_clients   = []             # list of SSE queue untuk /events stream
_sse_lock      = threading.Lock()
_scheduler     = None           # APScheduler instance (lazy init)

# ---------------------------------------------------------------------------
# Auth — OTP token store
# ---------------------------------------------------------------------------
_auth_tokens      = {}               # token → {"email": str, "exp": float}
_auth_tokens_lock = threading.Lock()
AUTH_TOKEN_TTL_H  = 24              # Token berlaku 24 jam

# Email yang diizinkan login (dari USER_EMAIL di .env)
_USER_EMAIL_RAW   = os.getenv("USER_EMAIL", "")
ALLOWED_EMAILS    = [e.strip().lower() for e in _USER_EMAIL_RAW.split(",") if e.strip()]

# Jika ALLOWED_EMAILS kosong, autentikasi dinonaktifkan (open access)
AUTH_ENABLED      = bool(ALLOWED_EMAILS)

# Secret internal untuk request antar-endpoint (misal dari /whatsapp ke /chat)
INTERNAL_SECRET   = uuid.uuid4().hex


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


def _summarize_history(history: list, client) -> list:
    """
    Jika history terlalu panjang, ringkas pesan-pesan lama agar tidak overflow token.
    Kembalikan history baru yang lebih pendek.
    """
    if len(history) <= MAX_HISTORY_MSGS:
        return history

    # Ambil pesan lama untuk diringkas, sisakan SUMMARY_KEEP_MSGS terakhir
    old_msgs   = history[:-SUMMARY_KEEP_MSGS]
    recent_msgs = history[-SUMMARY_KEEP_MSGS:]

    old_text = "\n".join(
        f"{m['role'].upper()}: {m['content'][:300]}" for m in old_msgs
    )
    summary_prompt = (
        f"Ringkas percakapan berikut dalam maksimal 5 poin penting "
        f"(bahasa Indonesia, padat):\n\n{old_text}"
    )
    try:
        from google.genai import types
        resp = client.models.generate_content(
            model=CHAT_MODEL,
            contents=summary_prompt
        )
        summary_text = resp.text.strip()
        summary_msg  = {"role": "system", "content": f"[Ringkasan percakapan sebelumnya]:\n{summary_text}", "time": ""}
        print(f"[ContextMgmt] History diringkas ({len(old_msgs)} → 1 summary)", flush=True)
        return [summary_msg] + recent_msgs
    except Exception as e:
        print(f"[ContextMgmt] Gagal ringkas: {e}", flush=True)
        # Fallback: buang pesan lama saja
        return recent_msgs


def _sse_push(event_type: str, message: str):
    """Kirim event ke semua SSE client yang terhubung."""
    import queue
    payload = f"event: {event_type}\ndata: {json.dumps({'message': message})}\n\n"
    with _sse_lock:
        dead = []
        for q in _sse_clients:
            try:
                q.put_nowait(payload)
            except Exception:
                dead.append(q)
        for q in dead:
            _sse_clients.remove(q)


def _get_scheduler():
    """Lazy-init APScheduler. Jalankan sekali saat pertama kali diperlukan."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.jobstores.sqlalchemy  import SQLAlchemyJobStore
        from apscheduler.executors.pool        import ThreadPoolExecutor
        import logging
        logging.getLogger("apscheduler").setLevel(logging.WARNING)

        db_path    = Path(__file__).parent / "reminders.db"
        _scheduler = BackgroundScheduler(
            jobstores ={"default": SQLAlchemyJobStore(url=f"sqlite:///{db_path}")},
            executors ={"default": ThreadPoolExecutor(5)},
            job_defaults={"coalesce": True, "max_instances": 1},
            timezone  = TZ,
        )
        _scheduler.start()
        print("[Scheduler] APScheduler started", flush=True)
    except ImportError:
        print("[Scheduler] APScheduler tidak terinstall — fitur scheduler dinonaktifkan", flush=True)
        _scheduler = None
    return _scheduler


def _init_schedulers():
    """Inisialisasi semua scheduled jobs saat startup."""
    sched = _get_scheduler()
    if sched is None:
        return

    # 1. Daily Brief setiap 07:00 WIB
    try:
        from features.daily_brief import schedule_daily_brief
        schedule_daily_brief(sched)
    except Exception as e:
        print(f"[Scheduler] Daily brief error: {e}")

    # 2. Auto-ingest Google Drive setiap 6 jam
    try:
        from apscheduler.triggers.interval import IntervalTrigger
        def _run_drive_ingest():
            try:
                from features import drive_ingest
                drive_ingest.ingest_drive_files()
            except Exception as e2:
                print(f"[DriveIngest] Error: {e2}")
        sched.add_job(
            _run_drive_ingest,
            trigger=IntervalTrigger(hours=6),
            id="auto_drive_ingest",
            replace_existing=True,
            name="Auto Drive Ingest",
        )
        print("[Scheduler] Drive auto-ingest: setiap 6 jam", flush=True)
    except Exception as e:
        print(f"[Scheduler] Drive ingest error: {e}")

    # 3. Proactive Agent (kalender & obat)
    try:
        from features import proactive
        proactive.set_sse_push(_sse_push)
        proactive.register_jobs(sched)
    except Exception as e:
        print(f"[Scheduler] Proactive error: {e}")


# ---------------------------------------------------------------------------
# Flask App
# ---------------------------------------------------------------------------
app = Flask(__name__, static_folder="web_chat", static_url_path="/static")
CORS(app)

# Inisialisasi scheduler jobs (background, non-blocking)
try:
    _init_schedulers()
except Exception as _sched_err:
    print(f"[Startup] Scheduler init error: {_sched_err}")


@app.route("/")
def index():
    return send_from_directory("web_chat", "index.html")

@app.route("/briefing")
def briefing():
    """Ambil daily briefing — dari cache jika sudah ada, generate jika belum."""
    try:
        from features.daily_brief import get_cached_brief
        data = get_cached_brief()
        return jsonify({"briefing": data["content"], "generated_at": data["generated_at"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/events")
def sse_events():
    """
    SSE stream untuk notifikasi real-time: reminder, kalender, obat.
    Frontend subscribe ke endpoint ini dan terima push event.
    """
    import queue
    from flask import Response, stream_with_context

    q = queue.Queue(maxsize=50)
    with _sse_lock:
        _sse_clients.append(q)

    def generate():
        # Kirim heartbeat agar koneksi tidak timeout
        yield "event: connected\ndata: {}\n\n"
        try:
            while True:
                try:
                    msg = q.get(timeout=30)
                    yield msg
                except queue.Empty:
                    yield ": heartbeat\n\n"   # SSE comment sebagai keepalive
        except GeneratorExit:
            pass
        finally:
            with _sse_lock:
                if q in _sse_clients:
                    _sse_clients.remove(q)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control"  : "no-cache",
            "X-Accel-Buffering": "no",
        }
    )


@app.route("/transcribe", methods=["POST"])
def transcribe_audio():
    """Transkripsi file audio (WAV/MP3/WebM) menggunakan Whisper."""
    if "audio" not in request.files:
        return jsonify({"error": "Tidak ada file audio"}), 400
    audio_file = request.files["audio"]
    if not audio_file.filename:
        return jsonify({"error": "Nama file kosong"}), 400
    try:
        import tempfile, os
        suffix = Path(audio_file.filename).suffix or ".webm"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            audio_file.save(tmp.name)
            tmp_path = tmp.name
        from features import voice
        result = voice.transcribe(tmp_path)
        os.unlink(tmp_path)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/tasks", methods=["GET"])
def get_tasks():
    """Ambil daftar Google Tasks."""
    try:
        from features import tasks
        return jsonify({"tasks": tasks.list_tasks()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/tasks", methods=["POST"])
def add_task():
    """Tambah task baru ke Google Tasks."""
    try:
        data   = request.get_json(silent=True) or {}
        title  = data.get("title", "")
        due    = data.get("due", "")
        notes  = data.get("notes", "")
        if not title:
            return jsonify({"error": "Title wajib diisi"}), 400
        from features import tasks
        return jsonify({"result": tasks.add_task(title, due, notes)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/contacts/search", methods=["GET"])
def search_contact():
    """Cari kontak Google."""
    name = request.args.get("q", "").strip()
    if not name:
        return jsonify({"error": "Parameter 'q' wajib diisi"}), 400
    try:
        from features import contacts
        return jsonify({"result": contacts.search_contact(name)})
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
    try:
        from features import memory_semantic
        sem_count = memory_semantic.count()
    except Exception:
        sem_count = 0
    return jsonify({
        "status"        : "online",
        "model"         : CHAT_MODEL,
        "version"       : "APRIS v3.1",
        "timestamp"     : datetime.now(TZ).isoformat(),
        "sessions"      : len(_chat_sessions),
        "sse_clients"   : len(_sse_clients),
        "env"           : "railway" if os.getenv("RAILWAY_ENVIRONMENT") else "local",
        "rag"           : {"enabled": RAG_ENABLED, "connected": rag_ok, "chunks": rag_count},
        "semantic_memory": {"entries": sem_count},
        "scheduler"     : {"running": _scheduler is not None and _scheduler.running if _scheduler else False},
    })


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _cleanup_auth_tokens():
    """Hapus token yang sudah kedaluwarsa."""
    import time
    now = time.time()
    with _auth_tokens_lock:
        expired = [t for t, d in _auth_tokens.items() if d["exp"] < now]
        for t in expired:
            del _auth_tokens[t]


def _require_auth():
    """
    Validasi token dari header X-Auth-Token.
    Return (email, None) jika valid, atau (None, Response error) jika tidak.
    """
    if not AUTH_ENABLED:
        return "anonymous", None   # Auth dinonaktifkan — semua boleh masuk

    import time
    token = request.headers.get("X-Auth-Token", "").strip()
    if not token:
        return None, (jsonify({"error": "Unauthorized", "code": "NO_TOKEN"}), 401)

    if token == INTERNAL_SECRET:
        return "internal", None

    _cleanup_auth_tokens()
    with _auth_tokens_lock:
        data = _auth_tokens.get(token)

    if not data:
        return None, (jsonify({"error": "Token tidak valid atau kedaluwarsa.", "code": "INVALID_TOKEN"}), 401)

    if time.time() > data["exp"]:
        with _auth_tokens_lock:
            _auth_tokens.pop(token, None)
        return None, (jsonify({"error": "Token kedaluwarsa. Silakan login ulang.", "code": "TOKEN_EXPIRED"}), 401)

    return data["email"], None


# ---------------------------------------------------------------------------
# Auth Endpoints
# ---------------------------------------------------------------------------

@app.route("/auth/request-otp", methods=["POST"])
def auth_request_otp():
    """
    Minta OTP dikirim ke email.
    Body: {"email": "user@example.com"}
    """
    data  = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()

    if not email:
        return jsonify({"success": False, "message": "Email wajib diisi."}), 400

    # Validasi whitelist
    if AUTH_ENABLED and email not in ALLOWED_EMAILS:
        return jsonify({
            "success" : False,
            "message" : "Email tidak terdaftar. Hubungi administrator."
        }), 403

    try:
        from features.otp_email import send_otp
        result = send_otp(email)
        status_code = 200 if result["success"] else 429
        return jsonify(result), status_code
    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/auth/verify-otp", methods=["POST"])
def auth_verify_otp():
    """
    Verifikasi OTP dan dapatkan auth token.
    Body: {"email": "user@example.com", "otp": "123456"}
    Return: {"success": true, "auth_token": "...", "email": "..."}
    """
    import time
    data  = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    otp   = (data.get("otp")   or "").strip()

    if not email or not otp:
        return jsonify({"success": False, "message": "Email dan OTP wajib diisi."}), 400

    try:
        from features.otp_email import verify_otp
        result = verify_otp(email, otp)

        if not result.get("verified"):
            return jsonify({"success": False, "message": result["message"]}), 401

        # Generate auth token
        token = secrets.token_hex(32)
        exp   = time.time() + AUTH_TOKEN_TTL_H * 3600
        with _auth_tokens_lock:
            _auth_tokens[token] = {"email": email, "exp": exp}

        print(f"[Auth] Login berhasil: {email}", flush=True)
        return jsonify({
            "success"    : True,
            "auth_token" : token,
            "email"      : email,
            "expires_in" : AUTH_TOKEN_TTL_H * 3600,
            "message"    : result["message"]
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@app.route("/auth/logout", methods=["POST"])
def auth_logout():
    """Hapus token aktif (logout)."""
    token = request.headers.get("X-Auth-Token", "").strip()
    if token:
        with _auth_tokens_lock:
            _auth_tokens.pop(token, None)
    return jsonify({"success": True, "message": "Logout berhasil."})


@app.route("/auth/status", methods=["GET"])
def auth_status():
    """Cek apakah token masih valid."""
    email, err = _require_auth()
    if err:
        return jsonify({"authenticated": False}), 200
    return jsonify({"authenticated": True, "email": email})


# ---------------------------------------------------------------------------
# WhatsApp Webhook (Green-API)
# ---------------------------------------------------------------------------

def _process_green_api(data):
    """
    Diproses di background agar webhook merespons 200 OK dengan cepat.
    """
    import requests
    from features import green_api
    
    # 1. Ekstrak data dari Green-API webhook
    sender_data = data.get("senderData", {})
    chat_id = sender_data.get("chatId", "")
    if not chat_id:
        return
        
    msg_data = data.get("messageData", {})
    msg_type = msg_data.get("typeMessage", "")
    
    user_msg = ""
    media_data = {}
    
    if msg_type == "textMessage" or msg_type == "extendedTextMessage":
        user_msg = msg_data.get("textMessageData", {}).get("textMessage", "")
    elif msg_type in ["imageMessage", "documentMessage", "audioMessage", "videoMessage"]:
        # Ekstrak caption/text
        user_msg = msg_data.get("fileMessageData", {}).get("caption", "")
        # Download media
        download_url = msg_data.get("fileMessageData", {}).get("downloadUrl", "")
        mime_type = msg_data.get("fileMessageData", {}).get("mimeType", "")
        if download_url:
            try:
                media_data = green_api.media_to_base64(download_url, mime_type)
            except Exception as e:
                print(f"[Green-API] Gagal memproses media: {e}")
                user_msg += f"\n[Sistem: Gagal memproses file/media yang dikirim: {e}]"
    else:
        # Ignore tipe pesan lain
        return

    user_msg = user_msg.strip()
    if not user_msg and not media_data:
        return

    # 2. Siapkan payload untuk endpoint /chat internal kita
    payload = {
        "message": user_msg or "[Kirim Media]",
        "session_id": chat_id
    }
    if media_data:
        payload.update(media_data)

    # 3. Panggil /chat
    port = os.getenv("PORT", "5052")
    try:
        res = requests.post(
            f"http://127.0.0.1:{port}/chat",
            json=payload,
            headers={"X-Auth-Token": INTERNAL_SECRET},
            timeout=120
        )
        res_data = res.json()
        reply_text = res_data.get("reply", "Maaf, terjadi kesalahan saat memproses pesan.")
    except Exception as e:
        print(f"[WhatsApp] Internal request failed: {e}")
        reply_text = "Maaf, server sedang sibuk atau mengalami gangguan."

    # 4. Kirim balasan ke WhatsApp via Green-API
    green_api.send_message(chat_id, reply_text)


@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    """
    Webhook untuk Green-API.
    Menerima notifikasi pesan masuk dan merespons 200 OK.
    """
    data = request.get_json(silent=True) or {}
    
    # Pastikan ini event pesan masuk
    if data.get("typeWebhook") == "incomingMessageReceived":
        # Jalankan pemrosesan di background thread agar server tidak timeout
        threading.Thread(target=_process_green_api, args=(data,), daemon=True).start()
        
    # Selalu return 200 OK secepatnya ke Green-API
    return jsonify({"status": "ok"}), 200


# ---------------------------------------------------------------------------
# Chat Endpoint Utama
# ---------------------------------------------------------------------------


@app.route("/chat", methods=["POST"])
def chat():
    # Verifikasi auth token
    email, auth_err = _require_auth()
    if auth_err:
        return auth_err

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

        # Context Window Management: ringkas jika history terlalu panjang
        history[:] = _summarize_history(history, client)

        # History percakapan sebelumnya
        for msg in history:
            role = "user" if msg["role"] in ("user", "system") else "model"
            contents.append(
                types.Content(
                    role  = role,
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

        # Long-Term Memory Tier-1: tarik fakta eksplisit pengguna
        try:
            from features import memory
            long_term_memory = memory.get_all_memories()
        except Exception:
            long_term_memory = ""

        # Long-Term Memory Tier-2: semantic similarity search
        try:
            from features import memory_semantic
            semantic_memory = memory_semantic.retrieve_memory(user_msg, top_k=3)
        except Exception:
            semantic_memory = ""

        # Medical Module: tarik status obat
        try:
            from features import medical
            medical_context = medical.get_meds_summary()
        except Exception:
            medical_context = ""

        augmented_msg = user_msg
        if rag_context or url_context or long_term_memory or semantic_memory or medical_context:
            augmented_msg += "\n\n"
            if medical_context:
                augmented_msg += f"---\n[Konteks Medis Pengguna]:\n{medical_context}\n---\n\n"
            if long_term_memory:
                augmented_msg += f"---\n[Konteks dari Long-Term Memory]:\n{long_term_memory}\n---\n\n"
            if semantic_memory:
                augmented_msg += f"---\n[Konteks dari Memori Semantik]:\n{semantic_memory}\n---\n\n"
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

        # ================================================================
        # INTERCEPTOR EMAIL — 7 Fitur Lengkap
        # ================================================================
        import re as _re

        # 1. Baca email terbaru (UNREAD)
        if "<CHECK_EMAIL" in apris_reply:
            try:
                import google_gmail
                email_data = google_gmail.get_recent_emails(5)
                apris_reply = _re.sub(r'<CHECK_EMAIL\s*/?>', f"\n\n{email_data}", apris_reply).strip()
            except Exception as e:
                apris_reply = _re.sub(r'<CHECK_EMAIL\s*/?>', f"\n\n_Gagal membaca email: {e}_", apris_reply).strip()

        # 2. Rangkum inbox
        if "<SUMMARIZE_INBOX" in apris_reply:
            try:
                import google_gmail
                summary = google_gmail.summarize_inbox(10)
                apris_reply = _re.sub(r'<SUMMARIZE_INBOX\s*/?>', f"\n\n{summary}", apris_reply).strip()
            except Exception as e:
                apris_reply = _re.sub(r'<SUMMARIZE_INBOX\s*/?>', f"\n\n_Gagal merangkum inbox: {e}_", apris_reply).strip()

        # 3. Cari email
        if "<SEARCH_EMAIL" in apris_reply:
            m = _re.search(r'<SEARCH_EMAIL\s+query="([^"]+)"\s*/?>', apris_reply)
            if m:
                q = m.group(1).strip()
                apris_reply = _re.sub(r'<SEARCH_EMAIL[^>]*/>', '', apris_reply).strip()
                try:
                    import google_gmail
                    result = google_gmail.search_emails(q)
                    apris_reply += f"\n\n🔍 *Hasil Pencarian Email:*\n{result}"
                except Exception as e:
                    apris_reply += f"\n\n_Gagal mencari email: {e}_"

        # 4. Kirim email baru
        if "<SEND_EMAIL" in apris_reply:
            m = _re.search(r'<SEND_EMAIL\s+to="([^"]+)"\s+subject="([^"]+)"\s+body="([^"]+)"\s*/?>', apris_reply, _re.DOTALL)
            if m:
                to_addr, subj, body_txt = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
                apris_reply = _re.sub(r'<SEND_EMAIL[^>]*/>', '', apris_reply).strip()
                try:
                    import google_gmail
                    result = google_gmail.send_email(to_addr, subj, body_txt)
                    apris_reply += f"\n\n✉️ *{result}*"
                except Exception as e:
                    apris_reply += f"\n\n_Gagal mengirim email: {e}_"

        # 5. Balas email
        if "<REPLY_EMAIL" in apris_reply:
            m = _re.search(r'<REPLY_EMAIL\s+id="([^"]+)"\s+body="([^"]+)"\s*/?>', apris_reply, _re.DOTALL)
            if m:
                msg_id, reply_body = m.group(1).strip(), m.group(2).strip()
                apris_reply = _re.sub(r'<REPLY_EMAIL[^>]*/>', '', apris_reply).strip()
                try:
                    import google_gmail
                    result = google_gmail.reply_email(msg_id, reply_body)
                    apris_reply += f"\n\n↩️ *{result}*"
                except Exception as e:
                    apris_reply += f"\n\n_Gagal membalas email: {e}_"

        # 6. Tandai satu email sebagai dibaca
        if "<MARK_READ" in apris_reply:
            m = _re.search(r'<MARK_READ\s+id="([^"]+)"\s*/?>', apris_reply)
            if m:
                msg_id = m.group(1).strip()
                apris_reply = _re.sub(r'<MARK_READ[^>]*/>', '', apris_reply).strip()
                try:
                    import google_gmail
                    result = google_gmail.mark_as_read(msg_id)
                    apris_reply += f"\n\n✅ *{result}*"
                except Exception as e:
                    apris_reply += f"\n\n_Gagal menandai email: {e}_"

        # 7. Tandai semua email sebagai dibaca
        if "<MARK_ALL_READ" in apris_reply:
            try:
                import google_gmail
                result = google_gmail.mark_all_inbox_read()
                apris_reply = _re.sub(r'<MARK_ALL_READ\s*/?>', f"\n\n✅ *{result}*", apris_reply).strip()
            except Exception as e:
                apris_reply = _re.sub(r'<MARK_ALL_READ\s*/?>', f"\n\n_Gagal: {e}_", apris_reply).strip()

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

        # Intercept untuk Peta: Cari Tempat (OSM Nominatim)
        if "<SEARCH_PLACES" in apris_reply:
            import re
            match = re.search(r'<SEARCH_PLACES\s+query="([^"]+)"\s*/?>', apris_reply)
            if match:
                q = match.group(1).strip()
                apris_reply = re.sub(r'<SEARCH_PLACES[^>]*/>', '', apris_reply).strip()
                apris_reply = re.sub(r'<SEARCH_PLACES[^>]*>', '', apris_reply).strip()  # non-self-closing fallback
                try:
                    from features import location
                    res = location.search_places(q)
                    apris_reply += f"\n\n\U0001f5fa\ufe0f *Informasi Lokasi:*\n{res}"
                except Exception as e:
                    apris_reply += f"\n\n_Gagal mencari lokasi: {e}_"

        # Intercept untuk Peta: Hitung Rute (OSRM)
        if "<GET_ROUTE" in apris_reply:
            import re
            match = re.search(r'<GET_ROUTE\s+origin="([^"]+)"\s+destination="([^"]+)"\s*/?>', apris_reply)
            if match:
                ori = match.group(1).strip()
                dest = match.group(2).strip()
                apris_reply = re.sub(r'<GET_ROUTE[^>]*/>', '', apris_reply).strip()
                apris_reply = re.sub(r'<GET_ROUTE[^>]*>', '', apris_reply).strip()  # non-self-closing fallback
                try:
                    from features import location
                    res = location.get_route(ori, dest)
                    apris_reply += f"\n\n\U0001f697 *Panduan Rute:*\n{res}"
                except Exception as e:
                    apris_reply += f"\n\n_Gagal menghitung rute: {e}_"


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

        # Intercept untuk Google Tasks
        if "<ADD_TASK" in apris_reply or "<LIST_TASKS" in apris_reply or "<COMPLETE_TASK" in apris_reply:
            import re
            try:
                from features import tasks as task_module

                # ADD_TASK
                add_matches = re.findall(
                    r'<ADD_TASK title="([^"]+)"(?:\s+due="([^"]*)")?(?:\s+notes="([^"]*)")?\s*/?>', apris_reply
                )
                if add_matches:
                    apris_reply = re.sub(r'<ADD_TASK[^>]*/?>', '', apris_reply).strip()
                    for m in add_matches:
                        res = task_module.add_task(m[0].strip(), m[1].strip(), m[2].strip())
                        apris_reply += f"\n\n{res}"

                # LIST_TASKS
                if "<LIST_TASKS" in apris_reply:
                    apris_reply = re.sub(r'<LIST_TASKS\s*/?>', '', apris_reply).strip()
                    apris_reply += f"\n\n{task_module.list_tasks()}"

                # COMPLETE_TASK
                done_matches = re.findall(r'<COMPLETE_TASK title="([^"]+)"\s*/?>', apris_reply)
                if done_matches:
                    apris_reply = re.sub(r'<COMPLETE_TASK[^>]*/?>', '', apris_reply).strip()
                    for kw in done_matches:
                        res = task_module.complete_task(kw.strip())
                        apris_reply += f"\n\n{res}"

            except Exception as e:
                apris_reply += f"\n\n_Gagal mengakses Google Tasks: {e}_"

        # Intercept untuk Google Contacts
        if "<SEARCH_CONTACT" in apris_reply:
            import re
            matches = re.findall(r'<SEARCH_CONTACT name="([^"]+)"\s*/?>', apris_reply)
            if matches:
                apris_reply = re.sub(r'<SEARCH_CONTACT[^>]*/?>', '', apris_reply).strip()
                try:
                    from features import contacts as contacts_module
                    for name in matches:
                        res = contacts_module.search_contact(name.strip())
                        apris_reply += f"\n\n{res}"
                except Exception as e:
                    apris_reply += f"\n\n_Gagal mencari kontak: {e}_"

        # Simpan ke history sesi
        now_str = datetime.now(TZ).strftime("%H:%M")
        history.append({"role": "user",  "content": user_msg,    "time": now_str})
        history.append({"role": "apris", "content": apris_reply, "time": now_str})

        # Simpan snippet ke Semantic Memory (background, tidak blocking)
        def _store_snippet():
            try:
                from features import memory_semantic
                snippet = f"User: {user_msg[:200]}\nAPRIS: {apris_reply[:200]}"
                memory_semantic.store_memory(snippet, source="conversation")
            except Exception:
                pass
        threading.Thread(target=_store_snippet, daemon=True).start()

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
# WhatsApp Gateway — Twilio Webhook
# ---------------------------------------------------------------------------

def _wa_send_message(to: str, body: str):
    """
    Kirim pesan WhatsApp via Twilio REST API.
    Digunakan untuk notifikasi proaktif / pengiriman awal.
    """
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        print("[WA] Twilio credentials belum dikonfigurasi.", flush=True)
        return
    try:
        from twilio.rest import Client as TwilioClient
        tc = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        tc.messages.create(
            from_=TWILIO_WA_FROM,
            to=f"whatsapp:{to}" if not to.startswith("whatsapp:") else to,
            body=body,
        )
    except Exception as e:
        print(f"[WA] Gagal kirim pesan: {e}", flush=True)




def _run_action_interceptors(apris_reply: str) -> str:
    """
    Jalankan semua action interceptors yang ada di /chat juga untuk /whatsapp.
    Refactored ke fungsi terpisah agar bisa digunakan oleh kedua endpoint.
    """
    import re as _re

    # --- Calendar: Check ---
    if "<CHECK_CALENDAR/>" in apris_reply:
        import google_calendar
        try:
            cal_data = google_calendar.get_upcoming_events(5)
            apris_reply = apris_reply.replace("<CHECK_CALENDAR/>", f"\n\n{cal_data}")
        except Exception as e:
            apris_reply = apris_reply.replace("<CHECK_CALENDAR/>", f"\n\n_Gagal membaca kalender: {e}_")

    # --- Calendar: Create ---
    if "<CREATE_EVENT" in apris_reply:
        import google_calendar
        match = _re.search(r'<CREATE_EVENT title="([^"]+)" start="([^"]+)" end="([^"]+)">(.*?)</CREATE_EVENT>', apris_reply, _re.DOTALL)
        if match:
            title, start_t, end_t, desc = match.groups()
            apris_reply = _re.sub(r'<CREATE_EVENT.*?</CREATE_EVENT>', '', apris_reply, flags=_re.DOTALL).strip()
            try:
                res = google_calendar.create_event(title, start_t, end_t, desc.strip())
                apris_reply += f"\n\n✅ *{res}*"
            except Exception as e:
                apris_reply += f"\n\n_Gagal membuat jadwal: {e}_"

    # --- Email: Check ---
    if "<CHECK_EMAIL" in apris_reply:
        try:
            import google_gmail
            email_data = google_gmail.get_recent_emails(5)
            apris_reply = _re.sub(r'<CHECK_EMAIL\s*/?>', f"\n\n{email_data}", apris_reply).strip()
        except Exception as e:
            apris_reply = _re.sub(r'<CHECK_EMAIL\s*/?>', f"\n\n_Gagal membaca email: {e}_", apris_reply).strip()

    # --- Email: Summarize ---
    if "<SUMMARIZE_INBOX" in apris_reply:
        try:
            import google_gmail
            summary = google_gmail.summarize_inbox(10)
            apris_reply = _re.sub(r'<SUMMARIZE_INBOX\s*/?>', f"\n\n{summary}", apris_reply).strip()
        except Exception as e:
            apris_reply = _re.sub(r'<SUMMARIZE_INBOX\s*/?>', f"\n\n_Gagal merangkum inbox: {e}_", apris_reply).strip()

    # --- Email: Search ---
    if "<SEARCH_EMAIL" in apris_reply:
        m = _re.search(r'<SEARCH_EMAIL\s+query="([^"]+)"\s*/?>', apris_reply)
        if m:
            q = m.group(1).strip()
            apris_reply = _re.sub(r'<SEARCH_EMAIL[^>]*/>', '', apris_reply).strip()
            try:
                import google_gmail
                result = google_gmail.search_emails(q)
                apris_reply += f"\n\n🔍 *Hasil Pencarian Email:*\n{result}"
            except Exception as e:
                apris_reply += f"\n\n_Gagal mencari email: {e}_"

    # --- Email: Send ---
    if "<SEND_EMAIL" in apris_reply:
        m = _re.search(r'<SEND_EMAIL\s+to="([^"]+)"\s+subject="([^"]+)"\s+body="([^"]+)"\s*/?>', apris_reply, _re.DOTALL)
        if m:
            to_addr, subj, body_txt = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
            apris_reply = _re.sub(r'<SEND_EMAIL[^>]*/>', '', apris_reply).strip()
            try:
                import google_gmail
                result = google_gmail.send_email(to_addr, subj, body_txt)
                apris_reply += f"\n\n✉️ *{result}*"
            except Exception as e:
                apris_reply += f"\n\n_Gagal mengirim email: {e}_"

    # --- Email: Reply ---
    if "<REPLY_EMAIL" in apris_reply:
        m = _re.search(r'<REPLY_EMAIL\s+id="([^"]+)"\s+body="([^"]+)"\s*/?>', apris_reply, _re.DOTALL)
        if m:
            msg_id, reply_body = m.group(1).strip(), m.group(2).strip()
            apris_reply = _re.sub(r'<REPLY_EMAIL[^>]*/>', '', apris_reply).strip()
            try:
                import google_gmail
                result = google_gmail.reply_email(msg_id, reply_body)
                apris_reply += f"\n\n↩️ *{result}*"
            except Exception as e:
                apris_reply += f"\n\n_Gagal membalas email: {e}_"

    # --- Email: Mark Read ---
    if "<MARK_READ" in apris_reply:
        m = _re.search(r'<MARK_READ\s+id="([^"]+)"\s*/?>', apris_reply)
        if m:
            msg_id = m.group(1).strip()
            apris_reply = _re.sub(r'<MARK_READ[^>]*/>', '', apris_reply).strip()
            try:
                import google_gmail
                result = google_gmail.mark_as_read(msg_id)
                apris_reply += f"\n\n✅ *{result}*"
            except Exception as e:
                apris_reply += f"\n\n_Gagal menandai email: {e}_"

    # --- Email: Mark All Read ---
    if "<MARK_ALL_READ" in apris_reply:
        try:
            import google_gmail
            result = google_gmail.mark_all_inbox_read()
            apris_reply = _re.sub(r'<MARK_ALL_READ\s*/?>', f"\n\n✅ *{result}*", apris_reply).strip()
        except Exception as e:
            apris_reply = _re.sub(r'<MARK_ALL_READ\s*/?>', f"\n\n_Gagal: {e}_", apris_reply).strip()

    # --- Weather ---
    if "<CHECK_WEATHER" in apris_reply:
        match = _re.search(r'<CHECK_WEATHER location="([^"]+)"\s*/?>', apris_reply)
        if match:
            loc = match.group(1)
            try:
                from features import weather
                w_data = weather.get_weather_by_city(loc)
                apris_reply = _re.sub(r'<CHECK_WEATHER[^>]*/?>', f"\n\n*{w_data}*", apris_reply).strip()
            except Exception as e:
                apris_reply = _re.sub(r'<CHECK_WEATHER[^>]*/?>', f"\n\n_Gagal mengecek cuaca: {e}_", apris_reply).strip()

    # --- Notion ---
    if "<NOTION_WRITE" in apris_reply:
        match = _re.search(r'<NOTION_WRITE title="([^"]+)">(.*?)</NOTION_WRITE>', apris_reply, _re.DOTALL)
        if match:
            title   = match.group(1).strip()
            content = match.group(2).strip()
            apris_reply = _re.sub(r'<NOTION_WRITE.*?</NOTION_WRITE>', '', apris_reply, flags=_re.DOTALL).strip()
            try:
                from features import notion_api
                n_res = notion_api.write_to_notion(title, content)
                apris_reply += f"\n\n✅ *{n_res}*"
            except Exception as e:
                apris_reply += f"\n\n_Gagal menulis ke Notion: {e}_"

    # --- Long-Term Memory ---
    if "<REMEMBER" in apris_reply or "<FORGET" in apris_reply:
        try:
            from features import memory
            rem_matches = _re.findall(r'<REMEMBER fact="([^"]+)"\s*/?>', apris_reply)
            if rem_matches:
                apris_reply = _re.sub(r'<REMEMBER[^>]*/?>', '', apris_reply).strip()
                for fact in rem_matches:
                    res = memory.add_memory(fact.strip())
                    apris_reply += f"\n\n🧠 *{res}*"
            for_matches = _re.findall(r'<FORGET fact="([^"]+)"\s*/?>', apris_reply)
            if for_matches:
                apris_reply = _re.sub(r'<FORGET[^>]*/?>', '', apris_reply).strip()
                for fact in for_matches:
                    res = memory.remove_memory(fact.strip())
                    apris_reply += f"\n\n🧠 *{res}*"
        except Exception:
            pass

    # --- Pengingat Obat ---
    if "<ADD_MEDICINE" in apris_reply:
        match = _re.search(r'<ADD_MEDICINE name="([^"]+)" time="([^"]+)" reason="([^"]+)"\s*/?>', apris_reply)
        if match:
            m_name, m_time, m_reason = match.group(1).strip(), match.group(2).strip(), match.group(3).strip()
            apris_reply = _re.sub(r'<ADD_MEDICINE[^>]*/>', '', apris_reply).strip()
            try:
                from features import medical
                res = medical.add_medicine(m_name, m_time, m_reason)
                apris_reply += f"\n\n💊 *{res}*"
            except Exception as e:
                apris_reply += f"\n\n_Gagal menambahkan jadwal obat: {e}_"

    # --- Peta: Cari Tempat ---
    if "<SEARCH_PLACES" in apris_reply:
        match = _re.search(r'<SEARCH_PLACES\s+query="([^"]+)"\s*/?>', apris_reply)
        if match:
            q = match.group(1).strip()
            apris_reply = _re.sub(r'<SEARCH_PLACES[^>]*/>', '', apris_reply).strip()
            apris_reply = _re.sub(r'<SEARCH_PLACES[^>]*>', '', apris_reply).strip()
            try:
                from features import location
                res = location.search_places(q)
                apris_reply += f"\n\n\U0001f5fa\ufe0f *Informasi Lokasi:*\n{res}"
            except Exception as e:
                apris_reply += f"\n\n_Gagal mencari lokasi: {e}_"

    # --- Peta: Rute ---
    if "<GET_ROUTE" in apris_reply:
        match = _re.search(r'<GET_ROUTE\s+origin="([^"]+)"\s+destination="([^"]+)"\s*/?>', apris_reply)
        if match:
            ori, dest = match.group(1).strip(), match.group(2).strip()
            apris_reply = _re.sub(r'<GET_ROUTE[^>]*/>', '', apris_reply).strip()
            apris_reply = _re.sub(r'<GET_ROUTE[^>]*>', '', apris_reply).strip()
            try:
                from features import location
                res = location.get_route(ori, dest)
                apris_reply += f"\n\n\U0001f697 *Panduan Rute:*\n{res}"
            except Exception as e:
                apris_reply += f"\n\n_Gagal menghitung rute: {e}_"

    # --- Google Drive: Create Doc ---
    if "<CREATE_DOC" in apris_reply:
        import google_drive
        match = _re.search(r'<CREATE_DOC title="([^"]+)">(.*?)</CREATE_DOC>', apris_reply, _re.DOTALL)
        if match:
            title   = match.group(1).strip()
            content = match.group(2).strip()
            apris_reply = _re.sub(r'<CREATE_DOC.*?</CREATE_DOC>', '', apris_reply, flags=_re.DOTALL).strip()
            try:
                doc_url = google_drive.create_google_doc(title, content)
                if "Error" in doc_url:
                    apris_reply += f"\n\n_Maaf, gagal membuat dokumen: {doc_url}_"
                else:
                    apris_reply += f"\n\n✅ *Dokumen berhasil dibuat!*\nJudul: {title}\nBuka: {doc_url}"
            except Exception as e:
                apris_reply += f"\n\n_Gagal membuat dokumen: {e}_"

    # --- Google Tasks ---
    if "<ADD_TASK" in apris_reply or "<LIST_TASKS" in apris_reply or "<COMPLETE_TASK" in apris_reply:
        try:
            from features import tasks as task_module
            add_matches = _re.findall(
                r'<ADD_TASK title="([^"]+)"(?:\s+due="([^"]*)")?(?:\s+notes="([^"]*)")?\s*/?>', apris_reply
            )
            if add_matches:
                apris_reply = _re.sub(r'<ADD_TASK[^>]*/?>', '', apris_reply).strip()
                for m in add_matches:
                    res = task_module.add_task(m[0].strip(), m[1].strip(), m[2].strip())
                    apris_reply += f"\n\n{res}"
            if "<LIST_TASKS" in apris_reply:
                apris_reply = _re.sub(r'<LIST_TASKS\s*/?>', '', apris_reply).strip()
                apris_reply += f"\n\n{task_module.list_tasks()}"
            done_matches = _re.findall(r'<COMPLETE_TASK title="([^"]+)"\s*/?>', apris_reply)
            if done_matches:
                apris_reply = _re.sub(r'<COMPLETE_TASK[^>]*/?>', '', apris_reply).strip()
                for kw in done_matches:
                    res = task_module.complete_task(kw.strip())
                    apris_reply += f"\n\n{res}"
        except Exception as e:
            apris_reply += f"\n\n_Gagal mengakses Google Tasks: {e}_"

    # --- Google Contacts ---
    if "<SEARCH_CONTACT" in apris_reply:
        matches = _re.findall(r'<SEARCH_CONTACT name="([^"]+)"\s*/?>', apris_reply)
        if matches:
            apris_reply = _re.sub(r'<SEARCH_CONTACT[^>]*/?>', '', apris_reply).strip()
            try:
                from features import contacts as contacts_module
                for name in matches:
                    res = contacts_module.search_contact(name.strip())
                    apris_reply += f"\n\n{res}"
            except Exception as e:
                apris_reply += f"\n\n_Gagal mencari kontak: {e}_"

    return apris_reply


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
