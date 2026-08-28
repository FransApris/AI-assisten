"""
features/analytics.py — APRIS Usage Analytics
================================================
Mencatat setiap interaksi user untuk laporan statistik penggunaan.
Data disimpan ke SQLite (/data/analytics.db).

Admin ketik '/stats' di WA untuk melihat laporan.

Skema DB:
  TABLE usage_log (
    id         INTEGER PRIMARY KEY,
    chat_id    TEXT,
    name       TEXT,
    feature    TEXT,   -- 'chat', 'calendar', 'reminder', 'kb_update', 'cheatsheet', dll
    msg_len    INTEGER,
    created_at TEXT
  )
"""

import os
import sqlite3
import threading
from pathlib import Path
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_IS_RAILWAY   = bool(os.getenv("RAILWAY_ENVIRONMENT"))
_DEFAULT_PATH = "/data/analytics.db" if _IS_RAILWAY else str(
    Path(__file__).resolve().parent.parent / "analytics.db"
)
ANALYTICS_DB_PATH = os.getenv("ANALYTICS_DB_PATH", _DEFAULT_PATH)

_db_lock = threading.Lock()


# ---------------------------------------------------------------------------
# DB Init
# ---------------------------------------------------------------------------
def init_db():
    """Buat tabel analytics jika belum ada."""
    Path(ANALYTICS_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with _db_lock:
        conn = sqlite3.connect(ANALYTICS_DB_PATH, check_same_thread=False)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS usage_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id    TEXT    NOT NULL,
                name       TEXT    DEFAULT '',
                feature    TEXT    DEFAULT 'chat',
                msg_len    INTEGER DEFAULT 0,
                created_at TEXT    DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_id ON usage_log(chat_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_created ON usage_log(created_at)")
        conn.commit()
        conn.close()
    print(f"[Analytics] DB init: {ANALYTICS_DB_PATH}", flush=True)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def log_event(chat_id: str, feature: str = "chat", name: str = "", msg_len: int = 0):
    """
    Catat satu event penggunaan ke DB.
    Dipanggil di background thread agar tidak blocking.
    """
    try:
        with _db_lock:
            conn = sqlite3.connect(ANALYTICS_DB_PATH, check_same_thread=False)
            conn.execute(
                "INSERT INTO usage_log (chat_id, name, feature, msg_len) VALUES (?, ?, ?, ?)",
                (chat_id, name, feature, msg_len),
            )
            conn.commit()
            conn.close()
    except Exception as e:
        print(f"[Analytics] Gagal log: {e}", flush=True)


# ---------------------------------------------------------------------------
# Stats Report
# ---------------------------------------------------------------------------
def get_stats(days: int = 7) -> dict:
    """
    Ambil statistik penggunaan N hari terakhir.
    Return dict dengan data siap tampil.
    """
    try:
        since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        with _db_lock:
            conn = sqlite3.connect(ANALYTICS_DB_PATH, check_same_thread=False)
            conn.row_factory = sqlite3.Row

            # Total pesan
            total = conn.execute(
                "SELECT COUNT(*) as n FROM usage_log WHERE created_at >= ?", (since,)
            ).fetchone()["n"]

            # User unik aktif
            active_users = conn.execute(
                "SELECT COUNT(DISTINCT chat_id) as n FROM usage_log WHERE created_at >= ?", (since,)
            ).fetchone()["n"]

            # Breakdown per fitur
            features = conn.execute(
                """SELECT feature, COUNT(*) as n FROM usage_log
                   WHERE created_at >= ?
                   GROUP BY feature ORDER BY n DESC""",
                (since,)
            ).fetchall()

            # Top 5 user paling aktif
            top_users = conn.execute(
                """SELECT name, chat_id, COUNT(*) as n FROM usage_log
                   WHERE created_at >= ?
                   GROUP BY chat_id ORDER BY n DESC LIMIT 5""",
                (since,)
            ).fetchall()

            # Total sepanjang waktu
            total_all = conn.execute("SELECT COUNT(*) as n FROM usage_log").fetchone()["n"]
            total_users_all = conn.execute("SELECT COUNT(DISTINCT chat_id) as n FROM usage_log").fetchone()["n"]

            conn.close()

        return {
            "days"           : days,
            "total_msgs"     : total,
            "active_users"   : active_users,
            "features"       : [dict(f) for f in features],
            "top_users"      : [dict(u) for u in top_users],
            "total_all"      : total_all,
            "total_users_all": total_users_all,
        }
    except Exception as e:
        print(f"[Analytics] Gagal get_stats: {e}", flush=True)
        return {}


def format_stats_message(stats: dict) -> str:
    """Format dict stats menjadi pesan WA yang rapi."""
    if not stats:
        return "Gagal mengambil data analitik."

    days = stats.get("days", 7)
    lines = [
        f"📊 *STATISTIK APRIS ({days} hari terakhir)*",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        f"👥 User aktif    : *{stats['active_users']}* user",
        f"💬 Total pesan   : *{stats['total_msgs']}* pesan",
        "",
    ]

    # Breakdown fitur
    if stats.get("features"):
        total = stats["total_msgs"] or 1
        lines.append("📈 *Fitur terpopuler:*")
        feature_labels = {
            "chat"       : "Chat / Tanya jawab",
            "calendar"   : "Kalender & jadwal",
            "reminder"   : "Pengingat & obat",
            "cheatsheet" : "Lihat catatan",
            "kb_update"  : "Update knowledge base",
            "voice"      : "Pesan suara",
            "media"      : "Gambar / file",
            "admin"      : "Perintah admin",
        }
        for f in stats["features"][:6]:
            label = feature_labels.get(f["feature"], f["feature"])
            pct   = round(f["n"] / total * 100)
            bar   = "█" * (pct // 10) + "░" * (10 - pct // 10)
            lines.append(f"  {bar} {pct}% {label} ({f['n']}x)")
        lines.append("")

    # Top users
    if stats.get("top_users"):
        lines.append("🏆 *User paling aktif:*")
        for i, u in enumerate(stats["top_users"], 1):
            name = u.get("name") or u.get("chat_id", "?")[:15]
            lines.append(f"  {i}. {name} — {u['n']} pesan")
        lines.append("")

    lines += [
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"_Total sepanjang waktu: {stats['total_all']} pesan dari {stats['total_users_all']} user_",
    ]

    return "\n".join(lines)
