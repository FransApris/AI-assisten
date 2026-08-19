"""
ingest.py — APRIS RAG Knowledge Base Ingestion
================================================
Proses: PDF → Ekstrak Teks → Chunking → Gemini Embedding → ChromaDB

Cara pakai:
    python ingest.py                    # proses semua PDF baru di docs/
    python ingest.py --reset            # hapus database lama, proses ulang semua
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
import google.generativeai as genai
from dotenv import load_dotenv
from tqdm import tqdm
from colorama import init, Fore, Style
from langdetect import detect, LangDetectException

# ─── Init ────────────────────────────────────────────────────────────────────
init(autoreset=True)
load_dotenv()

GEMINI_API_KEY       = os.getenv("GEMINI_API_KEY")
EMBEDDING_MODEL      = os.getenv("GEMINI_EMBEDDING_MODEL", "models/embedding-001")
CHROMA_DB_PATH       = os.getenv("CHROMA_DB_PATH", "./vectorstore")
CHROMA_COLLECTION    = os.getenv("CHROMA_COLLECTION_NAME", "apris_knowledge")
DOCS_FOLDER          = os.getenv("DOCS_FOLDER", "./docs")
CHUNK_SIZE           = int(os.getenv("CHUNK_SIZE", 500))
CHUNK_OVERLAP        = int(os.getenv("CHUNK_OVERLAP", 50))
PROCESSED_LOG        = "./vectorstore/processed_files.json"

genai.configure(api_key=GEMINI_API_KEY)

# ─── Helper ───────────────────────────────────────────────────────────────────

def print_header():
    print(f"\n{Fore.CYAN}{'═'*55}")
    print(f"{Fore.CYAN}  APRIS RAG — PDF Ingestion")
    print(f"{Fore.CYAN}  Gemini Embedding  |  ChromaDB Storage")
    print(f"{Fore.CYAN}{'═'*55}{Style.RESET_ALL}\n")


def file_hash(filepath: str) -> str:
    """Hitung MD5 hash file untuk deteksi perubahan."""
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def load_processed_log() -> dict:
    """Load log file yang sudah diproses."""
    if os.path.exists(PROCESSED_LOG):
        with open(PROCESSED_LOG, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_processed_log(log: dict):
    """Simpan log file yang sudah diproses."""
    os.makedirs(os.path.dirname(PROCESSED_LOG), exist_ok=True)
    with open(PROCESSED_LOG, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)


def detect_language(text: str) -> str:
    """Deteksi bahasa teks (id/en/other)."""
    try:
        sample = text[:500].strip()
        if not sample:
            return "unknown"
        lang = detect(sample)
        return lang
    except LangDetectException:
        return "unknown"


# ─── PDF Extraction ───────────────────────────────────────────────────────────

def extract_text_from_pdf(pdf_path: str) -> list[dict]:
    """
    Ekstrak teks dari PDF per halaman.
    Returns: list of {page_num, text, char_count}
    """
    pages = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            total = len(pdf.pages)
            for i, page in enumerate(pdf.pages):
                text = page.extract_text()
                if text and text.strip():
                    pages.append({
                        "page_num": i + 1,
                        "total_pages": total,
                        "text": text.strip(),
                        "char_count": len(text)
                    })
    except Exception as e:
        print(f"{Fore.RED}  ✗ Gagal baca PDF: {e}")
    return pages


# ─── Chunking ─────────────────────────────────────────────────────────────────

def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """
    Potong teks menjadi chunks dengan overlap.
    Chunk berdasarkan kata, bukan karakter.
    """
    words = text.split()
    chunks = []
    start = 0
    while start < len(words):
        end = min(start + chunk_size, len(words))
        chunk = " ".join(words[start:end])
        if chunk.strip():
            chunks.append(chunk)
        if end == len(words):
            break
        start += chunk_size - overlap
    return chunks


# ─── Embedding ────────────────────────────────────────────────────────────────

def get_embedding(text: str) -> list[float]:
    """Buat embedding menggunakan Gemini embedding-001."""
    result = genai.embed_content(
        model=EMBEDDING_MODEL,
        content=text,
        task_type="retrieval_document"
    )
    return result["embedding"]


# ─── ChromaDB ────────────────────────────────────────────────────────────────

def get_chroma_collection(reset: bool = False):
    """Inisialisasi ChromaDB collection."""
    client = chromadb.PersistentClient(path=CHROMA_DB_PATH)

    if reset:
        try:
            client.delete_collection(CHROMA_COLLECTION)
            print(f"{Fore.YELLOW}  ⚠ Database lama dihapus.")
        except Exception:
            pass

    collection = client.get_or_create_collection(
        name=CHROMA_COLLECTION,
        metadata={"description": "APRIS Knowledge Base dari dokumen PDF"}
    )
    return collection


# ─── Main Ingestion ───────────────────────────────────────────────────────────

def ingest_pdf(pdf_path: str, collection, processed_log: dict, force: bool = False) -> int:
    """
    Proses satu file PDF ke dalam ChromaDB.
    Returns: jumlah chunks yang ditambahkan
    """
    filename  = os.path.basename(pdf_path)
    file_md5  = file_hash(pdf_path)

    # Skip jika sudah diproses & tidak berubah
    if not force and filename in processed_log:
        if processed_log[filename].get("hash") == file_md5:
            print(f"{Fore.YELLOW}  ↷ Dilewati (sudah diproses): {filename}")
            return 0

    print(f"\n{Fore.CYAN}  📄 Memproses: {filename}")

    # 1. Ekstrak teks
    pages = extract_text_from_pdf(pdf_path)
    if not pages:
        print(f"{Fore.RED}  ✗ Tidak ada teks yang bisa diekstrak dari {filename}")
        return 0

    total_chars = sum(p["char_count"] for p in pages)
    lang        = detect_language(pages[0]["text"])
    print(f"     Halaman: {len(pages)} | Karakter: {total_chars:,} | Bahasa: {lang}")

    # 2. Chunking
    all_chunks = []
    for page in pages:
        chunks = chunk_text(page["text"])
        for j, chunk in enumerate(chunks):
            all_chunks.append({
                "text": chunk,
                "page_num": page["page_num"],
                "chunk_index": j,
                "source": filename,
                "language": lang
            })

    print(f"     Chunks dibuat: {len(all_chunks)}")

    # 3. Embedding & simpan ke ChromaDB
    ids        = []
    embeddings = []
    documents  = []
    metadatas  = []

    print(f"     Membuat embeddings...", end="", flush=True)
    for i, chunk in enumerate(tqdm(all_chunks, desc="     ", leave=False, ncols=60)):
        chunk_id = f"{file_md5[:8]}_{chunk['page_num']}_{chunk['chunk_index']}"
        emb      = get_embedding(chunk["text"])

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
        "chunks":      len(all_chunks),
        "language":    lang,
        "ingested_at": datetime.now().isoformat(),
        "path":        str(pdf_path)
    }

    print(f"\n{Fore.GREEN}  ✓ Selesai: {len(all_chunks)} chunks disimpan dari {filename}")
    return len(all_chunks)


def run_ingestion(target_file: str = None, reset: bool = False):
    """Entry point untuk proses ingestion."""
    print_header()

    # Init ChromaDB
    collection    = get_chroma_collection(reset=reset)
    processed_log = {} if reset else load_processed_log()

    # Tentukan file yang akan diproses
    docs_path = Path(DOCS_FOLDER)
    docs_path.mkdir(exist_ok=True)

    if target_file:
        pdf_files = [docs_path / target_file]
        if not pdf_files[0].exists():
            print(f"{Fore.RED}  ✗ File tidak ditemukan: {target_file}")
            sys.exit(1)
    else:
        pdf_files = sorted(docs_path.glob("*.pdf"))

    if not pdf_files:
        print(f"{Fore.YELLOW}  ⚠ Tidak ada file PDF ditemukan di folder: {DOCS_FOLDER}")
        print(f"  → Letakkan file PDF ke folder: {docs_path.resolve()}")
        return

    print(f"  📁 Folder dokumen : {docs_path.resolve()}")
    print(f"  🗄  Vector DB      : {Path(CHROMA_DB_PATH).resolve()}")
    print(f"  📋 Total PDF      : {len(pdf_files)} file\n")

    # Proses semua PDF
    total_chunks = 0
    for pdf_path in pdf_files:
        total_chunks += ingest_pdf(str(pdf_path), collection, processed_log, force=reset)

    save_processed_log(processed_log)

    # Summary
    print(f"\n{Fore.CYAN}{'─'*55}")
    doc_count = collection.count()
    print(f"{Fore.GREEN}  ✅ Ingestion selesai!")
    print(f"  Total chunks baru    : {total_chunks}")
    print(f"  Total chunks di DB   : {doc_count}")
    print(f"  Database tersimpan di: {Path(CHROMA_DB_PATH).resolve()}")
    print(f"{Fore.CYAN}{'─'*55}\n")


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="APRIS RAG — Ingest PDF ke ChromaDB"
    )
    parser.add_argument(
        "--file", "-f",
        type=str,
        default=None,
        help="Nama file PDF spesifik (misal: buku.pdf)"
    )
    parser.add_argument(
        "--reset", "-r",
        action="store_true",
        default=False,
        help="Reset database dan proses ulang semua PDF"
    )
    args = parser.parse_args()
    run_ingestion(target_file=args.file, reset=args.reset)
