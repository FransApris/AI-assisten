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

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
EMBEDDING_MODEL = os.getenv("GEMINI_EMBEDDING_MODEL", "models/gemini-embedding-001")
CHROMA_DB_PATH = str(BASE_DIR.parent / "rag-knowledge" / "vectorstore")
CHROMA_COLLECTION = os.getenv("CHROMA_COLLECTION_NAME", "apris_knowledge")
PROCESSED_LOG = str(Path(CHROMA_DB_PATH) / "processed_drive.json")
CHUNK_SIZE = 500
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

if __name__ == "__main__":
    ingest_drive_files()
