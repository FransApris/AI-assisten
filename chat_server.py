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
# Google Credentials Bootstrap (Railway)
# ---------------------------------------------------------------------------
# Di Railway, credentials.json & token Google disimpan sebagai base64 env var.
# Decode dan tulis ke filesystem sebelum modul lain diimport.
def _setup_google_credentials():
    """Decode Google credentials dari env var BASE64 ke file (mode Railway)."""
    import base64
    _BASE = Path(__file__).parent
    mapping = {
        "GOOGLE_CREDENTIALS_B64": _BASE / "credentials.json",
        "GOOGLE_TOKEN_B64"      : _BASE / "token_fad2beth.json",
    }
    for env_key, dest_path in mapping.items():
        b64 = os.getenv(env_key, "")
        if b64 and not dest_path.exists():
            try:
                dest_path.write_bytes(base64.b64decode(b64))
                print(f"[CredSetup] {dest_path.name} ditulis dari env var {env_key}", flush=True)
            except Exception as e:
                print(f"[CredSetup] Gagal decode {env_key}: {e}", flush=True)

_setup_google_credentials()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
# Gemini API Key — support rotasi multi-key (pisahkan dengan koma di .env)
# Contoh: GEMINI_API_KEY=key1,key2,key3
_GEMINI_KEYS_RAW = os.getenv("GEMINI_API_KEY", "")
_GEMINI_KEYS     = [k.strip() for k in _GEMINI_KEYS_RAW.split(",") if k.strip()]
GEMINI_API_KEY   = _GEMINI_KEYS[0] if _GEMINI_KEYS else ""
_gemini_key_idx  = 0
_gemini_key_lock = threading.Lock()

CHAT_MODEL        = os.getenv("GEMINI_CHAT_MODEL", "models/gemini-2.5-flash")
EMBEDDING_MODEL   = os.getenv("GEMINI_EMBEDDING_MODEL", "models/gemini-embedding-001")
# Railway set PORT otomatis; fallback ke 5052 untuk lokal
SERVER_PORT       = int(os.getenv("PORT", os.getenv("CHAT_SERVER_PORT", 5052)))
# TTL sesi (jam) dan batas ukuran file upload (byte)
SESSION_TTL_HOURS = int(os.getenv("SESSION_TTL_HOURS", 24))
MAX_FILE_BYTES    = int(os.getenv("MAX_FILE_BYTES", 10 * 1024 * 1024))  # default 10 MB
# Context window: ringkas history jika melebihi batas ini
MAX_HISTORY_MSGS  = int(os.getenv("MAX_HISTORY_MSGS", 40))
SUMMARY_KEEP_MSGS = int(os.getenv("SUMMARY_KEEP_MSGS", 10))

# RAG Knowledge Base config
_RAG_DEFAULT   = str(Path(__file__).resolve().parent.parent / "rag-knowledge" / "vectorstore")
RAG_DB_PATH    = os.getenv("RAG_DB_PATH", _RAG_DEFAULT)
RAG_COLLECTION = os.getenv("RAG_COLLECTION", "apris_knowledge")
RAG_TOP_K      = int(os.getenv("RAG_TOP_K", 3))
RAG_ENABLED    = os.getenv("RAG_ENABLED", "true").lower() == "true"
# Jika RAG_SERVER_URL diset → panggil rag_server via HTTP (mode Railway)
# Jika kosong → buka ChromaDB lokal langsung (mode lokal)
RAG_SERVER_URL = os.getenv("RAG_SERVER_URL", "").rstrip("/")

# ---------------------------------------------------------------------------
# WhatsApp Multi-User Config
# ---------------------------------------------------------------------------
# WA_WHITELIST    : kosong = semua diizinkan | isi = hanya nomor ini (static)
# WA_INVITE_CODE  : kode undangan untuk registrasi mandiri user baru
# WA_ADMIN_NUMBERS: nomor admin yang bisa tambah/hapus user via WA command
_WA_WHITELIST_RAW  = os.getenv("WA_WHITELIST", "")
WA_WHITELIST       = [n.strip() for n in _WA_WHITELIST_RAW.split(",") if n.strip()]
WA_ADMIN_CHAT_ID   = os.getenv("WA_ADMIN_CHAT_ID", "")
WA_INVITE_CODE     = os.getenv("WA_INVITE_CODE", "").strip()
_WA_ADMIN_RAW      = os.getenv("WA_ADMIN_NUMBERS", os.getenv("WA_OWNER_CHAT_ID", ""))
WA_ADMIN_NUMBERS   = [n.strip() for n in _WA_ADMIN_RAW.split(",") if n.strip()]

# ---------------------------------------------------------------------------
# Module-level defaults — diperlukan agar linter (Pyrefly) tidak error
# Nilai aktual di-set di try/except block di bawah (jika modul tersedia)
# ---------------------------------------------------------------------------
_REGISTRY_OK  : bool = False
_LMEM_OK      : bool = False
_ANALYTICS_OK : bool = False
_lmem         = None   # type: ignore[assignment]  # features.long_memory
_analytics    = None   # type: ignore[assignment]  # features.analytics
_ureg         = None   # type: ignore[assignment]  # features.user_registry

# ---------------------------------------------------------------------------
# Persistent User Registry (SQLite)
# ---------------------------------------------------------------------------
# Approved users disimpan di SQLite agar tetap ada setelah Railway restart.
# Di Railway: /data/users.db (Railway Volume)
# Di lokal  : ./users.db
try:
    from features import user_registry as _ureg
    _ureg.init_db()
    # Seed awal: gabungan DB (persistent) + WA_WHITELIST (static env var)
    _wa_approved_lock  = threading.Lock()
    _wa_approved_users : set = _ureg.load_all_users() | set(WA_WHITELIST)
    print(f"[UserRegistry] {len(_wa_approved_users)} user dimuat dari DB.", flush=True)
    _REGISTRY_OK = True
except Exception as _reg_err:
    print(f"[UserRegistry] Gagal init: {_reg_err} — fallback ke in-memory", flush=True)
    _wa_approved_lock  = threading.Lock()
    _wa_approved_users : set = set(WA_WHITELIST)
    _REGISTRY_OK = False

# ---------------------------------------------------------------------------
# Pending KB Approval Queue
# ---------------------------------------------------------------------------
# Dokumen yang dikirim user, menunggu persetujuan admin sebelum masuk KB global
# Format: {token: {"filename", "bytes", "sender", "sender_name", "ts"}}
import time as _time_mod
_pending_kb      : dict = {}
_pending_kb_lock : threading.Lock = threading.Lock()


def _add_pending_kb(file_bytes: bytes, filename: str, sender_id: str, sender_name: str) -> str:
    """Simpan dokumen ke antrian pending KB. Return token 8-karakter unik."""
    import hashlib
    token = hashlib.md5(file_bytes).hexdigest()[:8].upper()
    with _pending_kb_lock:
        # Bersihkan entri lama (> 24 jam)
        now     = _time_mod.time()
        expired = [k for k, v in _pending_kb.items() if now - v["ts"] > 86400]
        for k in expired:
            del _pending_kb[k]
        _pending_kb[token] = {
            "filename"   : filename,
            "bytes"      : file_bytes,
            "sender"     : sender_id,
            "sender_name": sender_name,
            "ts"         : now,
        }
    return token


def _wa_approve_user(chat_id: str, name: str = "", added_by: str = "invite_code"):
    """Tambahkan nomor ke approved set DAN simpan ke SQLite."""
    with _wa_approved_lock:
        _wa_approved_users.add(chat_id)
    if _REGISTRY_OK:
        _ureg.add_user(chat_id, name=name, added_by=added_by)


def _wa_remove_user(chat_id: str):
    """Hapus nomor dari approved set DAN hapus dari SQLite."""
    with _wa_approved_lock:
        _wa_approved_users.discard(chat_id)
    if _REGISTRY_OK:
        _ureg.remove_user(chat_id)


def _wa_is_admin(chat_id: str) -> bool:
    """Cek apakah chat_id adalah admin."""
    if not WA_ADMIN_NUMBERS:
        return False
    return any(chat_id.startswith(n.lstrip("+")) or n in chat_id for n in WA_ADMIN_NUMBERS)


# ---------------------------------------------------------------------------
# Long-term Memory Init
# ---------------------------------------------------------------------------
try:
    from features import long_memory as _lmem
    _lmem.init_db()
    _LMEM_OK = True
    print("[LongMemory] SQLite memory init OK", flush=True)
except Exception as _lmem_err:
    print(f"[LongMemory] Gagal init: {_lmem_err}", flush=True)
    _LMEM_OK = False
    _lmem    = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Analytics Init
# ---------------------------------------------------------------------------
try:
    from features import analytics as _analytics
    _analytics.init_db()
    _ANALYTICS_OK = True
    print("[Analytics] SQLite analytics init OK", flush=True)
except Exception as _analytics_err:
    print(f"[Analytics] Gagal init: {_analytics_err}", flush=True)
    _ANALYTICS_OK = False
    _analytics    = None  # type: ignore[assignment]


