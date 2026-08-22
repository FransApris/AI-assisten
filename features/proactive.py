"""
features/proactive.py — Proactive Agent APRIS
==============================================
Scheduler jobs yang membuat APRIS bertindak proaktif:
  1. Reminder kalender H-60 menit sebelum event
  2. Reminder obat yang belum diminum (cek setiap 30 menit)
  3. Generate daily brief pagi hari

Semua notifikasi dikirim via SSE ke browser.
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

# Callback untuk push SSE event ke semua client yang terhubung
_sse_push: callable = None


def set_sse_push(fn):
    """Set callback untuk push SSE. Dipanggil oleh chat_server saat startup."""
    global _sse_push
    _sse_push = fn


def _push(event_type: str, message: str):
    """Push notifikasi ke semua client SSE yang aktif."""
    if _sse_push:
        try:
            _sse_push(event_type=event_type, message=message)
        except Exception as e:
            print(f"[Proactive] SSE push error: {e}")


def check_upcoming_events():
    """
    Cek event kalender dalam 60 menit ke depan.
    Jika ada, kirim notifikasi ke browser.
    """
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        import google_calendar
        from googleapiclient.discovery import build
        import google_drive

        creds   = google_drive.get_credentials()
        service = build("calendar", "v3", credentials=creds)

        now     = datetime.now(TZ)
        window  = now + timedelta(minutes=60)
        now_iso = now.isoformat()
        win_iso = window.isoformat()

        result = service.events().list(
            calendarId="primary",
            timeMin=now_iso,
            timeMax=win_iso,
            singleEvents=True,
            orderBy="startTime"
        ).execute()

        events = result.get("items", [])
        for ev in events:
            start_str = ev["start"].get("dateTime", ev["start"].get("date", ""))
            title     = ev.get("summary", "Tanpa judul")
            _push("calendar_reminder",
                  f"⏰ Pengingat: *{title}* dimulai dalam 60 menit ({start_str[:16]})")
    except Exception as e:
        print(f"[Proactive] check_upcoming_events error: {e}")


def check_missed_meds():
    """
    Cek apakah ada obat yang seharusnya sudah diminum tapi belum.
    Kirim notifikasi jika lewat 30 menit dari jadwal.
    """
    try:
        from features import medical
        meds  = medical.get_all_meds()
        now   = datetime.now(TZ)
        today = now.strftime("%Y-%m-%d")

        for m in meds:
            last_taken = m.get("last_taken", "")
            if last_taken == today:
                continue  # sudah diminum

            sched_hour, sched_min = map(int, m["time"].split(":"))
            sched_dt = now.replace(hour=sched_hour, minute=sched_min, second=0, microsecond=0)

            # Hanya kirim notif jika sudah lewat 0–90 menit dari jadwal
            diff_minutes = (now - sched_dt).total_seconds() / 60
            if 0 < diff_minutes <= 90:
                _push("med_reminder",
                      f"💊 Jangan lupa minum *{m['name']}* (jadwal {m['time']} WIB) — {m['reason']}")
    except Exception as e:
        print(f"[Proactive] check_missed_meds error: {e}")


def register_jobs(scheduler):
    """
    Daftarkan semua proactive jobs ke APScheduler.
    Dipanggil sekali saat startup chat_server.
    """
    from apscheduler.triggers.interval import IntervalTrigger
    from apscheduler.triggers.cron    import CronTrigger

    # Cek kalender setiap 30 menit
    scheduler.add_job(
        check_upcoming_events,
        trigger=IntervalTrigger(minutes=30),
        id="proactive_calendar",
        replace_existing=True,
        name="Proactive: Calendar Check",
    )

    # Cek obat setiap 15 menit
    scheduler.add_job(
        check_missed_meds,
        trigger=IntervalTrigger(minutes=15),
        id="proactive_meds",
        replace_existing=True,
        name="Proactive: Meds Check",
    )

    print("[Proactive] Jobs terdaftar: calendar (30m), meds (15m)", flush=True)
