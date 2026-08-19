"""
ingest.py — APRIS RAG Knowledge Base Ingestion
================================================
Proses: PDF -> Ekstrak Teks -> Chunking -> Gemini Embedding -> ChromaDB

Cara pakai:
    python ingest.py                      # proses semua PDF baru di docs/
    python ingest.py --reset              # hapus database lama, proses ulang semua
    python ingest.py --file namafile.pdf  # proses satu file spesifik
"""

import os
import sys
import argparse
import hashlib
import json
from pathlib import Path
from datetime import datetime

import pdfplumber
import chromadb
from google import genai
from google.genai import types
from dotenv import load_dotenv
from tqdm import tqdm
from langdetect import detect, LangDetectException

# --- Init --------------------------------------------------------------------
load_dotenv()

IS_PRODUCTION    = os.getenv("RAILWAY_ENVIRONMENT") is not None
GEMINI_API_KEY   = os.getenv("GEMINI_API_KEY", "")
EMBEDDING_MODEL  = os.getenv("GEMINI_EMBEDDING_MODEL", "models/text-embedding-004")

DEFAULT_DB_PATH  = "/data/vectorstore" if IS_PRODUCTION else "./vectorstore"
DEFAULT_DOCS     = "/data/docs"        if IS_PRODUCTION else "./docs"

CHROMA_DB_PATH   = os.getenv("CHROMA_DB_PATH",        DEFAULT_DB_PATH).strip()
CHROMA_COLLECTION= os.getenv("CHROMA_COLLECTION_NAME", "apris_knowledge").strip()
DOCS_FOLDER      = os.getenv("DOCS_FOLDER",            DEFAULT_DOCS).strip()
CHUNK_SIZE       = int(os.getenv("CHUNK_SIZE",  500))
CHUNK_OVERLAP    = int(os.getenv("CHUNK_OVERLAP", 50))
PROCESSED_LOG    = str(Path(CHROMA_DB_PATH) / "processed_files.json")

# Buat folder jika belum ada
Path(CHROMA_DB_PATH).mkdir(parents=True, exist_ok=True)
Path(DOCS_FOLDER).mkdir(parents=True, exist_ok=True)

if not GEMINI_API_KEY:
    print("[ERROR] GEMINI_API_KEY tidak ditemukan! Set di environment variable.")
    sys.exit(1)

client = genai.Client(api_key=GEMINI_API_KEY)

# --- Helper ------------------------------------------------------------------

def log(msg, level="info"):
    prefix = {"info": "[INFO]", "ok": "[ OK ]", "warn": "[WARN]", "error": "[ERR ]"}.get(level, "[    ]")
    print(f"{prefix} {msg}", flush=True)


