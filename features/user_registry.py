"""
features/user_registry.py — APRIS Persistent User Registry
============================================================
Menyimpan daftar user yang diizinkan mengakses APRIS via WhatsApp.
Menggunakan SQLite agar data tetap ada setelah Railway restart.

Path DB:
  - Railway  : /data/users.db  (Railway Volume harus di-mount ke /data)
  - Lokal    : ./users.db  (di folder asisten-virtual)
  - Custom   : USERS_DB_PATH env var
"""

import os
import sqlite3
import threading
from pathlib import Path
from datetime import datetime

# ---------------------------------------------------------------------------
# Config Path
# ---------------------------------------------------------------------------
_IS_RAILWAY = bool(os.getenv("RAILWAY_ENVIRONMENT"))
_DEFAULT_PATH = "/data/users.db" if _IS_RAILWAY else str(
    Path(__file__).resolve().parent.parent / "users.db"
)
USERS_DB_PATH = os.getenv("USERS_DB_PATH", _DEFAULT_PATH)

_db_lock = threading.Lock()


# ---------------------------------------------------------------------------
# DB Init
# ---------------------------------------------------------------------------
def _get_conn() -> sqlite3.Connection:
    """Buka koneksi SQLite (thread-safe via _db_lock)."""
    Path(USERS_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(USERS_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Buat tabel jika belum ada. Dipanggil sekali saat startup."""
    with _db_lock:
        conn = _get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS approved_users (
                chat_id     TEXT PRIMARY KEY,
                name        TEXT DEFAULT '',
                registered  TEXT DEFAULT (datetime('now')),
                added_by    TEXT DEFAULT 'system'
            )
        """)
        conn.commit()
        conn.close()
    print(f"[UserRegistry] DB init: {USERS_DB_PATH}", flush=True)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
def load_all_users() -> set:
    """Muat semua chat_id dari DB ke set. Dipanggil saat startup."""
    try:
        with _db_lock:
            conn = _get_conn()
            rows = conn.execute("SELECT chat_id FROM approved_users").fetchall()
            conn.close()
        return {row["chat_id"] for row in rows}
    except Exception as e:
        print(f"[UserRegistry] Gagal load users: {e}", flush=True)
        return set()


def add_user(chat_id: str, name: str = "", added_by: str = "invite_code") -> bool:
    """Tambahkan user ke DB. Return True jika berhasil."""
    try:
        with _db_lock:
            conn = _get_conn()
            conn.execute(
                """
                INSERT INTO approved_users (chat_id, name, added_by)
                VALUES (?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET name=excluded.name
                """,
                (chat_id, name, added_by),
            )
            conn.commit()
            conn.close()
        print(f"[UserRegistry] User ditambahkan: {chat_id} ({name})", flush=True)
        return True
    except Exception as e:
        print(f"[UserRegistry] Gagal tambah user {chat_id}: {e}", flush=True)
        return False


def remove_user(chat_id: str) -> bool:
    """Hapus user dari DB. Return True jika berhasil."""
    try:
        with _db_lock:
            conn = _get_conn()
            conn.execute("DELETE FROM approved_users WHERE chat_id = ?", (chat_id,))
            conn.commit()
            conn.close()
        print(f"[UserRegistry] User dihapus: {chat_id}", flush=True)
        return True
    except Exception as e:
        print(f"[UserRegistry] Gagal hapus user {chat_id}: {e}", flush=True)
        return False


def list_users() -> list:
    """Kembalikan list dict {chat_id, name, registered, added_by}."""
    try:
        with _db_lock:
            conn = _get_conn()
            rows = conn.execute(
                "SELECT chat_id, name, registered, added_by FROM approved_users ORDER BY registered"
            ).fetchall()
            conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[UserRegistry] Gagal list users: {e}", flush=True)
        return []


def user_count() -> int:
    """Kembalikan jumlah user terdaftar."""
    try:
        with _db_lock:
            conn = _get_conn()
            count = conn.execute("SELECT COUNT(*) FROM approved_users").fetchone()[0]
            conn.close()
        return count
    except Exception:
        return 0
