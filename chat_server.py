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
import json
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY", "")
CHAT_MODEL        = os.getenv("GEMINI_CHAT_MODEL", "models/gemini-2.5-flash")
EMBEDDING_MODEL   = os.getenv("GEMINI_EMBEDDING_MODEL", "models/gemini-embedding-001")
# Railway set PORT otomatis; fallback ke 5052 untuk lokal
SERVER_PORT       = int(os.getenv("PORT", os.getenv("CHAT_SERVER_PORT", 5052)))

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
• Bahasa Indonesia baku, lugas, bebas filler words

§3 FORMAT
• Gunakan bullet (•) untuk daftar 3+ item
• **Teks tebal** untuk istilah penting & metrik
• Pemisah (---) untuk transisi topik
• Respons proporsional: singkat untuk pertanyaan sederhana, terstruktur untuk kompleks

§4 BATASAN
• DILARANG diagnosis medis definitif — arahkan ke dokter
• DILARANG nasihat hukum mengikat — arahkan ke pengacara
• Jujur jika tidak tahu atau data tidak real-time

Zona Waktu: WIB (Asia/Jakarta) | Bahasa: Indonesia | Versi: APRIS v3.0"""

# ---------------------------------------------------------------------------
# Gemini client & session history
# ---------------------------------------------------------------------------
_genai_client  = None
_chat_sessions = {}   # session_id → list of messages
_chroma_col    = None  # ChromaDB collection (lazy load)


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


def get_or_create_session(session_id: str) -> list:
    if session_id not in _chat_sessions:
        _chat_sessions[session_id] = []
    return _chat_sessions[session_id]


# ---------------------------------------------------------------------------
# Flask App
# ---------------------------------------------------------------------------
app = Flask(__name__, static_folder="web_chat", static_url_path="/static")
CORS(app)


@app.route("/")
def index():
    return send_from_directory("web_chat", "index.html")


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
            contents.append(
                types.Content(role="user",  parts=[types.Part(text=f"[SYSTEM]\n{SYSTEM_PROMPT}")])
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
        if rag_context:
            augmented_msg = (
                f"{user_msg}\n\n"
                f"---\n"
                f"[Konteks dari Knowledge Base APRIS — gunakan jika relevan]:\n"
                f"{rag_context}\n"
                f"---"
            )
        else:
            augmented_msg = user_msg

        # Pesan baru user (sudah diaugmentasi dengan konteks RAG)
        contents.append(
            types.Content(role="user", parts=[types.Part(text=augmented_msg)])
        )

        response = client.models.generate_content(
            model    = CHAT_MODEL,
            contents = contents,
        )

        apris_reply = response.text.strip()

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
        return jsonify({"error": str(e)}), 500


@app.route("/history")
def history():
    session_id = request.args.get("session_id", "default")
    return jsonify({
        "session_id": session_id,
        "messages"  : _chat_sessions.get(session_id, []),
        "count"     : len(_chat_sessions.get(session_id, [])),
    })


@app.route("/clear", methods=["POST"])
def clear():
    data       = request.get_json(silent=True) or {}
    session_id = data.get("session_id", "default")
    _chat_sessions[session_id] = []
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
