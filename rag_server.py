"""
rag_server.py — APRIS RAG API Server (Flask)
==============================================
REST API agar sistem RAG bisa dipanggil dari WhatsApp / OpenClaw / tools lain.

Endpoints:
    POST /ask          — tanya berdasarkan dokumen PDF
    GET  /status       — status server & jumlah dokumen
    GET  /documents    — daftar dokumen yang tersimpan
    POST /ingest       — trigger ingest PDF baru (via API)

Cara pakai:
    python rag_server.py               # jalankan di port 5050
    python rag_server.py --port 8080   # port kustom
"""

import os
import sys
import argparse
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from werkzeug.utils import secure_filename

import chromadb
from google import genai
from google.genai import types
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from dotenv import load_dotenv
from colorama import init

# --- Init --------------------------------------------------------------------
# colorama: convert=True hanya untuk Windows, di Linux tidak diperlukan
if sys.platform == "win32":
    init(autoreset=True, convert=True)
else:
    init(autoreset=True)

load_dotenv()

# Deteksi environment: Railway set RAILWAY_ENVIRONMENT otomatis
IS_PRODUCTION = os.getenv("RAILWAY_ENVIRONMENT") is not None

GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY", "")
CHAT_MODEL        = os.getenv("GEMINI_CHAT_MODEL", "gemini-1.5-flash")
EMBEDDING_MODEL   = os.getenv("GEMINI_EMBEDDING_MODEL", "models/text-embedding-004")

# Di Railway: gunakan /data/* (Railway Volume di-mount ke /data)
# Di lokal  : gunakan ./vectorstore dan ./docs
DEFAULT_DB_PATH   = "/data/vectorstore" if IS_PRODUCTION else "./vectorstore"
DEFAULT_DOCS      = "/data/docs"        if IS_PRODUCTION else "./docs"

CHROMA_DB_PATH    = os.getenv("CHROMA_DB_PATH",        DEFAULT_DB_PATH)
CHROMA_COLLECTION = os.getenv("CHROMA_COLLECTION_NAME", "apris_knowledge")
TOP_K             = int(os.getenv("TOP_K_RESULTS", 5))
# Railway otomatis set PORT — fallback ke 5050 untuk lokal
SERVER_PORT       = int(os.getenv("PORT", os.getenv("RAG_SERVER_PORT", 5050)))
SERVER_HOST       = os.getenv("RAG_SERVER_HOST", "0.0.0.0")
DOCS_FOLDER       = os.getenv("DOCS_FOLDER", DEFAULT_DOCS)

# Buat folder dengan aman — jangan crash jika permission denied (Volume belum terpasang)
try:
    Path(CHROMA_DB_PATH).mkdir(parents=True, exist_ok=True)
    Path(DOCS_FOLDER).mkdir(parents=True, exist_ok=True)
except Exception as _mkdir_err:
    print(f"[WARN] Tidak bisa buat folder: {_mkdir_err}")

# Gemini client — dibuat lazy agar server bisa start walau API key belum diset
_genai_client = None

def get_genai_client():
    """Lazy init Gemini client."""
    global _genai_client
    if _genai_client is None:
        if not GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY belum diset. Tambahkan di Railway Variables.")
        _genai_client = genai.Client(api_key=GEMINI_API_KEY)
    return _genai_client

app = Flask(__name__)
CORS(app)

# ─── ChromaDB ────────────────────────────────────────────────────────────────

def get_collection():
    """Ambil ChromaDB collection."""
    client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
    try:
        return client.get_collection(CHROMA_COLLECTION)
    except Exception:
        return None


# ─── Core RAG Functions ──────────────────────────────────────────────────────

def get_query_embedding(text: str) -> list[float]:
    result = get_genai_client().models.embed_content(
        model=EMBEDDING_MODEL,
        contents=text,
        config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY")
    )
    return result.embeddings[0].values


def search_documents(query: str, top_k: int = TOP_K) -> list[dict]:
    collection = get_collection()
    if not collection or collection.count() == 0:
        return []

    query_emb = get_query_embedding(query)
    results   = collection.query(
        query_embeddings=[query_emb],
        n_results=min(top_k, collection.count()),
        include=["documents", "metadatas", "distances"]
    )

    chunks = []
    if results and results["documents"]:
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0]
        ):
            chunks.append({
                "text":     doc,
                "source":   meta.get("source", "unknown"),
                "page_num": meta.get("page_num", "?"),
                "language": meta.get("language", "?"),
                "score":    round(1 - dist, 4)
            })
    return chunks


