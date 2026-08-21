"""
ingest_md.py — APRIS RAG Markdown Ingestion
=============================================
Proses file .md/.txt dari folder docs/ → ChromaDB

Cara pakai:
    python ingest_md.py          # proses semua .md baru di docs/
    python ingest_md.py --reset  # hapus semua, proses ulang
"""

import os, sys, hashlib, json
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY", "")
EMBEDDING_MODEL   = os.getenv("GEMINI_EMBEDDING_MODEL", "models/text-embedding-004")
CHROMA_DB_PATH    = os.getenv("CHROMA_DB_PATH",         "./vectorstore")
CHROMA_COLLECTION = os.getenv("CHROMA_COLLECTION_NAME", "apris_knowledge")
DOCS_FOLDER       = os.getenv("DOCS_FOLDER",            "./docs")
CHUNK_SIZE        = int(os.getenv("CHUNK_SIZE",   500))
CHUNK_OVERLAP     = int(os.getenv("CHUNK_OVERLAP", 50))
PROCESSED_LOG     = str(Path(CHROMA_DB_PATH) / "processed_md.json")

Path(CHROMA_DB_PATH).mkdir(parents=True, exist_ok=True)

if not GEMINI_API_KEY:
    print("[ERROR] GEMINI_API_KEY tidak ditemukan!")
    sys.exit(1)

import chromadb
from google import genai

chroma = chromadb.PersistentClient(path=CHROMA_DB_PATH)
col    = chroma.get_or_create_collection(
    name=CHROMA_COLLECTION,
    metadata={"hnsw:space": "cosine"}
)
client = genai.Client(api_key=GEMINI_API_KEY)


def load_log() -> dict:
    if Path(PROCESSED_LOG).exists():
        return json.loads(Path(PROCESSED_LOG).read_text())
    return {}


def save_log(log: dict):
    Path(PROCESSED_LOG).write_text(json.dumps(log, indent=2))


def file_hash(path: str) -> str:
    return hashlib.md5(Path(path).read_bytes()).hexdigest()


def chunk_text(text: str) -> list[str]:
    """Bagi teks menjadi chunk-chunk kecil dengan overlap."""
    words   = text.split()
    chunks  = []
    step    = CHUNK_SIZE - CHUNK_OVERLAP
    for i in range(0, len(words), step):
        chunk = " ".join(words[i:i + CHUNK_SIZE])
        if chunk.strip():
            chunks.append(chunk)
    return chunks


def embed(texts: list[str]) -> list[list[float]]:
    """Generate embeddings via Gemini."""
    result = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=texts,
    )
    return [e.values for e in result.embeddings]


def ingest_file(path: Path, log: dict) -> int:
    """Proses satu file .md ke ChromaDB. Return jumlah chunk yang ditambahkan."""
    fhash = file_hash(str(path))
    if log.get(str(path)) == fhash:
        print(f"  [SKIP] {path.name} — tidak berubah")
        return 0

    text = path.read_text(encoding="utf-8", errors="ignore")
    # Hapus frontmatter YAML jika ada
    if text.startswith("---"):
        end = text.find("---", 3)
        if end > 0:
            text = text[end+3:].strip()

    chunks = chunk_text(text)
    if not chunks:
        print(f"  [WARN] {path.name} — tidak ada konten")
        return 0

    print(f"  [INFO] {path.name} -> {len(chunks)} chunk", end="", flush=True)

    # Hapus chunk lama dari file ini
    try:
        existing = col.get(where={"source": path.name})
        if existing["ids"]:
            col.delete(ids=existing["ids"])
    except Exception:
        pass

    # Proses per batch
    BATCH = 10
    added = 0
    for i in range(0, len(chunks), BATCH):
        batch = chunks[i:i+BATCH]
        try:
            vecs = embed(batch)
            ids  = [f"{path.stem}_{i+j}" for j in range(len(batch))]
            metas= [{"source": path.name, "chunk": i+j, "file": str(path)} for j in range(len(batch))]
            col.add(embeddings=vecs, documents=batch, ids=ids, metadatas=metas)
            added += len(batch)
            print(".", end="", flush=True)
        except Exception as e:
            print(f"\n  [ERROR] batch {i}: {e}")

    print(f" ✓ ({added} chunk)")
    log[str(path)] = fhash
    return added


def main():
    import argparse
    parser = argparse.ArgumentParser(description="APRIS RAG Markdown Ingestion")
    parser.add_argument("--reset", action="store_true", help="Hapus semua data lama")
    parser.add_argument("--file",  type=str,            help="Proses satu file saja")
    args = parser.parse_args()

    if args.reset:
        print("[RESET] Menghapus semua data lama...")
        chroma.delete_collection(CHROMA_COLLECTION)
        global col
        col = chroma.get_or_create_collection(CHROMA_COLLECTION, metadata={"hnsw:space":"cosine"})
        if Path(PROCESSED_LOG).exists():
            Path(PROCESSED_LOG).unlink()
        print("[RESET] Selesai.")

    log = load_log()

    if args.file:
        target = Path(DOCS_FOLDER) / args.file
        if not target.exists():
            print(f"[ERROR] File tidak ditemukan: {target}")
            sys.exit(1)
        files = [target]
    else:
        files = sorted(Path(DOCS_FOLDER).glob("*.md")) + sorted(Path(DOCS_FOLDER).glob("*.txt"))
        # Kecualikan README
        files = [f for f in files if f.name.lower() != "readme.md"]

    if not files:
        print(f"[INFO] Tidak ada file .md di {DOCS_FOLDER}")
        sys.exit(0)

    print(f"\nAPRIS RAG Ingestion ({len(files)} file)")
    print(f"  DB   : {CHROMA_DB_PATH}")
    print(f"  Model: {EMBEDDING_MODEL}")
    print(f"  Chunk: {CHUNK_SIZE} kata, overlap {CHUNK_OVERLAP}")
    print("-" * 40)

    total = 0
    for f in files:
        total += ingest_file(f, log)

    save_log(log)
    count = col.count()
    print("-" * 40)
    print(f"Selesai! +{total} chunk baru | Total DB: {count} chunk")


if __name__ == "__main__":
    main()
