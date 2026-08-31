"""
features/long_memory.py — APRIS Long-Term Memory (Per-User Persistent Facts)
===========================================================================
Menyimpan ringkasan & fakta penting tentang user ke SQLite (/data/memory.db)
agar tetap ada setelah Railway restart.

Operasi:
  - save_memory(user_id, snippet, key_facts)
  - load_memory(user_id) → str (konteks untuk inject ke Gemini)
  - clear_memory(user_id)
  - extract_key_facts_from_history(history) → list[str]
"""

import os
import sqlite3
import threading
import json
from pathlib import Path
from datetime import datetime

# ---------------------------------------------------------------------------
# Konfigurasi
# ---------------------------------------------------------------------------
_IS_RAILWAY = bool(os.getenv("RAILWAY_ENVIRONMENT"))
_DEFAULT_DB = "/data/memory.db" if _IS_RAILWAY else "./memory.db"
MEMORY_DB   = os.getenv("MEMORY_DB_PATH", _DEFAULT_DB)
_db_lock    = threading.Lock()

MAX_FACTS_PER_USER   = 20   # maks fakta per user
MAX_SNIPPET_LENGTH   = 800  # maks karakter snippet


# ---------------------------------------------------------------------------
# Inisialisasi DB
# ---------------------------------------------------------------------------
def init_db():
    """Buat tabel jika belum ada."""
    Path(MEMORY_DB).parent.mkdir(parents=True, exist_ok=True)
    with _db_lock:
        con = sqlite3.connect(MEMORY_DB)
        con.execute("""
            CREATE TABLE IF NOT EXISTS user_memory (
                user_id  TEXT NOT NULL,
                key_fact TEXT NOT NULL,
                updated  TEXT NOT NULL,
                PRIMARY KEY (user_id, key_fact)
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS user_snippet (
                user_id TEXT PRIMARY KEY,
                snippet TEXT,
                updated TEXT
            )
        """)
        con.commit()
        con.close()


def _get_con() -> sqlite3.Connection:
    Path(MEMORY_DB).parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(MEMORY_DB)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
def save_memory(user_id: str, snippet: str, key_facts: list[str] = None):
    """Simpan ringkasan dan/atau fakta user ke SQLite."""
    if not user_id:
        return
    now = datetime.now().isoformat()
    with _db_lock:
        con = _get_con()
        try:
            # Simpan snippet
            if snippet:
                snip = snippet[:MAX_SNIPPET_LENGTH]
                con.execute(
                    "INSERT OR REPLACE INTO user_snippet(user_id, snippet, updated) VALUES(?,?,?)",
                    (user_id, snip, now)
                )
            # Simpan fakta
            if key_facts:
                for fact in key_facts[:MAX_FACTS_PER_USER]:
                    if fact and fact.strip():
                        con.execute(
                            "INSERT OR REPLACE INTO user_memory(user_id, key_fact, updated) VALUES(?,?,?)",
                            (user_id, fact.strip()[:300], now)
                        )
                # Batasi maks fakta per user
                con.execute("""
                    DELETE FROM user_memory WHERE user_id=? AND key_fact NOT IN (
                        SELECT key_fact FROM user_memory WHERE user_id=?
                        ORDER BY updated DESC LIMIT ?
                    )
                """, (user_id, user_id, MAX_FACTS_PER_USER))
            con.commit()
        finally:
            con.close()


def load_memory(user_id: str) -> str:
    """
    Muat konteks memori user.
    Mengembalikan string siap inject ke system prompt, atau '' jika kosong.
    """
    if not user_id:
        return ""
    with _db_lock:
        con = _get_con()
        try:
            # Muat fakta
            rows  = con.execute(
                "SELECT key_fact FROM user_memory WHERE user_id=? ORDER BY updated DESC LIMIT ?",
                (user_id, MAX_FACTS_PER_USER)
            ).fetchall()
            facts = [r[0] for r in rows]

            # Muat snippet
            row = con.execute(
                "SELECT snippet FROM user_snippet WHERE user_id=?",
                (user_id,)
            ).fetchone()
            snippet = row[0] if row else ""
        finally:
            con.close()

    if not facts and not snippet:
        return ""

    parts = []
    if facts:
        parts.append("*Fakta yang diingat tentang pengguna ini:*")
        parts.extend(f"- {f}" for f in facts)
    if snippet:
        parts.append("\n*Ringkasan percakapan sebelumnya:*")
        parts.append(snippet)

    return "\n".join(parts)


def save_fact(user_id: str, fact: str):
    """Simpan satu fakta spesifik (dari tag <REMEMBER>)."""
    save_memory(user_id, snippet="", key_facts=[fact])


def forget_fact(user_id: str, fact: str):
    """Hapus fakta spesifik (dari tag <FORGET>)."""
    if not user_id or not fact:
        return
    with _db_lock:
        con = _get_con()
        try:
            # Cari fakta yang paling mirip (partial match)
            con.execute(
                "DELETE FROM user_memory WHERE user_id=? AND key_fact LIKE ?",
                (user_id, f"%{fact.strip()[:100]}%")
            )
            con.commit()
        finally:
            con.close()


def clear_memory(user_id: str):
    """Hapus semua memori user."""
    if not user_id:
        return
    with _db_lock:
        con = _get_con()
        try:
            con.execute("DELETE FROM user_memory WHERE user_id=?", (user_id,))
            con.execute("DELETE FROM user_snippet WHERE user_id=?", (user_id,))
            con.commit()
        finally:
            con.close()


# ---------------------------------------------------------------------------
# Ekstraksi fakta dari history (utility)
# ---------------------------------------------------------------------------
def extract_key_facts_from_history(history: list) -> list[str]:
    """
    Ekstrak fakta penting dari history percakapan.
    Sederhana: cari pola 'saya ... / nama saya / alergi / hobi / dsb'
    """
    facts = []
    keywords = [
        "nama saya", "saya bernama", "umur saya", "saya berumur",
        "pekerjaan saya", "saya bekerja", "saya tinggal", "alamat saya",
        "alergi", "hobi saya", "saya suka", "saya tidak suka",
        "saya vegetarian", "saya muslim", "agama saya",
    ]
    for msg in history:
        if msg.get("role") not in ("user",):
            continue
        content = (msg.get("content") or "").lower()
        for kw in keywords:
            if kw in content:
                # Ambil kalimat yang mengandung keyword
                for sent in content.split("."):
                    if kw in sent and len(sent.strip()) > 5:
                        fact = sent.strip()[:200]
                        if fact and fact not in facts:
                            facts.append(fact)
                break
    return facts[:10]
