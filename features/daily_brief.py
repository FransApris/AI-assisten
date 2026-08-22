"""
features/daily_brief.py — Generator Daily Briefing Otomatis
============================================================
Menghasilkan ringkasan pagi berisi cuaca, kalender, dan status obat.
Dijalankan via APScheduler setiap hari jam 07:00 WIB.
"""
import os
from datetime import datetime, timedelta
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo(os.getenv("TZ", "Asia/Jakarta"))
except Exception:
    from datetime import timezone
    TZ = timezone(timedelta(hours=7))

# Cache brief terakhir (diperbarui scheduler setiap pagi)
_brief_cache: dict = {
    "content"      : "",
    "generated_at" : None,
}


def generate_brief(city: str = "Jakarta") -> str:
    """
    Generate daily brief: cuaca + kalender + status obat.
    Dipanggil oleh scheduler atau saat /briefing diminta.
    """
    lines = []
    now_str = datetime.now(TZ).strftime("%A, %d %B %Y — %H:%M WIB")
    lines.append(f"☀️ *Selamat Pagi! Briefing Harian APRIS*")
    lines.append(f"_{now_str}_\n")

    # --- Cuaca ---
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from features import weather
        w = weather.get_weather_by_city(city)
        lines.append(f"🌤️ *Cuaca {city}:*\n{w}")
    except Exception as e:
        lines.append(f"🌤️ *Cuaca:* _Tidak tersedia ({e})_")

    lines.append("")

    # --- Kalender ---
    try:
        import google_calendar
        cal = google_calendar.get_upcoming_events(5)
        lines.append(f"📅 *Jadwal Hari Ini:*\n{cal}")
    except Exception as e:
        lines.append(f"📅 *Kalender:* _Tidak tersedia ({e})_")

    lines.append("")

    # --- Obat ---
    try:
        from features import medical
        meds = medical.get_meds_summary()
        if meds:
            lines.append(f"💊 *Status Obat:*\n{meds}")
        else:
            lines.append("💊 *Status Obat:* Tidak ada jadwal obat.")
    except Exception as e:
        lines.append(f"💊 *Status Obat:* _Tidak tersedia ({e})_")

    lines.append("")
    lines.append("—")
    lines.append("Ada yang bisa saya bantu hari ini?")

    brief = "\n".join(lines)
    _brief_cache["content"]      = brief
    _brief_cache["generated_at"] = datetime.now(TZ).isoformat()
    return brief


def get_cached_brief() -> dict:
    """Ambil brief dari cache. Jika belum ada, generate sekarang."""
    if not _brief_cache["content"]:
        generate_brief()
    return _brief_cache


def schedule_daily_brief(scheduler, city: str = "Jakarta"):
    """
    Daftarkan job di APScheduler untuk generate brief setiap pagi 07:00 WIB.
    Dipanggil sekali saat startup chat_server.
    """
    from apscheduler.triggers.cron import CronTrigger
    scheduler.add_job(
        generate_brief,
        trigger=CronTrigger(hour=7, minute=0, timezone=TZ),
        id="daily_brief",
        args=[city],
        replace_existing=True,
        name="Daily Brief Generator",
    )
    print("[DailyBrief] Job terdaftar: setiap hari 07:00 WIB", flush=True)
