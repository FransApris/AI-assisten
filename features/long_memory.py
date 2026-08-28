"""
features/long_memory.py — APRIS Persistent Long-term Memory Per User
=====================================================================
Menyimpan ringkasan percakapan per user ke SQLite (/data/memory.db)
agar APRIS tetap "ingat" konteks meski Railway restart.

Cara kerja:
  1. Saat session baru dibuat: load summary lama dari DB → inject ke history
  2. Saat history di-trim / session clear: simpan ringkasan baru ke DB
  3. Ringkasan dibuat oleh Gemini dari percakapan terakhir

Skema DB:
  TABLE user_memory (
    chat_id     TEXT PRIMARY KEY,
    summary     TEXT,        -- ringkasan percakapan terakhir
    key_facts   TEXT,        -- fakta penting (JSON array)
    updated_at  TEXT
  )
"""

import os
import json
import sqlite3
import threading
from pathlib import Path
from datetime import datetime

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_IS_RAILWAY  = bool(os.getenv("RAILWAY_ENVIRONMENT"))
_DEFAULT_PATH = "/data/memory.db" if _IS_RAILWAY else str(
    Path(__file__).resolve().parent.parent / "memory.db"
)
MEMORY_DB_PATH = os.getenv("MEMORY_DB_PATH", _DEFAULT_PATH)

_db_lock = threading.Lock()


# ---------------------------------------------------------------------------
# DB Init
# ---------------------------------------------------------------------------
def init_db():
    """Buat tabel jika belum ada."""
    Path(MEMORY_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with _db_lock:
        conn = sqlite3.connect(MEMORY_DB_PATH, check_same_thread=False)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_memory (
                chat_id    TEXT PRIMARY KEY,
                summary    TEXT DEFAULT '',
                key_facts  TEXT DEFAULT '[]',
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
        conn.close()
    print(f"[LongMemory] DB init: {MEMORY_DB_PATH}", flush=True)


# ---------------------------------------------------------------------------
# Read / Write
# ---------------------------------------------------------------------------
def load_memory(chat_id: str) -> dict:
    """
    Muat memori user dari DB.
    Return: {'summary': str, 'key_facts': list}
    """
    try:
        with _db_lock:
            conn = sqlite3.connect(MEMORY_DB_PATH, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT summary, key_facts FROM user_memory WHERE chat_id = ?",
                (chat_id,)
            ).fetchone()
            conn.close()
        if row:
            return {
                "summary"  : row["summary"] or "",
                "key_facts": json.loads(row["key_facts"] or "[]"),
            }
    except Exception as e:
        print(f"[LongMemory] Gagal load {chat_id}: {e}", flush=True)
    return {"summary": "", "key_facts": []}


def save_memory(chat_id: str, summary: str, key_facts: list = None):
    """Simpan/update memori user ke DB."""
    try:
        facts_json = json.dumps(key_facts or [], ensure_ascii=False)
        with _db_lock:
            conn = sqlite3.connect(MEMORY_DB_PATH, check_same_thread=False)
            conn.execute(
                """
                INSERT INTO user_memory (chat_id, summary, key_facts, updated_at)
                VALUES (?, ?, ?, datetime('now'))
                ON CONFLICT(chat_id) DO UPDATE SET
                    summary    = excluded.summary,
                    key_facts  = excluded.key_facts,
                    updated_at = excluded.updated_at
                """,
                (chat_id, summary, facts_json),
            )
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"[LongMemory] Gagal save {chat_id}: {e}", flush=True)


def clear_memory(chat_id: str):
    """Hapus memori user (saat user minta reset total)."""
    try:
        with _db_lock:
            conn = sqlite3.connect(MEMORY_DB_PATH, check_same_thread=False)
            conn.execute("DELETE FROM user_memory WHERE chat_id = ?", (chat_id,))
            conn.commit()
            conn.close()
        print(f"[LongMemory] Memori dihapus: {chat_id}", flush=True)
    except Exception as e:
        print(f"[LongMemory] Gagal clear {chat_id}: {e}", flush=True)


def list_all(limit: int = 20) -> list:
    """Kembalikan daftar user yang punya memori (untuk debug/admin)."""
    try:
        with _db_lock:
            conn = sqlite3.connect(MEMORY_DB_PATH, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT chat_id, updated_at, length(summary) as len FROM user_memory ORDER BY updated_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
            conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Memory Injection ke Session History
# ---------------------------------------------------------------------------
def build_memory_context(memory: dict) -> str:
    """
    Buat teks konteks memori untuk diinjeksikan ke system prompt / history.
    Dipanggil saat session baru dimulai.
    """
    parts = []
    if memory.get("summary"):
        parts.append(f"Ringkasan percakapan sebelumnya:\n{memory['summary']}")
    if memory.get("key_facts"):
        facts = "\n".join(f"- {f}" for f in memory["key_facts"])
        parts.append(f"Fakta penting tentang user ini:\n{facts}")
    return "\n\n".join(parts)


def extract_key_facts_from_history(history: list) -> list:
    """
    Ekstrak fakta penting dari history percakapan (nama, pekerjaan, preferensi, dll).
    Heuristik sederhana — bisa diganti dengan pemanggilan Gemini jika perlu lebih akurat.
    """
    import re
    facts = []
    user_messages = [h["content"] for h in history if h.get("role") == "user"]
    text = " ".join(user_messages[-20:])  # ambil 20 pesan terakhir

    # Deteksi nama
    name_match = re.search(r"(?:nama saya|panggil saya|saya adalah|i am|my name is)\s+([A-Z][a-z]+)", text, re.IGNORECASE)
    if name_match:
        facts.append(f"Nama user: {name_match.group(1)}")

    # Deteksi pekerjaan
    job_match = re.search(r"(?:saya bekerja|saya adalah|i work as|i am a|i'm a)\s+(?:seorang\s+)?([a-zA-Z ]+)", text, re.IGNORECASE)
    if job_match:
        job = job_match.group(1).strip()[:40]
        facts.append(f"Pekerjaan: {job}")

    return facts[:5]  # maks 5 fakta
