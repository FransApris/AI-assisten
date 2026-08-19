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

GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY", "")
CHAT_MODEL      = os.getenv("GEMINI_CHAT_MODEL",      "gemini-2.5-flash").strip()
EMBEDDING_MODEL = os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001").strip()

# Di Railway: gunakan /data/* (Railway Volume di-mount ke /data)
# Di lokal  : gunakan ./vectorstore dan ./docs
DEFAULT_DB_PATH   = "/data/vectorstore" if IS_PRODUCTION else "./vectorstore"
DEFAULT_DOCS      = "/data/docs"        if IS_PRODUCTION else "./docs"

CHROMA_DB_PATH    = os.getenv("CHROMA_DB_PATH",        DEFAULT_DB_PATH).strip()
CHROMA_COLLECTION = os.getenv("CHROMA_COLLECTION_NAME", "apris_knowledge").strip()
TOP_K             = int(os.getenv("TOP_K_RESULTS", 5))
# Railway otomatis set PORT — fallback ke 5050 untuk lokal
SERVER_PORT       = int(os.getenv("PORT", os.getenv("RAG_SERVER_PORT", 5050)))
SERVER_HOST       = os.getenv("RAG_SERVER_HOST", "0.0.0.0").strip()
DOCS_FOLDER       = os.getenv("DOCS_FOLDER", DEFAULT_DOCS).strip()

# Buat folder dengan aman — jangan crash jika permission denied (Volume belum terpasang)
try:
    Path(CHROMA_DB_PATH).mkdir(parents=True, exist_ok=True)
    Path(DOCS_FOLDER).mkdir(parents=True, exist_ok=True)
except Exception as _mkdir_err:
    print(f"[WARN] Tidak bisa buat folder: {_mkdir_err}")

# Gemini client — dibuat lazy agar server bisa start walau API key belum diset
_genai_client = None

def get_genai_client():
    """Lazy init Gemini client — pakai API v1 (bukan v1beta default)."""
    global _genai_client
    if _genai_client is None:
        if not GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY belum diset. Tambahkan di Railway Variables.")
        _genai_client = genai.Client(
            api_key=GEMINI_API_KEY,
            http_options={"api_version": "v1"}
        )
    return _genai_client

# Progress tracker untuk ingest — diakses via /ingest-progress
_ingest_progress = {
    "running":       False,
    "stage":         "idle",      # idle | extracting | ocr | embedding | done | error
    "current_file":  "",
    "current_page":  0,
    "total_pages":   0,
    "chunks_saved":  0,
    "message":       "",
    "error":         None,
    "started_at":    None,
    "finished_at":   None,
}

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


# --- API Endpoints -----------------------------------------------------------

