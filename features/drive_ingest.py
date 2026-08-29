import os
import io
import sys
import json
from pathlib import Path
from dotenv import load_dotenv

# Path handling
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))

# Load .env
load_dotenv(BASE_DIR / ".env", override=True)
load_dotenv(BASE_DIR.parent / "rag-knowledge" / ".env", override=True)

from googleapiclient.http import MediaIoBaseDownload
from PyPDF2 import PdfReader
import google_drive

import chromadb
from google import genai

GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY", "")
EMBEDDING_MODEL   = os.getenv("GEMINI_EMBEDDING_MODEL", "models/gemini-embedding-001")
CHROMA_COLLECTION = os.getenv("CHROMA_COLLECTION_NAME", "apris_knowledge")

# Path vectorstore:
#   1. RAG_DB_PATH env var (prioritas tertinggi)
#   2. /tmp/vectorstore (Railway — ephemeral, diisi ulang dari Drive tiap startup)
#   3. ../rag-knowledge/vectorstore (lokal)
_IS_RAILWAY = bool(os.getenv("RAILWAY_ENVIRONMENT"))
_LOCAL_PATH = str(BASE_DIR.parent / "rag-knowledge" / "vectorstore")
CHROMA_DB_PATH = os.getenv("RAG_DB_PATH", "/tmp/vectorstore" if _IS_RAILWAY else _LOCAL_PATH)

PROCESSED_LOG = str(Path(CHROMA_DB_PATH) / "processed_drive.json")
CHUNK_SIZE    = 500
CHUNK_OVERLAP = 50

def load_log() -> dict:
    if Path(PROCESSED_LOG).exists():
        return json.loads(Path(PROCESSED_LOG).read_text())
    return {}

def save_log(log: dict):
    Path(PROCESSED_LOG).write_text(json.dumps(log, indent=2))

def chunk_text(text: str) -> list[str]:
    words = text.split()
    chunks = []
    step = CHUNK_SIZE - CHUNK_OVERLAP
    for i in range(0, len(words), step):
        chunk = " ".join(words[i:i + CHUNK_SIZE])
        if chunk.strip():
            chunks.append(chunk)
    return chunks

def download_file_text(service, file_id, file_name, mime_type):
    """Download and extract text based on mimeType."""
    text = ""
    try:
        if 'application/vnd.google-apps.document' in mime_type:
            # Export Google Docs as plain text
            request = service.files().export_media(fileId=file_id, mimeType='text/plain')
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            text = fh.getvalue().decode('utf-8', errors='ignore')
            
        elif 'application/pdf' in mime_type:
            # Download PDF
            request = service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            fh.seek(0)
            
            # Parse PDF
            reader = PdfReader(fh)
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
                    
        elif 'text/plain' in mime_type or 'text/markdown' in mime_type:
            # Download normal text file
            request = service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            text = fh.getvalue().decode('utf-8', errors='ignore')
            
        else:
            print(f"  [SKIP] {file_name} — tipe file tidak didukung ({mime_type})")
    except Exception as e:
        print(f"  [ERROR] Gagal mengunduh {file_name}: {e}")
        
    return text