def file_hash(filepath: str) -> str:
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def load_processed_log() -> dict:
    if os.path.exists(PROCESSED_LOG):
        try:
            with open(PROCESSED_LOG, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_processed_log(log_data: dict):
    Path(PROCESSED_LOG).parent.mkdir(parents=True, exist_ok=True)
    with open(PROCESSED_LOG, "w", encoding="utf-8") as f:
        json.dump(log_data, f, indent=2, ensure_ascii=False)


def detect_language(text: str) -> str:
    try:
        return detect(text[:500].strip()) if text.strip() else "unknown"
    except LangDetectException:
        return "unknown"


# --- PDF Extraction ----------------------------------------------------------

def extract_text_from_pdf(pdf_path: str) -> list[dict]:
    """
    Ekstrak teks dari PDF.
    Coba PyMuPDF (fitz) dulu — lebih andal untuk banyak jenis PDF.
    Fallback ke pdfplumber jika gagal.
    """
    pages = []

    # === Metode 1: PyMuPDF (fitz) ===
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(pdf_path)
        total = len(doc)
        log(f"  PyMuPDF: membaca {total} halaman...", "info")
        for i in range(total):
            page = doc[i]
            text = page.get_text("text")
            if text and text.strip():
                pages.append({
                    "page_num":    i + 1,
                    "total_pages": total,
                    "text":        text.strip(),
                    "char_count":  len(text)
                })
        doc.close()

        if pages:
            log(f"  PyMuPDF berhasil: {len(pages)} halaman dengan teks.", "ok")
            return pages
        else:
            log("  PyMuPDF: tidak ada teks terdeteksi — coba pdfplumber...", "warn")

    except Exception as e:
        log(f"  PyMuPDF gagal: {e} — coba pdfplumber...", "warn")

    # === Metode 2: pdfplumber (fallback) ===
    try:
        with pdfplumber.open(pdf_path) as pdf:
            total = len(pdf.pages)
            log(f"  pdfplumber: membaca {total} halaman...", "info")
            for i, page in enumerate(pdf.pages):
                text = page.extract_text()
                if text and text.strip():
                    pages.append({
                        "page_num":    i + 1,
                        "total_pages": total,
                        "text":        text.strip(),
                        "char_count":  len(text)
                    })

        if pages:
            log(f"  pdfplumber berhasil: {len(pages)} halaman dengan teks.", "ok")
        else:
            log("  pdfplumber: tidak ada teks! PDF mungkin berformat gambar/scan.", "error")
            log("  Solusi: gunakan PDF yang bisa di-select teksnya, bukan hasil scan.", "error")

    except Exception as e:
        log(f"  pdfplumber gagal: {e}", "error")

    return pages


# --- Chunking ----------------------------------------------------------------

def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    words  = text.split()
    chunks = []
    start  = 0
    while start < len(words):
        end   = min(start + chunk_size, len(words))
        chunk = " ".join(words[start:end])
        if chunk.strip():
            chunks.append(chunk)
        if end == len(words):
            break
        start += chunk_size - overlap
    return chunks


# --- Embedding ---------------------------------------------------------------

def get_embedding(text: str) -> list[float]:
    """Buat embedding menggunakan Gemini text-embedding-004 (SDK baru)."""
    result = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=text,
        config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT")
    )
    return result.embeddings[0].values


# --- ChromaDB ----------------------------------------------------------------

def get_chroma_collection(reset: bool = False):
    chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
    if reset:
        try:
            chroma_client.delete_collection(CHROMA_COLLECTION)
            log("Database lama dihapus.", "warn")
        except Exception:
            pass
    collection = chroma_client.get_or_create_collection(
        name=CHROMA_COLLECTION,
        metadata={"description": "APRIS Knowledge Base dari dokumen PDF"}
    )
    return collection


# --- Main Ingestion ----------------------------------------------------------