@app.route("/favicon.ico", methods=["GET"])
def favicon():
    """Return empty favicon to prevent 404 log noise."""
    return "", 204


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
    Berjalan di background thread dengan error logging.
    """
    data  = request.get_json(silent=True) or {}
    reset = data.get("reset", False)
    file  = data.get("file",  None)
    # force=True by default via API — retry bahkan jika sebelumnya 0 chunks
    force = data.get("force", True)

    ingest_status = {"running": True, "result": None, "error": None}

    def run_ingest():
        global _ingest_progress
        _ingest_progress.update({
            "running": True, "stage": "extracting",
            "current_file": file or "semua PDF",
            "current_page": 0, "total_pages": 0,
            "chunks_saved": 0, "error": None,
            "message": "Memulai proses ingest...",
            "started_at": datetime.now().isoformat(),
            "finished_at": None,
        })
        try:
            import ingest as ingest_module
            # Inject progress callback ke ingest module
            ingest_module._progress = _ingest_progress
            from ingest import run_ingestion
            result = run_ingestion(target_file=file, reset=reset, force=force)
            _ingest_progress.update({
                "stage": "done",
                "chunks_saved": result.get("total_chunks", 0),
                "message": f"Selesai! {result.get('total_chunks', 0)} chunks disimpan.",
                "finished_at": datetime.now().isoformat(),
            })
            print(f"[INGEST] Selesai: {result}", flush=True)
        except Exception as e:
            import traceback
            _ingest_progress.update({
                "stage": "error", "error": str(e),
                "message": f"Error: {e}",
                "finished_at": datetime.now().isoformat(),
            })
            print(f"[INGEST ERROR] {traceback.format_exc()}", flush=True)
        finally:
            _ingest_progress["running"] = False

    thread = threading.Thread(target=run_ingest, daemon=True)
    thread.start()

    return jsonify({
        "status":  "ingest_started",
        "reset":   reset,
        "file":    file,
        "message": "Proses ingest berjalan. Pantau di /ingest-progress."
    })


@app.route("/ingest-progress", methods=["GET"])
def ingest_progress():
    """Progress real-time dari proses ingest yang sedang berjalan."""
    return jsonify(_ingest_progress)


@app.route("/debug-ingest", methods=["GET", "POST"])
def debug_ingest():
    """
    Debug endpoint: jalankan ingest SINKRON dan tampilkan error detail.
    GET: cek kondisi folder & file
    POST: jalankan ingest sinkron pada 1 PDF pertama
    """
    import traceback

    # Cek kondisi path
    db_path   = Path(CHROMA_DB_PATH)
    docs_path = Path(DOCS_FOLDER)

    info = {
        "paths": {
            "chroma_db": str(db_path.resolve()),
            "docs_folder": str(docs_path.resolve()),
            "chroma_db_exists": db_path.exists(),
            "docs_folder_exists": docs_path.exists(),
        },
        "env": {
            "CHROMA_DB_PATH_raw": os.getenv("CHROMA_DB_PATH", "(not set)"),
            "DOCS_FOLDER_raw":    os.getenv("DOCS_FOLDER",    "(not set)"),
            "GEMINI_API_KEY_set": bool(GEMINI_API_KEY),
            "IS_PRODUCTION":      IS_PRODUCTION,
        },
        "pdf_files": [],
    }

    if docs_path.exists():
        info["pdf_files"] = [f.name for f in docs_path.glob("*.pdf")]

    # Cek writability
    try:
        test_file = db_path / ".write_test"
        db_path.mkdir(parents=True, exist_ok=True)
        test_file.write_text("ok")
        test_file.unlink()
        info["paths"]["chroma_db_writable"] = True
    except Exception as e:
        info["paths"]["chroma_db_writable"] = False
        info["paths"]["chroma_db_write_error"] = str(e)

    if request.method == "GET":
        return jsonify(info)

    # POST: jalankan ingest sinkron
    if not info["pdf_files"]:
        return jsonify({"error": "Tidak ada PDF di folder docs", "info": info}), 400

    try:
        from ingest import run_ingestion
        result = run_ingestion(target_file=info["pdf_files"][0], reset=False)
        return jsonify({"success": True, "result": result, "info": info})
    except Exception as e:
        return jsonify({
            "success": False,
            "error":   str(e),
            "traceback": traceback.format_exc(),
            "info":    info
        }), 500


@app.route("/test-pdf", methods=["GET"])
def test_pdf():
    """
    Diagnosa cepat: tes ekstraksi teks + Gemini Vision OCR pada halaman pertama PDF.
    Gunakan ?page=1 untuk tes halaman tertentu.
    """
    import traceback
    import base64

    page_num = int(request.args.get("page", 1)) - 1  # 0-indexed
    docs_path = Path(DOCS_FOLDER)
    pdf_files  = list(docs_path.glob("*.pdf"))

    if not pdf_files:
        return jsonify({"error": "Tidak ada PDF di docs folder"}), 404

    pdf_path = str(pdf_files[0])
    result   = {"file": pdf_files[0].name, "page_tested": page_num + 1}

    # 1. Test PyMuPDF
    try:
        import fitz
        doc = fitz.open(pdf_path)
        result["total_pages"] = len(doc)
        if page_num < len(doc):
            text = doc[page_num].get_text("text")
            result["pymupdf_chars"] = len(text)
            result["pymupdf_preview"] = text[:300] if text else "(kosong)"
        doc.close()
    except Exception as e:
        result["pymupdf_error"] = str(e)

    # 2. Test Gemini Vision OCR pada halaman pertama
    try:
        import fitz
        from google.genai import types as gtypes
        doc      = fitz.open(pdf_path)
        pg       = doc[min(page_num, len(doc)-1)]
        pix      = pg.get_pixmap(dpi=150)
        img_b64  = base64.b64encode(pix.tobytes("png")).decode()
        img_bytes = pix.tobytes("png")
        doc.close()

        gc = get_genai_client()
        response = gc.models.generate_content(
            model=CHAT_MODEL,
            contents=[
                gtypes.Part(inline_data=gtypes.Blob(data=img_bytes, mime_type="image/png")),
                gtypes.Part(text="Ekstrak semua teks dari halaman ini. Kembalikan hanya teks asli.")
            ]
        )
        ocr_text = (response.text or "").strip()
        result["ocr_chars"]   = len(ocr_text)
        result["ocr_preview"] = ocr_text[:500] if ocr_text else "(kosong — kemungkinan halaman kosong atau gambar tidak terbaca)"
        result["ocr_success"] = len(ocr_text) > 0

    except Exception as e:
        result["ocr_error"]     = str(e)
        result["ocr_traceback"] = traceback.format_exc()
        result["ocr_success"]   = False

    return jsonify(result)


@app.route("/test-embed", methods=["GET"])
def test_embed():
    """Test Gemini embedding API — pastikan model dan API key benar."""
    import traceback
    result = {
        "embedding_model": EMBEDDING_MODEL,
        "chat_model":      CHAT_MODEL,
        "api_key_set":     bool(GEMINI_API_KEY),
        "api_key_prefix":  GEMINI_API_KEY[:8] + "..." if GEMINI_API_KEY else "(kosong)",
    }
    try:
        gc = get_genai_client()
        # Test embedding dengan teks pendek
        emb = gc.models.embed_content(
            model=EMBEDDING_MODEL,
            contents="Test embedding dari APRIS RAG",
            config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT")
        )
        vals = emb.embeddings[0].values
        result["embed_success"]  = True
        result["embed_dims"]     = len(vals)
        result["embed_preview"]  = vals[:5]
        result["message"]        = f"✅ Embedding berhasil! Dimensi: {len(vals)}"
    except Exception as e:
        result["embed_success"]  = False
        result["embed_error"]    = str(e)
        result["embed_traceback"]= traceback.format_exc()
        result["message"]        = f"❌ Embedding gagal: {e}"
    return jsonify(result)


@app.route("/list-models", methods=["GET"])
def list_models():
    """List semua model yang tersedia untuk API key ini."""
    try:
        gc = get_genai_client()
        models = gc.models.list()
        names = sorted([m.name for m in models])
        embed_models = [n for n in names if "embed" in n.lower()]
        chat_models  = [n for n in names if "gemini" in n.lower() and "embed" not in n.lower()]
        return jsonify({
            "api_key_prefix":  GEMINI_API_KEY[:8] + "..." if GEMINI_API_KEY else "(kosong)",
            "total_models":    len(names),
            "embed_models":    embed_models,
            "chat_models":     chat_models[:10],
            "all_models":      names[:30],
        })
    except Exception as e:
        return jsonify({"error": str(e), "note": "API key mungkin tidak valid. Pastikan menggunakan key dari aistudio.google.com/apikey"}), 500


# --- Error Handlers ----------------------------------------------------------

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