# WHATSAPP_ALLOWED_NUMBERS: daftar nomor yang diizinkan (fallback jika WA_WHITELIST kosong)
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

§12 PENGINGAT & PESAN TERJADWAL
Jika pengguna meminta diingatkan sesuatu pada waktu tertentu ("ingatkan saya jam 3 sore", "tolong ingatkan besok pukul 08:00", dll), buat pengingat menggunakan:
<SCHEDULE_MSG to="{nomor_wa_pengirim}" at="YYYY-MM-DDTHH:MM" message="Teks pengingat yang akan dikirim"/>
PENTING:
- Gunakan nomor WA pengguna saat ini sebagai nilai 'to' (dari session_id)
- Gunakan format tanggal-waktu ISO: YYYY-MM-DDTHH:MM (contoh: 2026-09-01T15:00)
- Pastikan waktu dalam zona WIB (+07:00)
- Jika pengguna tidak menyebut tanggal, asumsikan hari ini
- Jika waktu sudah lewat hari ini, asumsikan besok

Contoh respons:
"Baik, saya akan mengingatkan Anda pukul 15:00."
<SCHEDULE_MSG to="{nomor_wa}" at="2026-08-31T15:00" message="Pengingat: rapat dengan tim pukul 15:00"/>

Untuk melihat daftar pengingat aktif:
<LIST_SCHEDULE_MSG/>

Untuk membatalkan pengingat:
<CANCEL_SCHEDULE_MSG id="rem_xxxxxxxx"/>

§13 BATASAN
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
_session_locks = {}             # session_id → Lock (cegah race condition per sesi)
_sse_clients   = []             # list of SSE queue untuk /events stream
_sse_lock      = threading.Lock()
_scheduler     = None           # APScheduler instance (lazy init)

# ---------------------------------------------------------------------------
# WhatsApp Flood Detector — rate limit per nomor
# ---------------------------------------------------------------------------
# Konfigurasi via .env:
#   WA_FLOOD_MAX_MSG=5      → maks pesan dalam satu window
#   WA_FLOOD_WINDOW_SEC=60  → window waktu (detik)
#   WA_FLOOD_COOLDOWN_SEC=180 → cooldown setelah flood terdeteksi (detik)
WA_FLOOD_MAX_MSG     = int(os.getenv("WA_FLOOD_MAX_MSG",     5))
WA_FLOOD_WINDOW_SEC  = int(os.getenv("WA_FLOOD_WINDOW_SEC",  60))
WA_FLOOD_COOLDOWN_SEC= int(os.getenv("WA_FLOOD_COOLDOWN_SEC",180))

# tracker: chat_id → {"timestamps": [float,...], "cooldown_until": float, "warned": bool}
_wa_flood: dict = {}
_wa_flood_lock  = threading.Lock()

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


def get_client(api_key: str = None):
    """Buat / ambil Gemini client. Jika api_key diberikan, buat client baru."""
    global _genai_client
    if api_key:
        from google import genai
        return genai.Client(api_key=api_key)
    if _genai_client is None:
        from google import genai
        _genai_client = genai.Client(api_key=GEMINI_API_KEY)
    return _genai_client


def _rotate_gemini_key() -> str:
    """
    Rotasi ke API key Gemini berikutnya jika tersedia.
    Dipanggil saat terkena rate limit 429.
    Return: api_key baru, atau string kosong jika hanya 1 key.
    """
    global _gemini_key_idx, _genai_client
    if len(_GEMINI_KEYS) <= 1:
        return ""
    with _gemini_key_lock:
        _gemini_key_idx = (_gemini_key_idx + 1) % len(_GEMINI_KEYS)
        new_key = _GEMINI_KEYS[_gemini_key_idx]
        _genai_client = None   # reset cache agar buat ulang dengan key baru
    print(f"[KeyRotation] Beralih ke API key #{_gemini_key_idx + 1}", flush=True)
    return new_key


# ---------------------------------------------------------------------------
# Webhook Deduplication
# ---------------------------------------------------------------------------
_seen_msg_ids: set = set()
_seen_msg_lock     = threading.Lock()
_SEEN_MSG_MAX      = 500


def _wa_seen_msg(msg_id: str) -> bool:
    """Return True jika msg_id sudah diproses (duplikat). Auto-register jika belum."""
    with _seen_msg_lock:
        if msg_id in _seen_msg_ids:
            return True
        _seen_msg_ids.add(msg_id)
        if len(_seen_msg_ids) > _SEEN_MSG_MAX:
            _seen_msg_ids.discard(next(iter(_seen_msg_ids)))
        return False


def _wa_is_whitelisted(chat_id: str) -> bool:
    """
    Cek apakah chat_id diizinkan mengakses APRIS.

    Logika (prioritas berurutan):
    1. Jika WA_WHITELIST dan WA_INVITE_CODE keduanya kosong → semua diizinkan (open access)
    2. Admin selalu diizinkan
    3. Cek _wa_approved_users (gabungan WA_WHITELIST + registrasi kode undangan)
    """
    # Open access: tidak ada whitelist static dan tidak ada kode undangan
    if not WA_WHITELIST and not WA_INVITE_CODE:
        return True
    # Admin selalu diizinkan
    if _wa_is_admin(chat_id):
        return True
    # Cek approved set (whitelist static + yang sudah daftar via kode)
    with _wa_approved_lock:
        return chat_id in _wa_approved_users


def _wa_notify_admin(subject: str, detail: str):
    """Kirim notifikasi error kritis ke admin via WA. Hanya jika WA_ADMIN_CHAT_ID diset."""
    if not WA_ADMIN_CHAT_ID:
        return
    try:
        from features import green_api as _ga
        now_str = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
        msg = (
            f"\U0001f6a8 *[APRIS ERROR ALERT]*\n"
            f"_{now_str}_\n\n"
            f"*{subject}*\n\n"
            f"{detail[:500]}"
        )
        _ga.send_message(WA_ADMIN_CHAT_ID, msg)
    except Exception as e:
        print(f"[AdminNotif] Gagal kirim notif admin: {e}", flush=True)


