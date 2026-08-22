"""
features/daily_brief.py — Generator Daily Briefing Otomatis
============================================================
Menghasilkan ringkasan pagi berisi cuaca, kalender, dan status obat.
Dijalankan via APScheduler setiap hari jam 07:00 WIB.
"""
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Pastikan root project ada di sys.path agar google_gmail bisa diimpor
# bahkan saat modul ini dieksekusi oleh APScheduler sebagai background job.
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

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

    # --- Autonomous Action: Kirim ke Email pengguna ---
    try:
        user_emails_raw = os.getenv("USER_EMAIL", "").strip()
        if not user_emails_raw or user_emails_raw == "isi_dengan_email_anda@gmail.com":
            print("[DailyBrief] USER_EMAIL belum dikonfigurasi di .env. Lewati pengiriman email.", flush=True)
        else:
            import google_gmail
            clean_body = brief.replace("*", "").replace("_", "")
            subject    = f"\u2600\ufe0f Laporan Harian APRIS - {now_str}"

            # Dukung banyak email: pisahkan dengan koma
            recipient_list = [
                addr.strip() for addr in user_emails_raw.split(",")
                if addr.strip() and "@" in addr.strip() and "." in addr.strip().split("@")[-1]
            ]

            if not recipient_list:
                print(f"[DailyBrief] Tidak ada alamat email valid ditemukan di USER_EMAIL: '{user_emails_raw}'", flush=True)
            else:
                for addr in recipient_list:
                    result = google_gmail.send_email(to=addr, subject=subject, body=clean_body)
                    print(f"[DailyBrief] {result}", flush=True)
    except Exception as e:
        print(f"[DailyBrief] Gagal mengirim email: {e}", flush=True)

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