def ingest_drive_files():
    if not GEMINI_API_KEY:
        print("[ERROR] GEMINI_API_KEY tidak ditemukan!")
        return
        
    try:
        service = google_drive.get_drive_service()
        brain_id = google_drive.get_apris_brain_id(service)
        if not brain_id:
            print("[ERROR] Folder APRIS_Brain belum ada.")
            return
            
        # Get Knowledge_Base folder ID
        res = service.files().list(
            q=f"name='Knowledge_Base' and '{brain_id}' in parents and mimeType='application/vnd.google-apps.folder'",
            spaces='drive',
            fields="files(id, name)"
        ).execute()
        
        kb_items = res.get('files', [])
        if not kb_items:
            print("[ERROR] Folder Knowledge_Base belum ada di dalam APRIS_Brain.")
            return
            
        kb_id = kb_items[0]['id']
        
        # Get all files in Knowledge_Base
        files_res = service.files().list(
            q=f"'{kb_id}' in parents and mimeType != 'application/vnd.google-apps.folder'",
            spaces='drive',
            fields="files(id, name, mimeType, md5Checksum, modifiedTime)"
        ).execute()
        
        files = files_res.get('files', [])
        if not files:
            print("[INFO] Tidak ada file dokumen di Knowledge_Base Drive Anda.")
            return
            
    except Exception as e:
        print(f"[ERROR] Gagal mengakses Google Drive: {e}")
        return

    # Inisialisasi DB
    Path(CHROMA_DB_PATH).mkdir(parents=True, exist_ok=True)
    chroma = chromadb.PersistentClient(path=CHROMA_DB_PATH)
    col = chroma.get_or_create_collection(name=CHROMA_COLLECTION, metadata={"hnsw:space": "cosine"})
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    log = load_log()
    total_added = 0
    
    print(f"\nAPRIS Drive Ingestion ({len(files)} file ditemukan)")
    print("-" * 40)
    
    for f in files:
        fid = f['id']
        fname = f['name']
        mtype = f['mimeType']
        
        # Gunakan kombinasi ID dan modifiedTime sebagai hash unik, atau md5Checksum
        fhash = f.get('md5Checksum', f.get('modifiedTime', fid))
        
        if log.get(fid) == fhash:
            print(f"  [SKIP] {fname} — tidak berubah")
            continue
            
        text = download_file_text(service, fid, fname, mtype)
        if not text.strip():
            continue
            
        chunks = chunk_text(text)
        if not chunks:
            continue
            
        print(f"  [INFO] {fname} -> {len(chunks)} chunk", end="", flush=True)
        
        # Hapus chunk lama file ini
        try:
            existing = col.get(where={"source": fname})
            if existing["ids"]:
                col.delete(ids=existing["ids"])
        except Exception:
            pass
            
        # Ingest
        BATCH = 10
        added = 0
        for i in range(0, len(chunks), BATCH):
            batch = chunks[i:i+BATCH]
            try:
                result = client.models.embed_content(model=EMBEDDING_MODEL, contents=batch)
                vecs = [e.values for e in result.embeddings]
                ids = [f"drive_{fid}_{i+j}" for j in range(len(batch))]
                metas = [{"source": fname, "chunk": i+j, "type": "drive"} for j in range(len(batch))]
                col.add(embeddings=vecs, documents=batch, ids=ids, metadatas=metas)
                added += len(batch)
                print(".", end="", flush=True)
            except Exception as e:
                print(f"\n  [ERROR] batch {i}: {e}")
                
        print(f" [OK] ({added} chunk)")
        log[fid] = fhash
        total_added += added
        
    save_log(log)
    print("-" * 40)
    print(f"Selesai! +{total_added} chunk baru | Total DB: {col.count()} chunk")
    return total_added


def get_chroma_collection():
    """Mengembalikan objek koleksi ChromaDB."""
    Path(CHROMA_DB_PATH).mkdir(parents=True, exist_ok=True)
    chroma = chromadb.PersistentClient(path=CHROMA_DB_PATH)
    return chroma.get_or_create_collection(name=CHROMA_COLLECTION, metadata={"hnsw:space": "cosine"})


def ingest_file_bytes(file_bytes: bytes, filename: str) -> dict:
    """
    Ingest file bytes secara langsung (upload dari WA).
    Mengembalikan dict berisi pesan status.
    """
    if not GEMINI_API_KEY:
        return {"success": False, "message": "Gagal ingest: GEMINI_API_KEY tidak dikonfigurasi"}

    fname_lower = filename.lower()
    text = ""
    
    try:
        if fname_lower.endswith(".pdf"):
            from features.pdf_ocr import extract_pdf_text
            client = genai.Client(api_key=GEMINI_API_KEY)
            res = extract_pdf_text(file_bytes, filename, gemini_client=client)
            if res.get("error"):
                return {"success": False, "message": f"Gagal baca PDF: {res['error']}"}
            text = res.get("text", "")
        elif fname_lower.endswith((".txt", ".md", ".csv", ".json")):
            text = file_bytes.decode("utf-8", errors="ignore")
        else:
            return {"success": False, "message": f"Tipe file tidak didukung untuk KB: {filename}"}
    except Exception as e:
        return {"success": False, "message": f"Gagal mengekstrak teks: {e}"}

    if not text.strip():
        return {"success": False, "message": f"Dokumen kosong atau teks tidak bisa dibaca: {filename}"}

    chunks = chunk_text(text)
    if not chunks:
        return {"success": False, "message": f"Dokumen terlalu pendek untuk masuk KB: {filename}"}

    try:
        col = get_chroma_collection()
        client = genai.Client(api_key=GEMINI_API_KEY)
        
        # Hapus versi lama jika ada
        try:
            existing = col.get(where={"source": filename})
            if existing["ids"]:
                col.delete(ids=existing["ids"])
        except Exception:
            pass

        # Embed dan Ingest
        BATCH = 10
        added = 0
        for i in range(0, len(chunks), BATCH):
            batch = chunks[i:i+BATCH]
            result = client.models.embed_content(model=EMBEDDING_MODEL, contents=batch)
            vecs = [e.values for e in result.embeddings]
            import hashlib
            ids = [f"wa_{hashlib.md5(filename.encode()).hexdigest()[:6]}_{i+j}" for j in range(len(batch))]
            metas = [{"source": filename, "chunk": i+j, "type": "wa_upload"} for j in range(len(batch))]
            col.add(embeddings=vecs, documents=batch, ids=ids, metadatas=metas)
            added += len(batch)

        return {"success": True, "message": f"✅ Berhasil ingest *{filename}* (+{added} chunk)"}
    
    except Exception as e:
        return {"success": False, "message": f"Gagal menyimpan ke DB: {e}"}