def get_chroma():
    """Buat ChromaDB connection baru (thread-safe: tiap request punya client sendiri).
    Hanya dipakai dalam mode lokal (RAG_SERVER_URL tidak diset).
    """
    if RAG_SERVER_URL:  # mode HTTP — tidak perlu ChromaDB lokal
        return None
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

    Mode HTTP  (RAG_SERVER_URL diset): panggil POST /ask ke rag_server.py
    Mode Lokal (RAG_SERVER_URL kosong): buka ChromaDB langsung dari filesystem

    Return: string konteks untuk diinjeksi ke prompt, atau string kosong.
    """
    if not RAG_ENABLED:
        return ""

    # ── Mode HTTP: panggil rag_server via REST API ──────────────────────────
    if RAG_SERVER_URL:
        try:
            import requests as _req
            k = top_k or RAG_TOP_K
            resp = _req.post(
                f"{RAG_SERVER_URL}/ask",
                json={"query": query, "top_k": k},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            # rag_server /ask mengembalikan {"answer": ..., "sources": [...], "context": ...}
            context = data.get("context", "")
            if not context:
                # Fallback: gabungkan dari sources jika context tidak ada
                sources = data.get("sources", [])
                context = "\n\n".join(
                    f"[Sumber: {s.get('source','?')}]\n{s.get('content','')[:700]}"
                    for s in sources
                ) if sources else ""
            print(f"[RAG-HTTP] Query berhasil, konteks {len(context)} chars", flush=True)
            return context
        except Exception as e:
            print(f"[RAG-HTTP] Gagal panggil rag_server ({RAG_SERVER_URL}): {e}", flush=True)
            return ""

    # ── Mode Lokal: ChromaDB langsung ───────────────────────────────────────
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


def _get_session_lock(session_id: str) -> threading.Lock:
    """
    Kembalikan Lock khusus untuk session_id ini.
    Mencegah race condition saat dua request bersamaan masuk ke sesi yang sama
    (misalnya dari WhatsApp dan Web Chat secara simultan).
    """
    with _sessions_lock:
        if session_id not in _session_locks:
            _session_locks[session_id] = threading.Lock()
        return _session_locks[session_id]


def _wa_check_flood(chat_id: str) -> tuple[bool, str, int]:
    """
    Cek apakah nomor ini mengirim terlalu banyak pesan (flood).

    Returns:
        (is_flooded: bool, notif_msg: str, cooldown_remaining: int)
        - is_flooded=True  → blokir pesan, kirim notif_msg ke WA
        - is_flooded=False → lanjut proses normal
    """
    import time
    now = time.time()

    with _wa_flood_lock:
        entry = _wa_flood.setdefault(chat_id, {
            "timestamps"    : [],
            "cooldown_until": 0.0,
            "warned"        : False,
        })

        # 1. Cek apakah masih dalam cooldown
        if now < entry["cooldown_until"]:
            remaining = int(entry["cooldown_until"] - now)
            # Hanya kirim notif sekali per cooldown (bukan setiap pesan)
            if not entry["warned"]:
                entry["warned"] = True
                msg = (
                    f"🛑 *APRIS perlu istirahat sejenak.*\n\n"
                    f"Kamu mengirim terlalu banyak pesan dalam waktu singkat. "
                    f"APRIS butuh waktu memproses satu per satu.\n\n"
                    f"⏳ Silakan tunggu *{remaining} detik* lagi sebelum mengirim pesan berikutnya."
                )
                return True, msg, remaining
            else:
                # Sudah diperingatkan — blokir diam-diam saja
                return True, "", remaining

        # Cooldown sudah selesai — reset warned flag
        entry["warned"] = False

        # 2. Buang timestamp di luar window
        cutoff = now - WA_FLOOD_WINDOW_SEC
        entry["timestamps"] = [t for t in entry["timestamps"] if t > cutoff]

        # 3. Catat timestamp pesan ini
        entry["timestamps"].append(now)
        count = len(entry["timestamps"])

        # 4. Cek apakah melebihi batas
        if count > WA_FLOOD_MAX_MSG:
            entry["cooldown_until"] = now + WA_FLOOD_COOLDOWN_SEC
            entry["warned"]         = True
            cooldown_min = WA_FLOOD_COOLDOWN_SEC // 60
            msg = (
                f"🛑 *Terlalu banyak pesan! APRIS kewalahan.*\n\n"
                f"Kamu telah mengirim *{count} pesan* dalam {WA_FLOOD_WINDOW_SEC} detik terakhir. "
                f"Batas maksimal adalah *{WA_FLOOD_MAX_MSG} pesan* per {WA_FLOOD_WINDOW_SEC} detik.\n\n"
                f"⏳ APRIS akan merespons kembali setelah *{cooldown_min} menit*.\n"
                f"_Pesan-pesan selama cooldown tidak akan diproses._"
            )
            print(f"[FloodDetect] {chat_id} diblokir {WA_FLOOD_COOLDOWN_SEC}s ({count} pesan/{WA_FLOOD_WINDOW_SEC}s)", flush=True)
            return True, msg, WA_FLOOD_COOLDOWN_SEC

    return False, "", 0


def _summarize_history(history: list, client, session_id: str = "") -> list:
    """
    Jika history terlalu panjang, ringkas pesan-pesan lama agar tidak overflow token.
    Kembalikan history baru yang lebih pendek.
    Juga menyimpan ringkasan ke long-term memory (SQLite) agar persistent.
    """
    if len(history) <= MAX_HISTORY_MSGS:
        return history

    # Ambil pesan lama untuk diringkas, sisakan SUMMARY_KEEP_MSGS terakhir
    old_msgs    = history[:-SUMMARY_KEEP_MSGS]
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

        # 💾 Simpan ringkasan ke long-term memory
        if session_id and _LMEM_OK:
            try:
                key_facts = _lmem.extract_key_facts_from_history(old_msgs)
                _lmem.save_memory(session_id, summary_text, key_facts)
                print(f"[LongMemory] Ringkasan disimpan untuk {session_id}", flush=True)
            except Exception as _me:
                print(f"[LongMemory] Gagal simpan ringkasan: {_me}", flush=True)

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


def _run_drive_ingest():
    """
    Fungsi ingest Google Drive ke ChromaDB.
    HARUS berada di level modul (bukan nested) agar APScheduler
    bisa men-serialize referensinya ke SQLite job store.
    """
    try:
        from features import drive_ingest
        drive_ingest.ingest_drive_files()
    except Exception as e:
        print(f"[DriveIngest] Error: {e}", flush=True)


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

        # Gunakan /data/ (Railway Volume) jika tersedia, fallback ke direktori lokal
        _data_dir  = Path("/data") if Path("/data").exists() else Path(__file__).parent
        db_path    = _data_dir / "reminders.db"
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

    # 1. Auto-ingest Google Drive setiap 6 jam
    # PENTING: gunakan referensi string 'chat_server:_run_drive_ingest'
    # atau fungsi level-modul agar APScheduler bisa serialize ke SQLite.
    try:
        from apscheduler.triggers.interval import IntervalTrigger
        sched.add_job(
            _run_drive_ingest,          # fungsi level modul — bisa di-serialize
            trigger=IntervalTrigger(hours=6),
            id="auto_drive_ingest",
            replace_existing=True,
            name="Auto Drive Ingest",
        )
        print("[Scheduler] Drive auto-ingest terdaftar: setiap 6 jam", flush=True)
    except Exception as e:
        print(f"[Scheduler] Gagal daftarkan drive ingest job: {e}", flush=True)

    # Startup ingest — pisahkan dari scheduler agar selalu jalan meski job gagal didaftarkan
    if os.getenv("RAILWAY_ENVIRONMENT"):
        print("[DriveIngest] Railway startup — menjalankan ingest awal dari Google Drive...", flush=True)
        threading.Thread(target=_run_drive_ingest, daemon=True, name="startup-ingest").start()


    # 3. Proactive Agent (kalender & obat)
    try:
        from features import proactive
        from features import green_api as _ga
        proactive.set_sse_push(_sse_push)
        # Kirim notif proaktif juga ke WA pemilik jika WA_OWNER_CHAT_ID diset
        _owner_id = os.getenv("WA_OWNER_CHAT_ID", "")
        if _owner_id:
            proactive.set_wa_push(lambda msg, _cid=_owner_id: _ga.send_message(_cid, msg))
            print(f"[Proactive] WA push aktif → {_owner_id}", flush=True)
        else:
            print("[Proactive] WA_OWNER_CHAT_ID tidak diset — notif hanya via browser", flush=True)
        proactive.register_jobs(sched)
    except Exception as e:
        print(f"[Scheduler] Proactive error: {e}")

    # 4. Salam pagi harian (WA_DAILY_GREETING=true, default: 06:00 WIB)
    if os.getenv("WA_DAILY_GREETING", "false").lower() == "true":
        try:
            from apscheduler.triggers.cron import CronTrigger

            def _send_daily_greeting():
                """Kirim salam pagi ke semua user terdaftar."""
                try:
                    from features import green_api as _ga2
                    from datetime import date
                    today = date.today().strftime("%A, %d %B %Y")
                    # Hari dalam bahasa Indonesia
                    days_id = {
                        "Monday": "Senin", "Tuesday": "Selasa", "Wednesday": "Rabu",
                        "Thursday": "Kamis", "Friday": "Jumat",
                        "Saturday": "Sabtu", "Sunday": "Minggu"
                    }
                    months_id = {
                        "January": "Januari", "February": "Februari", "March": "Maret",
                        "April": "April", "May": "Mei", "June": "Juni",
                        "July": "Juli", "August": "Agustus", "September": "September",
                        "October": "Oktober", "November": "November", "December": "Desember"
                    }
                    for en, id_ in {**days_id, **months_id}.items():
                        today = today.replace(en, id_)

                    greeting = (
                        f"Selamat pagi! ☀️\n\n"
                        f"Hari ini *{today}*.\n\n"
                        f"Saya *APRIS* siap membantu Anda hari ini. "
                        f"Ada yang bisa saya bantu? 😊"
                    )
                    # Kirim ke semua user terdaftar
                    if _REGISTRY_OK:
                        targets = [u["chat_id"] for u in _ureg.list_users()]
                    else:
                        with _wa_approved_lock:
                            targets = list(_wa_approved_users)

                    sent = 0
                    for cid in targets:
                        try:
                            _ga2.send_message(cid, greeting)
                            sent += 1
                        except Exception:
                            pass
                    print(f"[DailyGreeting] Salam pagi dikirim ke {sent}/{len(targets)} user", flush=True)
                except Exception as eg:
                    print(f"[DailyGreeting] Error: {eg}", flush=True)

            # Jadwal: setiap hari pukul 06:00 WIB (UTC+7 = 23:00 UTC hari sebelumnya)
            greeting_hour = int(os.getenv("WA_GREETING_HOUR", "6"))    # jam WIB
            sched.add_job(
                _send_daily_greeting,
                trigger=CronTrigger(hour=greeting_hour, minute=0, timezone=TZ),
                id="daily_greeting",
                replace_existing=True,
                name="Daily Morning Greeting",
            )
            print(f"[Scheduler] Salam pagi harian: pukul {greeting_hour:02d}:00 WIB", flush=True)
        except Exception as e:
            print(f"[Scheduler] Daily greeting error: {e}", flush=True)



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
        data = request.get_json(silent=True) or {}
        name = (data.get("name") or "").strip()
        time = (data.get("time") or "").strip()
        if not name or not time:
            return jsonify({"error": "'name' dan 'time' wajib diisi."}), 400
        from features import medical
        res = medical.mark_taken(name, time)
        return jsonify({"success": res})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/health")
@app.route("/status")
def status():
    # Cek status RAG — bedakan mode HTTP vs lokal
    rag_mode    = "http" if RAG_SERVER_URL else "local"
    rag_ok      = False
    rag_count   = 0

    if RAG_SERVER_URL:
        # Mode HTTP: ping rag_server
        try:
            import requests as _req
            r = _req.get(f"{RAG_SERVER_URL}/status", timeout=5)
            if r.status_code == 200:
                rag_ok    = True
                rag_count = r.json().get("total_chunks", r.json().get("chunks", 0))
        except Exception:
            rag_ok = False
    else:
        # Mode lokal: cek ChromaDB langsung
        col = get_chroma()
        rag_ok = col is not None
        if rag_ok:
            try: rag_count = col.count()
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
        "rag"           : {
            "enabled"  : RAG_ENABLED,
            "mode"     : rag_mode,
            "connected": rag_ok,
            "chunks"   : rag_count,
            "server"   : RAG_SERVER_URL or None,
        },
        "semantic_memory": {"entries": sem_count},
        "scheduler"     : {"running": _scheduler is not None and _scheduler.running if _scheduler else False},
        "users"         : {
            "registry_ok" : _REGISTRY_OK,
            "db_path"     : _ureg.USERS_DB_PATH if _REGISTRY_OK else None,
            "total"       : _ureg.user_count() if _REGISTRY_OK else len(_wa_approved_users),
            "volume_ok"   : os.path.exists("/data") if os.getenv("RAILWAY_ENVIRONMENT") else None,
        },
        "invite_mode"   : bool(WA_INVITE_CODE),
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
    # Jangan kembalikan "anonymous" sebagai email nyata
    safe_email = email if email and email != "anonymous" else None
    return jsonify({"authenticated": True, "email": safe_email})


# ---------------------------------------------------------------------------
# WhatsApp Webhook (Green-API)
# ---------------------------------------------------------------------------

def _process_green_api(data):
    """
    Diproses di background agar webhook merespons 200 OK dengan cepat.
    """
    import requests, time, threading as _th
    from features import green_api
    from features import messages as _msg

    # 1. Ekstrak data dari Green-API webhook
    sender_data = data.get("senderData", {})
    chat_id     = sender_data.get("chatId", "")
    msg_id      = data.get("idMessage", "") or data.get("messageData", {}).get("idMessage", "")

    if not chat_id:
        return

    # 🛠️ Maintenance Mode — tolak semua pesan dengan notif
    if _msg.MAINTENANCE_MODE:
        green_api.send_message(chat_id, _msg.MAINTENANCE_TEXT)
        return

    # 🔒 Sistem Akses: Whitelist & Invite-Only
    sender_name = sender_data.get("senderName", "")
    if not _wa_is_whitelisted(chat_id):
        # Cek apakah ini pesan registrasi (kode undangan)
        raw_text = (
            data.get("messageData", {}).get("textMessageData", {}).get("textMessage", "")
            or data.get("messageData", {}).get("extendedTextMessageData", {}).get("text", "")
        ).strip()

        if WA_INVITE_CODE and raw_text == WA_INVITE_CODE:
            # Kode benar → daftarkan nomor ini ke memory + SQLite
            _wa_approve_user(chat_id, name=sender_name, added_by="invite_code")
            print(f"[Invite] Nomor terdaftar via kode: {chat_id} ({sender_name})", flush=True)
            green_api.send_message(chat_id, _msg.get_registered_message(sender_name))
            # Notif admin jika ada
            if WA_ADMIN_CHAT_ID:
                green_api.send_message(WA_ADMIN_CHAT_ID,
                    f"[APRIS] User baru terdaftar:\n{sender_name} ({chat_id})")
        else:
            # Belum terdaftar / kode salah → selalu tampilkan prompt undangan
            # (jangan tampilkan 'kode salah' karena user mungkin belum tahu harus kirim kode)
            print(f"[Whitelist] Nomor belum terdaftar: {chat_id}", flush=True)
            green_api.send_message(chat_id, _msg.get_invite_prompt())
        return

    # 👮 Perintah Admin (hanya untuk admin yang terdaftar di WA_ADMIN_NUMBERS)
    if _wa_is_admin(chat_id):
        raw_admin_msg = (
            data.get("messageData", {}).get("textMessageData", {}).get("textMessage", "")
            or data.get("messageData", {}).get("extendedTextMessageData", {}).get("text", "")
        ).strip()

        if raw_admin_msg.startswith("/adduser "):
            target = raw_admin_msg[9:].strip().replace(" ", "").replace("+", "")
            target_id = target + "@c.us" if "@" not in target else target
            _wa_approve_user(target_id, name="", added_by=chat_id)
            total = _ureg.user_count() if _REGISTRY_OK else len(_wa_approved_users)
            green_api.send_message(chat_id, f"User {target_id} berhasil ditambahkan. Total: {total} user.")
            return
        elif raw_admin_msg.startswith("/removeuser "):
            target = raw_admin_msg[12:].strip().replace(" ", "").replace("+", "")
            target_id = target + "@c.us" if "@" not in target else target
            _wa_remove_user(target_id)
            green_api.send_message(chat_id, f"User {target_id} dihapus dari daftar.")
            return
        elif raw_admin_msg == "/listusers":
            if _REGISTRY_OK:
                users = _ureg.list_users()
                if users:
                    lines = [f"*Daftar User ({len(users)}):*"]
                    for u in users:
                        nm   = u.get('name') or '-'
                        reg  = (u.get('registered') or '')[:10]
                        lines.append(f"- {nm} | {reg}")
                    msg = "\n".join(lines)
                else:
                    msg = "Belum ada user terdaftar."
            else:
                with _wa_approved_lock:
                    users_list = list(_wa_approved_users)
                msg = f"*Daftar User ({len(users_list)}):*\n" + "\n".join(users_list) if users_list else "Belum ada user."
            green_api.send_message(chat_id, msg)
            return
        elif raw_admin_msg == "/usercount":
            total = _ureg.user_count() if _REGISTRY_OK else len(_wa_approved_users)
            green_api.send_message(chat_id, f"Total user terdaftar: *{total}*")
            return
        elif raw_admin_msg.startswith("/approve"):
            # /approve <TOKEN> — setujui dokumen masuk ke KB
            parts = raw_admin_msg.split()
            if len(parts) < 2:
                # Tampilkan daftar pending
                with _pending_kb_lock:
                    pending_list = list(_pending_kb.items())
                if not pending_list:
                    green_api.send_message(chat_id, "Tidak ada dokumen yang menunggu persetujuan.")
                else:
                    lines = ["*Dokumen menunggu persetujuan:*"]
                    for tok, doc in pending_list:
                        lines.append(f"• `{tok}` — {doc['filename']} dari {doc['sender_name']}")
                    lines.append("\nGunakan: `/approve <TOKEN>`")
                    green_api.send_message(chat_id, "\n".join(lines))
            else:
                token = parts[1].upper()
                with _pending_kb_lock:
                    doc = _pending_kb.get(token)
                if not doc:
                    green_api.send_message(chat_id, f"Token `{token}` tidak ditemukan atau sudah kadaluarsa.")
                else:
                    green_api.send_message(chat_id,
                        f"Menambahkan *{doc['filename']}* ke knowledge base...")
                    def _do_approve(_doc=doc, _token=token):
                        try:
                            from features import drive_ingest as _di
                            res = _di.ingest_file_bytes(_doc["bytes"], _doc["filename"])
                            green_api.send_message(chat_id, res["message"])
                            # Notif ke pengirim asli
                            if _doc["sender"] != chat_id:
                                green_api.send_message(_doc["sender"],
                                    f"✅ Dokumen *{_doc['filename']}* Anda telah disetujui admin "
                                    f"dan kini menjadi bagian dari knowledge base APRIS!")
                            # Hapus dari antrian
                            with _pending_kb_lock:
                                _pending_kb.pop(_token, None)
                            if _ANALYTICS_OK:
                                _analytics.log_event(chat_id, feature="kb_update", name="admin_approve")
                        except Exception as e:
                            green_api.send_message(chat_id, f"Gagal approve: {e}")
                    threading.Thread(target=_do_approve, daemon=True).start()
            return
        elif raw_admin_msg == "/pending":
            # Alias untuk /approve tanpa token
            with _pending_kb_lock:
                pending_list = list(_pending_kb.items())
            if not pending_list:
                green_api.send_message(chat_id, "Tidak ada dokumen yang menunggu persetujuan.")
            else:
                lines = ["*Dokumen menunggu persetujuan:*"]
                for tok, doc in pending_list:
                    lines.append(f"• `/approve {tok}` — {doc['filename']} dari {doc['sender_name']}")
                green_api.send_message(chat_id, "\n".join(lines))
            return
        elif raw_admin_msg == "/kb-status":
            green_api.send_message(chat_id, "Mengambil status Knowledge Base...")
            def _get_status():
                try:
                    from features import drive_ingest as _di
                    status = _di.get_kb_status()
                    msg = _di.format_kb_status_message(status)
                    green_api.send_message(chat_id, msg)
                except Exception as e:
                    green_api.send_message(chat_id, f"❌ Gagal: {e}")
            threading.Thread(target=_get_status, daemon=True).start()
            return
        elif raw_admin_msg == "/ingest-kb":
            green_api.send_message(chat_id, "Memulai proses ingest ulang Google Drive...")
            def _do_ingest():
                try:
                    from features import drive_ingest as _di
                    added = _di.ingest_drive_files()
                    green_api.send_message(chat_id, f"✅ Proses ingest selesai! {added} chunk ditambahkan.")
                except Exception as e:
                    green_api.send_message(chat_id, f"❌ Ingest gagal: {e}")
            threading.Thread(target=_do_ingest, daemon=True).start()
            return
        elif raw_admin_msg in ("/maintenance on", "/maintenance off"):
            import features.messages as _msg_mod
            mode_on = raw_admin_msg.endswith("on")
            os.environ["WA_MAINTENANCE_MODE"] = "true" if mode_on else "false"
            _msg_mod.MAINTENANCE_MODE = mode_on
            status = "AKTIF — APRIS tidak akan menjawab user sementara." if mode_on else "NONAKTIF — APRIS kembali normal."
            green_api.send_message(chat_id, f"Mode pemeliharaan: *{status}*")
            return


    # 🔁 Dedup: abaikan webhook duplikat dari Green-API
    if msg_id and _wa_seen_msg(msg_id):
        print(f"[Dedup] Pesan duplikat diabaikan: {msg_id}", flush=True)
        return

    # 🛡️ Flood guard: cek apakah nomor ini mengirim terlalu banyak pesan
    is_flooded, flood_msg, _ = _wa_check_flood(chat_id)
    if is_flooded:
        if flood_msg:
            green_api.send_message(chat_id, flood_msg)
        return

    msg_data = data.get("messageData", {})
    msg_type = msg_data.get("typeMessage", "")

    user_msg   = ""
    media_data = {}

    if msg_type == "textMessage":
        user_msg = msg_data.get("textMessageData", {}).get("textMessage", "")
    elif msg_type == "extendedTextMessage":
        user_msg = msg_data.get("extendedTextMessageData", {}).get("text", "")
    elif msg_type == "documentMessage":
        # 📄 Dokumen — PDF dengan auto-fallback OCR untuk scan/gambar
        user_msg     = msg_data.get("fileMessageData", {}).get("caption", "")
        download_url = msg_data.get("fileMessageData", {}).get("downloadUrl", "")
        filename     = msg_data.get("fileMessageData", {}).get("fileName", "document")
        mime_type    = msg_data.get("fileMessageData", {}).get("mimeType", "")
        fname_lower  = filename.lower()

        if download_url:
            try:
                import requests as _req, io
                resp = _req.get(download_url, timeout=60)
                resp.raise_for_status()
                file_bytes = resp.content

                if fname_lower.endswith(".pdf") or "pdf" in mime_type:
                    # 🔍 PDF — coba ekstrak teks, fallback ke OCR jika scan
                    from features import pdf_ocr as _pocr
                    # Beri tahu user bahwa sedang diproses
                    green_api.send_message(chat_id,
                        f"📄 Memproses *{filename}*... _(mohon tunggu sebentar)_")

                    # Dapatkan Gemini client untuk OCR
                    _ocr_client = None
                    try:
                        from google import genai as _genai
                        _ocr_client = _genai.Client(api_key=GEMINI_API_KEY)
                    except Exception:
                        pass

                    result = _pocr.extract_pdf_text(
                        file_bytes, filename=filename, gemini_client=_ocr_client
                    )

                    if result["error"]:
                        user_msg += f"\n[Sistem: {result['error']}]"
                    else:
                        method_label = {
                            "pypdf2"    : "teks digital",
                            "gemini_ocr": "OCR (scan dikenali AI ✨)",
                        }.get(result["method"], result["method"])
                        trunc_note = " _(dipotong, dokumen terlalu panjang)_" if result["truncated"] else ""
                        user_msg = (
                            f"[PDF: *{filename}* | {result['pages']} hlm | {method_label}{trunc_note}]\n\n"
                            f"{user_msg + chr(10) if user_msg else ''}"
                            f"Isi dokumen:\n```\n{result['text']}\n```"
                        )
                        print(f"[DocPDF] '{filename}': {result['chars']} char via {result['method']}", flush=True)

                        # 📬 Simpan ke antrian pending KB + notifikasi admin
                        if WA_ADMIN_CHAT_ID and not _wa_is_admin(chat_id):
                            _sender_name_local = sender_name or chat_id
                            token = _add_pending_kb(
                                file_bytes, filename,
                                sender_id=chat_id, sender_name=_sender_name_local
                            )
                            green_api.send_message(WA_ADMIN_CHAT_ID,
                                f"📄 *PDF baru dari user:*\n"
                                f"👤 {_sender_name_local} ({chat_id})\n"
                                f"📎 {filename} ({result['pages']} hlm, {result['method']})\n\n"
                                f"Ketik `/approve {token}` untuk menambahkan ke knowledge base."
                            )
                            print(f"[PendingKB] '{filename}' dari {chat_id}, token={token}", flush=True)


                elif fname_lower.endswith((".txt", ".md", ".csv", ".json")):
                    text_content = file_bytes.decode("utf-8", errors="ignore")
                    if len(text_content) > 12000:
                        text_content = text_content[:12000] + "\n\n_(dipotong)_"
                    user_msg = (
                        f"[Dokumen teks diterima: *{filename}*]\n\n"
                        f"{user_msg + chr(10) if user_msg else ''}"
                        f"Isi dokumen:\n```\n{text_content}\n```"
                    )
                else:
                    media_data = green_api.media_to_base64(download_url, mime_type)
                    if not user_msg:
                        user_msg = f"[File diterima: {filename}]"

            except Exception as e:
                print(f"[DocHandler] Gagal memproses '{filename}': {e}", flush=True)
                user_msg += f"\n[Sistem: Gagal membaca dokumen '{filename}': {e}]"


    elif msg_type in ["imageMessage", "audioMessage", "videoMessage"]:
        # 🖼️ Gambar/audio/video → base64 ke Gemini Vision
        user_msg     = msg_data.get("fileMessageData", {}).get("caption", "")
        download_url = msg_data.get("fileMessageData", {}).get("downloadUrl", "")
        mime_type    = msg_data.get("fileMessageData", {}).get("mimeType", "")
        if download_url:
            try:
                media_data = green_api.media_to_base64(download_url, mime_type)
            except Exception as e:
                print(f"[Green-API] Gagal memproses media: {e}")
                user_msg += f"\n[Sistem: Gagal memproses file/media yang dikirim: {e}]"

    # 🔘 Interactive Button Reply — user tap tombol
    elif msg_type == "interactiveMessageReplyButton":
        btn_data  = msg_data.get("interactiveMessageReplyData", {})
        btn_id    = btn_data.get("buttonId", "")
        btn_title = btn_data.get("buttonTitle", btn_data.get("selectedButtonId", btn_id))
        user_msg  = f"[USER_SELECTED: {btn_id}] {btn_title}".strip()
        print(f"[Interactive] Button tap: id={btn_id}, title={btn_title}")

    # 📋 Interactive List Reply — user pilih item dari list
    elif msg_type == "interactiveMessageReplyList":
        list_data  = msg_data.get("interactiveMessageReplyData", {})
        row_id     = list_data.get("rowId", list_data.get("selectedRowId", ""))
        row_title  = list_data.get("title", list_data.get("rowTitle", row_id))
        user_msg   = f"[USER_SELECTED: {row_id}] {row_title}".strip()
        print(f"[Interactive] List select: id={row_id}, title={row_title}")

    else:
        return  # Ignore tipe lain


    user_msg = user_msg.strip()
    if not user_msg and not media_data:
        return

    # 🔍 Reverse lookup — cari nama pengirim di Google Contacts
    # Dijalankan di thread terpisah agar tidak blocking ack message
    sender_name = sender_data.get("senderName", "") or ""
    _lookup_done = [False]
    _lookup_result = [sender_name]

    def _do_reverse_lookup():
        try:
            from features import contacts as _ct
            result = _ct.reverse_lookup_wa(chat_id)
            if result:
                _lookup_result[0] = result
        except Exception:
            pass
        finally:
            _lookup_done[0] = True

    if not sender_name:
        import threading as _lt
        _lt_thread = _lt.Thread(target=_do_reverse_lookup, daemon=True)
        _lt_thread.start()
        _lt_thread.join(timeout=2.0)   # tunggu max 2 detik (cache hit instan)
        sender_name = _lookup_result[0]

    # 👋 Welcome message — kirim sekali untuk pengguna baru
    # Cek via session (in-memory) DAN user_registry (persistent)
    try:
        history   = get_or_create_session(chat_id)
        is_new_user = (len(history) == 0)
        # Jika session kosong tapi user sudah ada di registry lama,
        # cek flag 'welcomed' di DB agar tidak kirim ulang saat Railway restart
        if is_new_user and _REGISTRY_OK:
            users_db = {u["chat_id"]: u for u in _ureg.list_users()}
            if chat_id in users_db and users_db[chat_id].get("added_by") == "welcomed":
                is_new_user = False   # sudah pernah disambut sebelumnya
    except Exception:
        is_new_user = False

    if is_new_user:
        welcome_mode = os.getenv("WA_WELCOME_MODE", "id").lower()
        if welcome_mode == "interactive":
            try:
                interactive_payload = _msg.get_welcome_interactive()
                green_api.send_interactive_from_cloud_api(chat_id, interactive_payload)
            except Exception as we:
                print(f"[Welcome] Gagal kirim interactive, fallback teks: {we}")
                green_api.send_message(chat_id, _msg.get_welcome_message())
        else:
            welcome_msg = _msg.get_welcome_message()
            if welcome_msg:
                try:
                    green_api.send_message(chat_id, welcome_msg)
                except Exception:
                    pass
        # Tandai sudah disambut di DB agar tidak kirim ulang setelah Railway restart
        if _REGISTRY_OK:
            _ureg.add_user(chat_id, name=sender_name, added_by="welcomed")

    # 📋 Cheatsheet — deteksi kata kunci sebelum dikirim ke AI
    # Hemat token: tidak perlu memanggil Gemini untuk perintah ini
    if _msg.is_cheatsheet_request(user_msg):
        is_admin_caller = _wa_is_admin(chat_id)
        print(f"[Cheatsheet] Dikirim ke {chat_id} (admin={is_admin_caller})", flush=True)
        green_api.send_message(chat_id, _msg.get_full_cheatsheet(is_admin=is_admin_caller))
        if _ANALYTICS_OK:
            threading.Thread(
                target=_analytics.log_event,
                args=(chat_id,), kwargs={"feature": "cheatsheet", "name": sender_name},
                daemon=True
            ).start()
        return

    # 🗑️ Hapus riwayat — clear session (dan opsional clear memori)
    _RESET_TRIGGERS = {"hapus riwayat", "reset chat", "mulai baru", "clear chat", "hapus memori"}
    _RESET_MEM_TRIGGERS = {"hapus memori", "reset memori", "lupa semua"}
    user_msg_lower = user_msg.strip().lower()

    if user_msg_lower in _RESET_TRIGGERS:
        clear_memory = user_msg_lower in _RESET_MEM_TRIGGERS
        # Simpan dulu ke long-term memory sebelum clear (kecuali jika "hapus memori")
        if _LMEM_OK and not clear_memory:
            try:
                hist_now = get_or_create_session(chat_id)
                if hist_now:
                    kf = _lmem.extract_key_facts_from_history(hist_now)
                    snippet = "\n".join(
                        f"{m['role'].upper()}: {m['content'][:200]}"
                        for m in hist_now[-8:] if m.get("role") in ("user","assistant")
                    )
                    _lmem.save_memory(chat_id, snippet, kf)
            except Exception:
                pass
        # Clear session in-memory
        with _sessions_lock:
            _chat_sessions[chat_id] = {"messages": [], "last_access": datetime.now(TZ)}
        # Hapus memori jika diminta
        if clear_memory and _LMEM_OK:
            _lmem.clear_memory(chat_id)
            reply = "Riwayat percakapan dan memori APRIS untuk Anda sudah dihapus. 🗑️\nSemua percakapan dimulai dari awal."
        else:
            reply = "Riwayat percakapan berhasil dihapus. 🗑️\nKita mulai percakapan baru!\n\n_Catatan: APRIS masih mengingat fakta penting dari percakapan sebelumnya. Ketik *hapus memori* untuk reset total._"
        green_api.send_message(chat_id, reply)
        return

    # ✅ Acknowledgment segera
    try:
        green_api.send_message(chat_id, _msg.get_ack_message(sender_name))
    except Exception:
        pass

    # 2. Payload untuk /chat
    # Sertakan nama pengirim di pesan agar Gemini tahu konteks
    if sender_name:

        payload = {
            "message"    : user_msg or "[Kirim Media]",
            "session_id" : chat_id,
            "sender_name": sender_name,
        }
    else:
        payload = {"message": user_msg or "[Kirim Media]", "session_id": chat_id}
    if media_data:
        payload.update(media_data)


    # 3. Panggil /chat — timeout 60s, progress message tiap 30s
    port = os.getenv("PORT", "5052")
    reply_text     = ""
    _done          = [False]

    def _send_progress():
        elapsed = 0
        while not _done[0]:
            time.sleep(30)
            if _done[0]:
                break
            elapsed += 30
            try:
                green_api.send_message(chat_id, _msg.get_progress_message())
            except Exception:
                pass

    _th.Thread(target=_send_progress, daemon=True).start()

    try:
        res      = requests.post(
            f"http://127.0.0.1:{port}/chat",
            json    = payload,
            headers = {"X-Auth-Token": INTERNAL_SECRET},
            timeout = 60,   # dikurangi dari 120s → 60s
        )
        res_data = res.json()

        if res.status_code == 429:
            retry   = res_data.get("retry_after", 60)
            # Coba rotasi Gemini API key dan ulangi request
            new_key = _rotate_gemini_key()
            if new_key:
                print("[KeyRotation] Rotasi karena 429, coba ulang", flush=True)
                try:
                    res2 = requests.post(
                        f"http://127.0.0.1:{port}/chat",
                        json    = payload,
                        headers = {"X-Auth-Token": INTERNAL_SECRET},
                        timeout = 60,
                    )
                    if res2.status_code == 200:
                        reply_text = res2.json().get("reply", "")
                except Exception:
                    pass
            if not reply_text:
                reply_text = (
                    f"⏱️ *APRIS sedang kelebihan beban.*\n\n"
                    f"Kirim ulang dalam *{retry} detik*.\n\n"
                    f"_Pesan: \"{user_msg[:80]}{'...' if len(user_msg)>80 else ''}\"_"
                )

        elif res.status_code == 500:
            err        = res_data.get("error", "Unknown error")
            reply_text = (
                f"❌ *APRIS mengalami kendala internal.*\n\n"
                f"_{err[:200]}_\n\nCoba kirim ulang. Jika berulang, hubungi admin."
            )
            _wa_notify_admin("Error 500 pada /chat",
                f"chat_id: {chat_id}\npesan: {user_msg[:200]}\nerror: {err[:300]}")

        elif res.status_code == 503:
            err = res_data.get("error", "Server sibuk saat ini.")
            reply_text = f"⏳ *Server AI Sedang Penuh*\n\n_{err}_\n\nMohon bersabar dan coba kirim ulang pesan Anda dalam 1-2 menit."


        elif res.status_code == 413:
            mb         = int(os.getenv("MAX_FILE_BYTES", 10*1024*1024)) // (1024*1024)
            reply_text = f"📦 *File terlalu besar.* Batas {mb} MB. Kirim file lebih kecil."

        else:
            reply_text = res_data.get("reply", "Maaf, terjadi kesalahan saat memproses pesan.")

    except requests.exceptions.Timeout:
        print("[WhatsApp] Timeout /chat setelah 60s", flush=True)
        reply_text = (
            "⏳ *APRIS membutuhkan waktu terlalu lama.*\n\n"
            "Coba lagi dengan pertanyaan lebih singkat."
        )
        _wa_notify_admin("Timeout /chat (>60s)", f"chat_id: {chat_id}\npesan: {user_msg[:200]}")

    except requests.exceptions.ConnectionError:
        print("[WhatsApp] Tidak bisa konek ke /chat", flush=True)
        reply_text = "🔌 *APRIS tidak merespons.* Coba lagi dalam 1–2 menit."
        _wa_notify_admin("Connection Error ke /chat", f"chat_id: {chat_id}\npesan: {user_msg[:200]}")

    except Exception as e:
        print(f"[WhatsApp] Exception: {e}", flush=True)
        reply_text = f"⚠️ *Terjadi kesalahan tak terduga.*\n\n_{str(e)[:150]}_\n\nSilakan coba kirim ulang."
        _wa_notify_admin("Exception tak terduga",
            f"chat_id: {chat_id}\nerror: {str(e)[:400]}")

    finally:
        _done[0] = True  # hentikan thread progress

    # 4. Kirim balasan ke WA — deteksi JSON interactive payload dulu
    if not reply_text:
        reply_text = "Maaf, APRIS tidak dapat memproses pesan ini saat ini."

    def _smart_send(text: str):
        """
        Kirim teks ke WA. Jika ada ```json block berisi interactive payload,
        ekstrak dan kirim via send_interactive_from_cloud_api().
        Teks di luar block dikirim terpisah sebagai pesan biasa.
        """
        import json as _json, re as _re2

        # Cari semua ```json ... ``` blocks
        json_blocks = _re2.findall(r'```json\s*([\s\S]*?)```', text)
        # Hapus semua json blocks dari teks utama
        clean_text  = _re2.sub(r'```json\s*[\s\S]*?```', '', text).strip()

        # Guard: jika keduanya kosong, kirim pesan fallback
        if not clean_text and not json_blocks:
            green_api.send_message(chat_id, "_Maaf, tidak ada respons yang dapat ditampilkan._")
            return

        # Kirim teks utama dulu (jika ada)
        if clean_text:
            try:
                green_api.send_message(chat_id, clean_text)
            except Exception as e:
                print(f"[WhatsApp] Gagal kirim teks: {e}", flush=True)

        # Kirim setiap interactive payload
        for block in json_blocks:
            try:
                payload = _json.loads(block.strip())
                if "interactive" in payload:
                    green_api.send_interactive_from_cloud_api(chat_id, payload)
                else:
                    # Bukan interactive payload — kirim sebagai monospace
                    green_api.send_message(chat_id, f"```\n{block.strip()[:2000]}\n```")
            except (_json.JSONDecodeError, Exception) as e:
                print(f"[Interactive] Gagal parse JSON block: {e}")
                green_api.send_message(chat_id, block.strip()[:2000])

    try:
        _smart_send(reply_text)
    except Exception as send_err:
        print(f"[WhatsApp] Gagal kirim balasan (1): {send_err}", flush=True)
        time.sleep(3)
        try:
            green_api.send_message(chat_id, reply_text)
            print("[WhatsApp] Retry kirim balasan: berhasil", flush=True)
        except Exception as send_err2:
            print(f"[WhatsApp] Retry gagal: {send_err2}", flush=True)
            _wa_notify_admin("Gagal kirim balasan ke WA (2x percobaan)",
                f"chat_id: {chat_id}\nerror: {str(send_err2)[:300]}")





@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    """
    Webhook untuk Green-API.
    Menerima notifikasi pesan masuk dan merespons 200 OK.
    """
    data = request.get_json(silent=True) or {}
    
    # Pastikan ini event pesan masuk (atau pesan keluar dari HP sendiri untuk testing Developer)
    webhook_type = data.get("typeWebhook")
    if webhook_type in ["incomingMessageReceived", "outgoingMessageReceived"]:
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

    data        = request.get_json(silent=True) or {}
    user_msg    = (data.get("message") or "").strip()
    session_id  = data.get("session_id") or "default"
    sender_name = data.get("sender_name") or ""


    if not user_msg:
        return jsonify({"error": "Pesan tidak boleh kosong."}), 400

    if not GEMINI_API_KEY:
        return jsonify({"error": "GEMINI_API_KEY belum dikonfigurasi."}), 500

    # Per-session lock: cegah race condition jika dua request masuk bersamaan
    session_lock = _get_session_lock(session_id)
    if not session_lock.acquire(blocking=True, timeout=30):
        return jsonify({
            "error": "Server sedang memproses permintaan sebelumnya untuk sesi ini. Silakan tunggu.",
            "code": 429,
        }), 429

    try:
        history = get_or_create_session(session_id)

        from google import genai
        from google.genai import types

        client = get_client()

        # Bangun contents: system prompt + history + pesan baru
        contents = []

        # Sisipkan system prompt sebagai pesan pertama user/model
        if not history:
            now_str_full  = datetime.now(TZ).strftime("%A, %Y-%m-%d %H:%M:%S %z")
            # Inject nomor WA & nama user agar Gemini bisa pakai di tag SCHEDULE_MSG
            _sender_label = sender_name or session_id
            dynamic_prompt = (
                f"[SYSTEM]\n"
                f"Waktu saat ini: {now_str_full}\n"
                f"Nomor WA pengguna: {session_id}\n"
                f"Nama pengguna: {_sender_label}\n\n"
                f"{SYSTEM_PROMPT}"
            )
            contents.append(
                types.Content(role="user",  parts=[types.Part(text=dynamic_prompt)])
            )
            contents.append(
                types.Content(role="model", parts=[types.Part(text="Siap. Saya adalah APRIS.")])
            )

        # Context Window Management: ringkas jika history terlalu panjang
        history[:] = _summarize_history(history, client, session_id=session_id)

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
                from features import memory as _mem_json  # fallback JSON (global)

                # Proses SEMUA tag <REMEMBER>
                rem_matches = re.findall(r'<REMEMBER fact="([^"]+)"\s*/?>', apris_reply)
                if rem_matches:
                    apris_reply = re.sub(r'<REMEMBER[^>]*/?>',  '', apris_reply).strip()
                    for fact in rem_matches:
                        fact = fact.strip()
                        # Simpan ke SQLite per-user (persistent Railway Volume)
                        if _LMEM_OK and _lmem and session_id:
                            _lmem.save_fact(session_id, fact)
                        # Simpan juga ke JSON global (fallback)
                        _mem_json.add_memory(fact)
                        apris_reply += f"\n\n\U0001f9e0 *Saya telah mengingat hal tersebut.*"

                # Proses SEMUA tag <FORGET>
                for_matches = re.findall(r'<FORGET fact="([^"]+)"\s*/?>', apris_reply)
                if for_matches:
                    apris_reply = re.sub(r'<FORGET[^>]*/?>',  '', apris_reply).strip()
                    for fact in for_matches:
                        fact = fact.strip()
                        if _LMEM_OK and _lmem and session_id:
                            _lmem.forget_fact(session_id, fact)
                        _mem_json.remove_memory(fact)
                        apris_reply += f"\n\n\U0001f9e0 *Baik, saya telah melupakannya.*"

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
        if "<SEARCH_CONTACT" in apris_reply or "<GET_CONTACT_WA" in apris_reply:
            import re
            try:
                from features import contacts as contacts_module
                # SEARCH_CONTACT — tampilkan detail kontak
                sc_matches = re.findall(r'<SEARCH_CONTACT name="([^"]+)"\s*/?>', apris_reply)
                if sc_matches:
                    apris_reply = re.sub(r'<SEARCH_CONTACT[^>]*/?>', '', apris_reply).strip()
                    for name in sc_matches:
                        res = contacts_module.search_contact(name.strip())
                        apris_reply += f"\n\n{res}"
                # GET_CONTACT_WA — tampilkan nomor WA siap pakai
                gw_matches = re.findall(r'<GET_CONTACT_WA name="([^"]+)"\s*/?>', apris_reply)
                if gw_matches:
                    apris_reply = re.sub(r'<GET_CONTACT_WA[^>]*/?>', '', apris_reply).strip()
                    for name in gw_matches:
                        res = contacts_module.get_contact_wa(name.strip())
                        apris_reply += f"\n\n{res}"
            except Exception as e:
                apris_reply += f"\n\n_Gagal mencari kontak: {e}_"

        # Intercept untuk Web Search
        if "<WEB_SEARCH" in apris_reply or "<SEARCH_NEWS" in apris_reply:
            import re
            try:
                from features import search as _search_mod

                # WEB_SEARCH
                ws_matches = re.findall(r'<WEB_SEARCH\s+query="([^"]+)"\s*/?>', apris_reply)
                if ws_matches:
                    apris_reply = re.sub(r'<WEB_SEARCH[^>]*/?>',  '', apris_reply).strip()
                    for query in ws_matches:
                        result = _search_mod.search(query.strip())
                        summary = result.get("summary", "_Tidak ada hasil._")
                        apris_reply += f"\n\n🔍 *Hasil pencarian untuk: {query}*\n\n{summary}"

                # SEARCH_NEWS
                sn_matches = re.findall(r'<SEARCH_NEWS\s+query="([^"]+)"\s*/?>', apris_reply)
                if sn_matches:
                    apris_reply = re.sub(r'<SEARCH_NEWS[^>]*/?>',  '', apris_reply).strip()
                    for query in sn_matches:
                        result = _search_mod.search_news(query.strip())
                        summary = result.get("summary", "_Tidak ada berita._")
                        apris_reply += f"\n\n📰 *Berita tentang: {query}*\n\n{summary}"

            except Exception as e:
                apris_reply += f"\n\n_Gagal melakukan pencarian: {e}_"

        # Intercept untuk Pesan Terjadwal (SCHEDULE_MSG)
        if "<SCHEDULE_MSG" in apris_reply or "<CANCEL_SCHEDULE_MSG" in apris_reply or "<LIST_SCHEDULE_MSG" in apris_reply:
            import re
            try:
                from features import reminder as rem_mod
                from features import green_api as _sched_ga

                # LIST_SCHEDULE_MSG
                if "<LIST_SCHEDULE_MSG" in apris_reply:
                    apris_reply = re.sub(r'<LIST_SCHEDULE_MSG\s*/?>', '', apris_reply).strip()
                    jobs = rem_mod.list_reminders()
                    if jobs:
                        job_lines = "\n".join(
                            f"- `{j['id']}` — _{j['name']}_ → {j['next_run']}"
                            for j in jobs if j['id'].startswith('rem_')
                        )
                        apris_reply += f"\n\n*Pesan Terjadwal:*\n{job_lines}" if job_lines else "\n\n_Tidak ada pesan terjadwal aktif._"
                    else:
                        apris_reply += "\n\n_Tidak ada pesan terjadwal aktif._"

                # CANCEL_SCHEDULE_MSG
                cancel_m = re.findall(r'<CANCEL_SCHEDULE_MSG\s+id="([^"]+)"\s*/?>', apris_reply)
                if cancel_m:
                    apris_reply = re.sub(r'<CANCEL_SCHEDULE_MSG[^>]*/?>', '', apris_reply).strip()
                    for rid in cancel_m:
                        try:
                            rem_mod.cancel_reminder(rid.strip())
                            apris_reply += f"\n\n✅ Pesan terjadwal `{rid}` berhasil dibatalkan."
                        except ValueError:
                            apris_reply += f"\n\n❌ ID `{rid}` tidak ditemukan."

                # SCHEDULE_MSG — auto-resolve nama & normalisasi nomor
                sched_matches = re.findall(
                    r'<SCHEDULE_MSG\s+to="([^"]+)"\s+at="([^"]+)"\s+message="([^"]+)"\s*/?>',
                    apris_reply
                )
                if sched_matches:
                    apris_reply = re.sub(r'<SCHEDULE_MSG[^>]*/?>', '', apris_reply).strip()
                    from features import contacts as _ct
                    for to_raw, at_time, msg_text in sched_matches:
                        run_at = at_time.replace('T', ' ')[:16]
                        # Cek apakah input adalah nomor atau nama
                        digits_only = re.sub(r'\D', '', to_raw)
                        if len(digits_only) >= 8:
                            to_num        = _ct.normalize_wa_number(to_raw)
                            contact_label = to_num
                        else:
                            to_num, disp  = _ct.resolve_contact_to_wa(to_raw)
                            contact_label = f"{disp} ({to_num})" if to_num else to_raw
                        if not to_num:
                            apris_reply += (
                                f"\n\n❌ Tidak dapat menemukan nomor WA untuk *{to_raw}*. "
                                f"Pastikan kontak ada di Google Contacts atau berikan nomor lengkap."
                            )
                            continue
                        def _make_wa_callback(target_id):
                            def _cb(target, message):
                                try: _sched_ga.send_message(target_id, message)
                                except Exception as cb_e:
                                    print(f"[ScheduleMsg] Gagal kirim ke {target_id}: {cb_e}")
                            return _cb
                        rem_mod.set_send_callback(_make_wa_callback(to_num))
                        result = rem_mod.set_reminder(message=msg_text, target=to_num, run_at=run_at)
                        apris_reply += (
                            f"\n\n✅ Pesan dijadwalkan ke *{contact_label}* pada *{run_at} WIB*."
                            f"\nID: `{result['id']}`"
                        )
            except Exception as e:
                apris_reply += f"\n\n_Gagal menjadwalkan pesan: {e}_"

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
        
        # Penanganan 503 Overloaded (High Demand)
        if "503" in err_str or "UNAVAILABLE" in err_str or "high demand" in err_str:
            return jsonify({
                "error": "Server AI sedang melayani terlalu banyak permintaan secara global (Overloaded). Lonjakan trafik ini biasanya hanya sementara.",
                "code" : 503
            }), 503

        return jsonify({"error": err_str}), 500
    finally:
        # Selalu bebaskan per-session lock
        session_lock.release()


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

    # Simpan ringkasan ke long-term memory sebelum clear
    if _LMEM_OK:
        try:
            with _sessions_lock:
                history = _chat_sessions.get(session_id, {}).get("messages", [])
            if history:
                key_facts = _lmem.extract_key_facts_from_history(history)
                # Buat ringkasan singkat dari history terakhir
                summary_lines = []
                for m in history[-10:]:
                    if m.get("role") in ("user", "assistant"):
                        summary_lines.append(f"{m['role'].upper()}: {m['content'][:200]}")
                summary = "\n".join(summary_lines)
                _lmem.save_memory(session_id, summary, key_facts)
                print(f"[LongMemory] Session {session_id} disimpan sebelum clear", flush=True)
        except Exception as _me:
            print(f"[LongMemory] Gagal simpan sebelum clear: {_me}", flush=True)

    with _sessions_lock:
        _chat_sessions[session_id] = {"messages": [], "last_access": datetime.now(TZ)}
    return jsonify({"status": "cleared", "session_id": session_id})


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

    # --- Google Contacts (SEARCH_CONTACT & GET_CONTACT_WA) ---
    if "<SEARCH_CONTACT" in apris_reply or "<GET_CONTACT_WA" in apris_reply:
        try:
            from features import contacts as contacts_module
            # SEARCH_CONTACT — detail lengkap
            sc_matches = _re.findall(r'<SEARCH_CONTACT name="([^"]+)"\s*/?>', apris_reply)
            if sc_matches:
                apris_reply = _re.sub(r'<SEARCH_CONTACT[^>]*/?>', '', apris_reply).strip()
                for name in sc_matches:
                    res = contacts_module.search_contact(name.strip())
                    apris_reply += f"\n\n{res}"
            # GET_CONTACT_WA — nomor WA siap pakai
            gw_matches = _re.findall(r'<GET_CONTACT_WA name="([^"]+)"\s*/?>', apris_reply)
            if gw_matches:
                apris_reply = _re.sub(r'<GET_CONTACT_WA[^>]*/?>', '', apris_reply).strip()
                for name in gw_matches:
                    res = contacts_module.get_contact_wa(name.strip())
                    apris_reply += f"\n\n{res}"
        except Exception as e:
            apris_reply += f"\n\n_Gagal mencari kontak: {e}_"

    # --- Pesan Terjadwal (SCHEDULE_MSG) ---
    if "<SCHEDULE_MSG" in apris_reply or "<CANCEL_SCHEDULE_MSG" in apris_reply or "<LIST_SCHEDULE_MSG" in apris_reply:
        try:
            from features import reminder as rem_mod
            from features import green_api as _sched_ga

            # LIST_SCHEDULE_MSG
            if "<LIST_SCHEDULE_MSG" in apris_reply:
                apris_reply = _re.sub(r'<LIST_SCHEDULE_MSG\s*/?>', '', apris_reply).strip()
                jobs = rem_mod.list_reminders()
                scheduled = [j for j in jobs if j['id'].startswith('rem_')]
                if scheduled:
                    job_lines = "\n".join(
                        f"- `{j['id']}` — _{j['name']}_ → {j['next_run']}"
                        for j in scheduled
                    )
                    apris_reply += f"\n\n*Pesan Terjadwal:*\n{job_lines}"
                else:
                    apris_reply += "\n\n_Tidak ada pesan terjadwal aktif._"

            # CANCEL_SCHEDULE_MSG
            cancel_m = _re.findall(r'<CANCEL_SCHEDULE_MSG\s+id="([^"]+)"\s*/?>', apris_reply)
            if cancel_m:
                apris_reply = _re.sub(r'<CANCEL_SCHEDULE_MSG[^>]*/?>', '', apris_reply).strip()
                for rid in cancel_m:
                    try:
                        rem_mod.cancel_reminder(rid.strip())
                        apris_reply += f"\n\n✅ Pesan terjadwal `{rid}` berhasil dibatalkan."
                    except ValueError:
                        apris_reply += f"\n\n❌ ID `{rid}` tidak ditemukan."

            # SCHEDULE_MSG — auto-resolve nama & normalisasi nomor
            sched_matches = _re.findall(
                r'<SCHEDULE_MSG\s+to="([^"]+)"\s+at="([^"]+)"\s+message="([^"]+)"\s*/?>',
                apris_reply
            )
            if sched_matches:
                apris_reply = _re.sub(r'<SCHEDULE_MSG[^>]*/?>', '', apris_reply).strip()
                from features import contacts as _ct
                for to_raw, at_time, msg_text in sched_matches:
                    run_at = at_time.replace('T', ' ')[:16]
                    digits_only = _re.sub(r'\D', '', to_raw)
                    if len(digits_only) >= 8:
                        to_num        = _ct.normalize_wa_number(to_raw)
                        contact_label = to_num
                    else:
                        to_num, disp  = _ct.resolve_contact_to_wa(to_raw)
                        contact_label = f"{disp} ({to_num})" if to_num else to_raw
                    if not to_num:
                        apris_reply += (
                            f"\n\n❌ Tidak dapat menemukan nomor WA untuk *{to_raw}*. "
                            f"Pastikan kontak ada di Google Contacts atau berikan nomor lengkap."
                        )
                        continue
                    def _make_wa_callback(target_id):
                        def _cb(target, message):
                            try: _sched_ga.send_message(target_id, message)
                            except Exception as cb_e:
                                print(f"[ScheduleMsg] Gagal kirim ke {target_id}: {cb_e}")
                        return _cb
                    rem_mod.set_send_callback(_make_wa_callback(to_num))
                    result = rem_mod.set_reminder(message=msg_text, target=to_num, run_at=run_at)
                    apris_reply += (
                        f"\n\n✅ Pesan dijadwalkan ke *{contact_label}* pada *{run_at} WIB*."
                        f"\nID: `{result['id']}`"
                    )
        except Exception as e:
            apris_reply += f"\n\n_Gagal menjadwalkan pesan: {e}_"

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