def build_prompt(query: str, chunks: list[dict]) -> str:
    context_parts = []
    for c in chunks:
        context_parts.append(
            f"[Sumber: {c['source']}, Hal. {c['page_num']}]\n{c['text']}"
        )
    context = "\n\n---\n\n".join(context_parts)

    return f"""Kamu adalah APRIS, asisten pribadi cerdas berbahasa Indonesia.
Jawablah pertanyaan pengguna HANYA berdasarkan konteks dokumen yang diberikan.
Jika jawaban tidak tersedia dalam dokumen, katakan dengan jujur.
Sebutkan nama file sumber saat menjawab agar pengguna tahu asal informasi.
Gunakan bahasa yang sama dengan pertanyaan (Indonesia/Inggris/campuran).
Format jawaban untuk WhatsApp: gunakan *bold* untuk istilah penting, bullet point untuk daftar.

═══════════════════════════════
KONTEKS DOKUMEN:
═══════════════════════════════
{context}

═══════════════════════════════
PERTANYAAN:
═══════════════════════════════
{query}

═══════════════════════════════
JAWABAN:
═══════════════════════════════"""


def generate_answer(query: str, chunks: list[dict]) -> str:
    if not chunks:
        return "Tidak ada dokumen relevan ditemukan dalam knowledge base. Pastikan PDF sudah diingest."

    prompt = build_prompt(query, chunks)
    response = get_genai_client().models.generate_content(
        model=CHAT_MODEL,
        contents=prompt
    )
    return response.text


# ─── API Endpoints ────────────────────────────────────────────────────────────

@app.route("/status", methods=["GET"])
def status():
    """Status server dan database — selalu return 200 untuk healthcheck."""
    try:
        collection  = get_collection()
        doc_count   = collection.count() if collection else 0
    except Exception:
        doc_count = 0

    try:
        docs_folder = os.path.abspath(DOCS_FOLDER)
        pdf_count   = len(list(Path(docs_folder).glob("*.pdf"))) if os.path.exists(docs_folder) else 0
    except Exception:
        docs_folder = DOCS_FOLDER
        pdf_count   = 0

    return jsonify({
        "status":       "online",
        "timestamp":    datetime.now().isoformat(),
        "environment":  "production" if IS_PRODUCTION else "local",
        "api_key_set":  bool(GEMINI_API_KEY),
        "database": {
            "path":         CHROMA_DB_PATH,
            "collection":   CHROMA_COLLECTION,
            "total_chunks": doc_count
        },
        "docs_folder": {
            "path":      docs_folder,
            "pdf_count": pdf_count
        },
        "models": {
            "chat":      CHAT_MODEL,
            "embedding": EMBEDDING_MODEL
        }
    })


@app.route("/documents", methods=["GET"])
def list_documents():
    """Daftar semua dokumen yang tersimpan di database."""
    collection = get_collection()
    if not collection or collection.count() == 0:
        return jsonify({"documents": [], "total_chunks": 0})

    results = collection.get(limit=5000, include=["metadatas"])
    sources = {}
    for meta in results["metadatas"]:
        src = meta.get("source", "unknown")
        if src not in sources:
            sources[src] = {
                "chunks":   0,
                "pages":    set(),
                "language": meta.get("language", "?"),
                "ingested_at": meta.get("ingested_at", "?")
            }
        sources[src]["chunks"] += 1
        sources[src]["pages"].add(meta.get("page_num", 0))

    docs = []
    for src, info in sorted(sources.items()):
        docs.append({
            "filename":    src,
            "chunks":      info["chunks"],
            "pages":       len(info["pages"]),
            "language":    info["language"],
            "ingested_at": info["ingested_at"]
        })

    return jsonify({
        "documents":   docs,
        "total_docs":  len(docs),
        "total_chunks": collection.count()
    })


