"""
features/analytics.py — APRIS Usage Analytics
===============================================
Log penggunaan fitur per user ke SQLite (/data/analytics.db)
Digunakan oleh admin command /stats untuk laporan.
"""

import os
import sqlite3
import threading
from pathlib import Path
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Konfigurasi
# ---------------------------------------------------------------------------
_IS_RAILWAY  = bool(os.getenv("RAILWAY_ENVIRONMENT"))
_DEFAULT_DB  = "/data/analytics.db" if _IS_RAILWAY else "./analytics.db"
ANALYTICS_DB = os.getenv("ANALYTICS_DB_PATH", _DEFAULT_DB)
_db_lock     = threading.Lock()


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------
def init_db():
    """Buat tabel analytics jika belum ada."""
    Path(ANALYTICS_DB).parent.mkdir(parents=True, exist_ok=True)
    with _db_lock:
        con = sqlite3.connect(ANALYTICS_DB)
        con.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id  TEXT NOT NULL,
                feature  TEXT NOT NULL,
                name     TEXT,
                ts       TEXT NOT NULL
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_ts      ON events(ts)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_user    ON events(user_id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_feature ON events(feature)")
        con.commit()
        con.close()


def _get_con() -> sqlite3.Connection:
    Path(ANALYTICS_DB).parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(ANALYTICS_DB)


# ---------------------------------------------------------------------------
# Log
# ---------------------------------------------------------------------------
def log_event(user_id: str, feature: str = "chat", name: str = ""):
    """Log satu event penggunaan fitur."""
    if not user_id:
        return
    ts = datetime.now().isoformat()
    try:
        with _db_lock:
            con = _get_con()
            con.execute(
                "INSERT INTO events(user_id, feature, name, ts) VALUES(?,?,?,?)",
                (user_id, feature, name or "", ts)
            )
            con.commit()
            con.close()
    except Exception as e:
        print(f"[Analytics] log_event error: {e}", flush=True)


# ---------------------------------------------------------------------------
# Stats Report
# ---------------------------------------------------------------------------
def get_stats(days: int = 7) -> dict:
    """
    Ambil statistik penggunaan selama N hari terakhir.
    Returns:
        {
          "period_days": int,
          "total_messages": int,
          "active_users": int,
          "features": [{"name": str, "count": int, "pct": float}],
          "top_users": [{"user_id": str, "name": str, "count": int}],
          "error": str | None
        }
    """
    try:
        since = (datetime.now() - timedelta(days=days)).isoformat()
        with _db_lock:
            con = _get_con()

            total = con.execute(
                "SELECT COUNT(*) FROM events WHERE ts >= ?", (since,)
            ).fetchone()[0]

            active_users = con.execute(
                "SELECT COUNT(DISTINCT user_id) FROM events WHERE ts >= ?", (since,)
            ).fetchone()[0]

            feature_rows = con.execute(
                """SELECT feature, COUNT(*) as cnt FROM events
                   WHERE ts >= ? GROUP BY feature ORDER BY cnt DESC""",
                (since,)
            ).fetchall()

            user_rows = con.execute(
                """SELECT user_id, name, COUNT(*) as cnt FROM events
                   WHERE ts >= ? GROUP BY user_id ORDER BY cnt DESC LIMIT 10""",
                (since,)
            ).fetchall()

            con.close()

        features = [
            {
                "name" : row[0],
                "count": row[1],
                "pct"  : round(row[1] / max(total, 1) * 100, 1),
            }
            for row in feature_rows
        ]

        top_users = [
            {"user_id": row[0], "name": row[1] or row[0], "count": row[2]}
            for row in user_rows
        ]

        return {
            "period_days"   : days,
            "total_messages": total,
            "active_users"  : active_users,
            "features"      : features,
            "top_users"     : top_users,
            "error"         : None,
        }
    except Exception as e:
        return {
            "period_days"   : days,
            "total_messages": 0,
            "active_users"  : 0,
            "features"      : [],
            "top_users"     : [],
            "error"         : str(e),
        }


def format_stats_message(stats: dict) -> str:
    """Format stats dict menjadi pesan WA untuk admin."""
    if stats.get("error"):
        return f"❌ Gagal membuat laporan: {stats['error']}"

    days  = stats["period_days"]
    total = stats["total_messages"]
    users = stats["active_users"]

    lines = [
        f"📊 *STATISTIK APRIS ({days} hari terakhir)*",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        f"👥 User aktif    : *{users} user*",
        f"💬 Total pesan   : *{total} pesan*",
        "",
    ]

    if stats["features"]:
        lines.append("📈 *Fitur terpopuler:*")
        bar_chars = "█"
        for f in stats["features"][:6]:
            bar_len = max(1, int(f["pct"] / 10))
            bar     = bar_chars * bar_len + "░" * (10 - bar_len)
            lines.append(f"  {bar} {f['pct']}% {f['name']} ({f['count']}x)")

    if stats["top_users"]:
        lines.append("")
        lines.append("🏆 *User paling aktif:*")
        for i, u in enumerate(stats["top_users"][:5], 1):
            lines.append(f"  {i}. {u['name']} — {u['count']} pesan")

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"_Gunakan `/stats 30` untuk laporan 30 hari_",
    ]
    return "\n".join(lines)
