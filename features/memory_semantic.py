"""
features/memory_semantic.py — Tier-2 Semantic Memory via ChromaDB
==================================================================
Memory semantik: simpan & retrieve snippets percakapan penting
berdasarkan similarity, bukan exact match.

Tier 1 (episodic)   → memory.py     → JSON flat list
Tier 2 (semantic)   → memory_semantic.py → ChromaDB apris_memory

Penggunaan:
    memory_semantic.store_memory(text, source="user")
    memory_semantic.retrieve_memory(query, top_k=3)
"""
import os
import uuid
import threading
from datetime import datetime
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo(os.getenv("TZ", "Asia/Jakarta"))
except Exception:
    from datetime import timezone, timedelta
    _TZ = timezone(timedelta(hours=7))

# Config
_BASE_DIR    = Path(__file__).resolve().parent.parent
_DB_PATH     = os.getenv("RAG_DB_PATH",
               str(_BASE_DIR.parent / "rag-knowledge" / "vectorstore"))
_COLLECTION  = "apris_memory"          # koleksi berbeda dari knowledge base
_EMB_MODEL   = os.getenv("GEMINI_EMBEDDING_MODEL", "models/gemini-embedding-001")
_API_KEY     = os.getenv("GEMINI_API_KEY", "")
_lock        = threading.Lock()
_col         = None
_genai_client = None   # Cached Gemini client


def _get_genai_client():
    """Lazy-init Gemini client (cached, bukan buat baru setiap kali)."""
    global _genai_client
    if _genai_client is None:
        from google import genai
        _genai_client = genai.Client(api_key=_API_KEY)
    return _genai_client


def _get_collection():
    global _col
    if _col is not None:
        return _col
    try:
        import chromadb
        client = chromadb.PersistentClient(path=_DB_PATH)
        _col   = client.get_or_create_collection(
            name=_COLLECTION,
            metadata={"hnsw:space": "cosine"}
        )
        return _col
    except Exception as e:
        print(f"[SemanticMemory] ChromaDB tidak tersedia: {e}")
        return None


def _embed(text: str) -> list:
    """Buat embedding untuk teks menggunakan cached client."""
    client = _get_genai_client()
    result = client.models.embed_content(
        model=_EMB_MODEL,
        contents=text
    )
    return result.embeddings[0].values


def store_memory(text: str, source: str = "conversation") -> bool:
    """
    Simpan snippet ke semantic memory.
    Return True jika berhasil.
    """
    if not _API_KEY or not text.strip():
        return False
    col = _get_collection()
    if col is None:
        return False
    try:
        with _lock:
            vec = _embed(text)
            mid = f"mem_{uuid.uuid4().hex[:12]}"
            col.add(
                embeddings=[vec],
                documents=[text],
                ids=[mid],
                metadatas=[{
                    "source"    : source,
                    "stored_at" : datetime.now(_TZ).isoformat(),
                }]
            )
        return True
    except Exception as e:
        print(f"[SemanticMemory] Gagal simpan: {e}")
        return False


def retrieve_memory(query: str, top_k: int = 3) -> str:
    """
    Ambil memory yang paling relevan dengan query.
    Return string atau string kosong jika tidak ada.
    """
    if not _API_KEY or not query.strip():
        return ""
    col = _get_collection()
    if col is None or col.count() == 0:
        return ""
    try:
        vec     = _embed(query)
        n       = min(top_k, col.count())
        results = col.query(
            query_embeddings=[vec],
            n_results=n,
            include=["documents", "metadatas", "distances"]
        )
        docs  = results.get("documents", [[]])[0]
        dists = results.get("distances", [[]])[0]
        if not docs:
            return ""

        # Filter: hanya ambil yang similarity > 0.6
        relevant = [
            d for d, dist in zip(docs, dists)
            if (1 - dist) >= 0.6
        ]
        if not relevant:
            return ""

        lines = ["[Memori Semantik Relevan]:"]
        for d in relevant:
            lines.append(f"• {d[:300]}")
        return "\n".join(lines)
    except Exception as e:
        print(f"[SemanticMemory] Gagal retrieve: {e}")
        return ""


def count() -> int:
    """Jumlah entry di semantic memory."""
    col = _get_collection()
    return col.count() if col else 0