@app.route("/ask", methods=["POST"])
def ask():
    """
    Tanya berdasarkan dokumen PDF.

    Request body (JSON):
        {
            "query": "Apa yang dimaksud dengan XYZ?",
            "top_k": 5   (opsional)
        }

    Response:
        {
            "answer": "...",
            "sources": [...],
            "query": "..."
        }
    """
    data = request.get_json(silent=True)
    if not data or "query" not in data:
        return jsonify({"error": "Field 'query' wajib diisi"}), 400

    query  = data["query"].strip()
    top_k  = int(data.get("top_k", TOP_K))

    if not query:
        return jsonify({"error": "Query tidak boleh kosong"}), 400

    try:
        chunks = search_documents(query, top_k=top_k)
        answer = generate_answer(query, chunks)

        sources = [
            {
                "source":   c["source"],
                "page_num": c["page_num"],
                "score":    c["score"],
                "language": c["language"]
            }
            for c in chunks
        ]

        return jsonify({
            "query":        query,
            "answer":       answer,
            "sources":      sources,
            "chunks_used":  len(chunks),
            "timestamp":    datetime.now().isoformat()
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/ingest", methods=["POST"])
def trigger_ingest():
    """
    Trigger proses ingest PDF baru via API.
    Berjalan di background thread.

    Request body (JSON) - opsional:
        {
            "reset": false,   // reset database & proses ulang semua
            "file": "buku.pdf"  // proses file spesifik
        }
    """
    data   = request.get_json(silent=True) or {}
    reset  = data.get("reset", False)
    file   = data.get("file", None)

    def run_ingest():
        cmd = [sys.executable, "ingest.py"]
        if reset:
            cmd.append("--reset")
        if file:
            cmd.extend(["--file", file])
        subprocess.run(cmd, cwd=os.path.dirname(os.path.abspath(__file__)))

    thread = threading.Thread(target=run_ingest, daemon=True)
    thread.start()

    return jsonify({
        "status":  "ingest_started",
        "reset":   reset,
        "file":    file,
        "message": "Proses ingest berjalan di background. Cek terminal untuk progress."
    })


# ─── Error Handlers ──────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
@app.route("/admin", methods=["GET"])
def admin_panel():
    """Sajikan Admin Dashboard."""
    admin_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "admin.html")
    if os.path.exists(admin_path):
        return send_file(admin_path)
    return jsonify({"message": "Admin panel not found. Make sure admin.html exists."}), 404


@app.route("/upload", methods=["POST"])
def upload_pdf():
    """
    Upload file PDF ke folder docs/.

    Request: multipart/form-data dengan field 'file'
    Response: { filename, size, path }
    """
    if "file" not in request.files:
        return jsonify({"error": "Tidak ada file yang dikirim"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "Nama file kosong"}), 400

    if not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Hanya file PDF yang diizinkan"}), 400

    docs_dir = Path(DOCS_FOLDER)
    docs_dir.mkdir(exist_ok=True)

    filename  = secure_filename(file.filename)
    save_path = docs_dir / filename
    file.save(str(save_path))

    size = save_path.stat().st_size

    return jsonify({
        "status":   "uploaded",
        "filename": filename,
        "size":     size,
        "path":     str(save_path.resolve())
    })


@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Endpoint tidak ditemukan"}), 404


@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({"error": "Method tidak diizinkan"}), 405


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="APRIS RAG - API Server")
    parser.add_argument("--port", type=int, default=SERVER_PORT, help=f"Port server (default: {SERVER_PORT})")
    parser.add_argument("--host", type=str, default=SERVER_HOST, help=f"Host server (default: {SERVER_HOST})")
    args = parser.parse_args()

    print("")
    print("=" * 55)
    print("  APRIS RAG - API Server")
    print("=" * 55)
    print(f"  Server   : http://{args.host}:{args.port}")
    print(f"  Admin UI : http://localhost:{args.port}/admin")
    print(f"  Endpoints:")
    print(f"     GET  /            - Admin Dashboard")
    print(f"     GET  /admin       - Admin Dashboard")
    print(f"     GET  /status      - cek status server")
    print(f"     GET  /documents   - daftar dokumen")
    print(f"     POST /ask         - tanya dokumen")
    print(f"     POST /upload      - upload PDF baru")
    print(f"     POST /ingest      - trigger ingest PDF baru")
    print(f"  Tekan Ctrl+C untuk berhenti")
    print("")

    # Cek database
    collection = get_collection()
    if collection:
        print(f"  [OK] Database terhubung: {collection.count()} chunks tersedia")
    else:
        print(f"  [!] Database belum ada. Jalankan: python ingest.py")
    print()

    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