def get_kb_status() -> dict:
    """
    Ambil statistik knowledge base dari ChromaDB.
    Digunakan oleh admin command /kb-status.

    Returns dict:
        {
          "total_chunks": int,
          "sources": [{"name": str, "chunks": int, "type": str}],
          "drive_files": int,   # jumlah file dari Google Drive
          "wa_files": int,      # jumlah file yang diupload via WA
          "error": str | None,
        }
    """
    try:
        col = get_chroma_collection()
        total = col.count()

        if total == 0:
            return {
                "total_chunks": 0,
                "sources"     : [],
                "drive_files" : 0,
                "wa_files"    : 0,
                "error"       : None,
            }

        # Ambil semua metadata (batasi 2000 untuk performa)
        result   = col.get(limit=min(total, 2000), include=["metadatas"])
        metas    = result.get("metadatas", [])

        # Hitung per sumber
        source_counts: dict = {}
        source_types : dict = {}
        for m in metas:
            src  = m.get("source", "unknown")
            typ  = m.get("type", "unknown")
            source_counts[src]  = source_counts.get(src, 0) + 1
            source_types[src]   = typ

        # Urutkan: terbanyak chunk di atas
        sources = sorted(
            [
                {"name": src, "chunks": cnt, "type": source_types.get(src, "?")}
                for src, cnt in source_counts.items()
            ],
            key=lambda x: x["chunks"],
            reverse=True,
        )

        drive_files = sum(1 for s in sources if s["type"] == "drive")
        wa_files    = sum(1 for s in sources if s["type"] == "wa_upload")

        return {
            "total_chunks": total,
            "sources"     : sources,
            "drive_files" : drive_files,
            "wa_files"    : wa_files,
            "error"       : None,
        }

    except Exception as e:
        return {
            "total_chunks": 0,
            "sources"     : [],
            "drive_files" : 0,
            "wa_files"    : 0,
            "error"       : str(e),
        }


def format_kb_status_message(status: dict) -> str:
    """Format dict status KB menjadi pesan WA yang rapi untuk admin."""
    if status.get("error"):
        return f"❌ Gagal mengambil status KB: {status['error']}"

    total  = status["total_chunks"]
    lines  = [
        "📚 *STATUS KNOWLEDGE BASE APRIS*",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        f"📊 Total chunk    : *{total:,}*",
        f"📂 Dari Google Drive : *{status['drive_files']}* file",
        f"📎 Dari WA upload    : *{status['wa_files']}* file",
        "",
    ]

    if status["sources"]:
        lines.append("📄 *Daftar Dokumen:*")
        for i, src in enumerate(status["sources"][:15], 1):
            icon = "📂" if src["type"] == "drive" else "📎"
            lines.append(f"  {i}. {icon} {src['name']} ({src['chunks']} chunk)")
        if len(status["sources"]) > 15:
            lines.append(f"  _...dan {len(status['sources']) - 15} dokumen lainnya_")
    else:
        lines.append("_Knowledge base masih kosong._")

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        "_Ketik `/ingest-kb` untuk re-ingest dari Drive_",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    ingest_drive_files()