def ingest_pdf(pdf_path: str, collection, processed_log: dict, force: bool = False) -> int:
    filename = os.path.basename(pdf_path)
    file_md5 = file_hash(pdf_path)

    if not force and filename in processed_log:
        if processed_log[filename].get("hash") == file_md5:
            log(f"Dilewati (tidak berubah): {filename}", "info")
            return 0

    log(f"Memproses: {filename}", "info")

    # 1. Ekstrak teks
    pages = extract_text_from_pdf(pdf_path)
    if not pages:
        log(f"Tidak ada teks dari: {filename}", "error")
        return 0

    total_chars = sum(p["char_count"] for p in pages)
    lang        = detect_language(pages[0]["text"])
    log(f"  Halaman: {len(pages)} | Karakter: {total_chars:,} | Bahasa: {lang}", "info")

    # 2. Chunking
    all_chunks = []
    for page in pages:
        for j, chunk in enumerate(chunk_text(page["text"])):
            all_chunks.append({
                "text":        chunk,
                "page_num":    page["page_num"],
                "chunk_index": j,
                "source":      filename,
                "language":    lang
            })
    log(f"  Chunks dibuat: {len(all_chunks)}", "info")

    # 3. Embedding & simpan ke ChromaDB
    ids        = []
    embeddings = []
    documents  = []
    metadatas  = []

    for i, chunk in enumerate(all_chunks):
        chunk_id = f"{file_md5[:8]}_{chunk['page_num']}_{chunk['chunk_index']}"
        try:
            emb = get_embedding(chunk["text"])
        except Exception as e:
            log(f"  Embedding gagal chunk {i}: {e}", "error")
            continue

        ids.append(chunk_id)
        embeddings.append(emb)
        documents.append(chunk["text"])
        metadatas.append({
            "source":      chunk["source"],
            "page_num":    chunk["page_num"],
            "chunk_index": chunk["chunk_index"],
            "language":    chunk["language"],
            "file_hash":   file_md5,
            "ingested_at": datetime.now().isoformat()
        })

        # Progress setiap 10 chunk
        if (i + 1) % 10 == 0 or (i + 1) == len(all_chunks):
            print(f"  Embedding: {i+1}/{len(all_chunks)}...", flush=True)

    if not ids:
        log(f"Tidak ada embedding yang berhasil dibuat untuk {filename}", "error")
        return 0

    # Batch upsert ke ChromaDB
    batch_size = 50
    for start in range(0, len(ids), batch_size):
        end = start + batch_size
        collection.upsert(
            ids=ids[start:end],
            embeddings=embeddings[start:end],
            documents=documents[start:end],
            metadatas=metadatas[start:end]
        )

    # 4. Update log
    processed_log[filename] = {
        "hash":        file_md5,
        "pages":       len(pages),
        "chunks":      len(ids),
        "language":    lang,
        "ingested_at": datetime.now().isoformat(),
        "path":        str(pdf_path)
    }
    log(f"Selesai: {len(ids)} chunks disimpan dari {filename}", "ok")
    return len(ids)


def run_ingestion(target_file: str = None, reset: bool = False) -> dict:
    """Entry point untuk proses ingestion. Returns summary dict."""
    print("\n" + "="*55, flush=True)
    print("  APRIS RAG - PDF Ingestion", flush=True)
    print("="*55 + "\n", flush=True)

    collection    = get_chroma_collection(reset=reset)
    processed_log = {} if reset else load_processed_log()

    docs_path = Path(DOCS_FOLDER)
    docs_path.mkdir(exist_ok=True)

    if target_file:
        pdf_files = [docs_path / target_file]
        if not pdf_files[0].exists():
            log(f"File tidak ditemukan: {target_file}", "error")
            return {"success": False, "error": f"File tidak ditemukan: {target_file}"}
    else:
        pdf_files = sorted(docs_path.glob("*.pdf"))

    if not pdf_files:
        log("Tidak ada file PDF ditemukan.", "warn")
        return {"success": True, "total_chunks": 0, "files": 0}

    log(f"Folder dokumen : {docs_path.resolve()}", "info")
    log(f"Vector DB      : {Path(CHROMA_DB_PATH).resolve()}", "info")
    log(f"Total PDF      : {len(pdf_files)} file\n", "info")

    total_chunks = 0
    results      = []
    for pdf_path in pdf_files:
        n = ingest_pdf(str(pdf_path), collection, processed_log, force=reset)
        total_chunks += n
        results.append({"file": pdf_path.name, "chunks": n})

    save_processed_log(processed_log)

    doc_count = collection.count()
    print(f"\n{'='*55}", flush=True)
    log(f"Ingestion selesai!", "ok")
    log(f"Chunks baru    : {total_chunks}", "info")
    log(f"Total di DB    : {doc_count}", "info")
    print("="*55 + "\n", flush=True)

    return {
        "success":      True,
        "total_chunks": total_chunks,
        "db_total":     doc_count,
        "files":        len(pdf_files),
        "results":      results
    }


# --- CLI ---------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="APRIS RAG - Ingest PDF ke ChromaDB")
    parser.add_argument("--file",  "-f", type=str,   default=None,  help="Nama file PDF spesifik")
    parser.add_argument("--reset", "-r", action="store_true", default=False, help="Reset database")
    args = parser.parse_args()
    result = run_ingestion(target_file=args.file, reset=args.reset)
    sys.exit(0 if result.get("success") else 1)
