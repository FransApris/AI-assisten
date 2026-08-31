"""
features/reminder.py — Pengingat Otomatis via APScheduler + SQLite
"""
import os, uuid
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo(os.getenv("TZ", "Asia/Jakarta"))
except Exception:
    from datetime import timezone
    TZ = timezone(timedelta(hours=7))

_IS_RAILWAY  = bool(os.getenv("RAILWAY_ENVIRONMENT"))
_default_db  = "/data/reminders.db" if _IS_RAILWAY else "./reminders.db"
SCHEDULER_DB = os.getenv("SCHEDULER_DB", _default_db)
_scheduler     = None
_send_callback = None   # dipasang oleh features_server jika WA aktif


def set_send_callback(fn):
    """Pasang callback untuk kirim pesan saat reminder tiba."""
    global _send_callback
    _send_callback = fn


def _get_scheduler():
    global _scheduler
    if _scheduler:
        return _scheduler
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
        from apscheduler.executors.pool import ThreadPoolExecutor
        import logging
        logging.getLogger("apscheduler").setLevel(logging.WARNING)

        Path(SCHEDULER_DB).parent.mkdir(parents=True, exist_ok=True)
        _scheduler = BackgroundScheduler(
            jobstores={"default": SQLAlchemyJobStore(url=f"sqlite:///{SCHEDULER_DB}")},
            executors={"default": ThreadPoolExecutor(5)},
            job_defaults={"coalesce": True, "max_instances": 1},
            timezone=TZ,
        )
        _scheduler.start()
        return _scheduler
    except ImportError:
        raise ImportError("Jalankan: pip install apscheduler sqlalchemy")


def _fire(rid: str, target: str, message: str):
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    print(f"\n⏰ REMINDER [{now}] → {target}: {message}\n")
    if _send_callback:
        try: _send_callback(target=target, message=f"⏰ *Pengingat APRIS*\n\n{message}")
        except Exception as e: print(f"[reminder] callback error: {e}")


def set_reminder(message: str, target: str = "console",
                 run_at: str = None, delay_minutes: int = None,
                 repeat: str = None) -> dict:
    """
    Buat pengingat.
    run_at format: 'YYYY-MM-DD HH:MM'
    repeat: 'daily' | 'hourly' | 'weekly' | None
    """
    sched = _get_scheduler()
    now   = datetime.now(TZ)
    rid   = f"rem_{uuid.uuid4().hex[:8]}"

    if run_at:
        run_time = datetime.strptime(run_at, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
    elif delay_minutes:
        run_time = now + timedelta(minutes=int(delay_minutes))
    else:
        raise ValueError("Berikan 'run_at' atau 'delay_minutes'.")

    if run_time <= now and not repeat:
        raise ValueError(f"Waktu {run_at} sudah lewat.")

    if repeat == "daily":
        from apscheduler.triggers.cron import CronTrigger
        trigger = CronTrigger(hour=run_time.hour, minute=run_time.minute, timezone=TZ)
    elif repeat == "hourly":
        from apscheduler.triggers.interval import IntervalTrigger
        trigger = IntervalTrigger(hours=1, start_date=run_time, timezone=TZ)
    elif repeat == "weekly":
        from apscheduler.triggers.cron import CronTrigger
        trigger = CronTrigger(day_of_week=run_time.strftime("%a").lower(),
                              hour=run_time.hour, minute=run_time.minute, timezone=TZ)
    else:
        from apscheduler.triggers.date import DateTrigger
        trigger = DateTrigger(run_date=run_time, timezone=TZ)

    sched.add_job(_fire, trigger=trigger, id=rid, args=[rid, target, message],
                  replace_existing=True, name=message[:40])
    return {
        "id"        : rid,
        "message"   : message,
        "target"    : target,
        "run_at"    : run_time.strftime("%Y-%m-%d %H:%M"),
        "repeat"    : repeat or "once",
        "status"    : "scheduled",
    }


def list_reminders() -> list:
    return [{"id":j.id, "name":j.name,
             "next_run": j.next_run_time.strftime("%Y-%m-%d %H:%M") if j.next_run_time else "N/A",
             "trigger": str(j.trigger)}
            for j in _get_scheduler().get_jobs()]


def cancel_reminder(rid: str) -> dict:
    sched = _get_scheduler()
    if not sched.get_job(rid):
        raise ValueError(f"Reminder '{rid}' tidak ditemukan.")
    sched.remove_job(rid)
    return {"id": rid, "status": "cancelled"}


def shutdown():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None
