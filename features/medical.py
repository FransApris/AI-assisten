import json
import threading
from pathlib import Path
from datetime import datetime, timedelta
import os

try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo(os.getenv("TZ", "Asia/Jakarta"))
except Exception:
    from datetime import timezone
    _TZ = timezone(timedelta(hours=7))

# Path file database obat
MED_FILE = Path(__file__).parent.parent / "medications.json"
_lock = threading.Lock()   # Proteksi race condition baca/tulis file


def _load_meds() -> list:
    if not MED_FILE.exists():
        return []
    try:
        with open(MED_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def _save_meds(meds: list):
    """Simpan data obat (harus dipanggil dalam _lock)."""
    with open(MED_FILE, 'w', encoding='utf-8') as f:
        json.dump(meds, f, indent=4, ensure_ascii=False)


def _validate_time(time_str: str) -> bool:
    """Validasi format waktu HH:MM (00:00 – 23:59)."""
    try:
        parts = time_str.strip().split(":")
        if len(parts) != 2:
            return False
        h, m = int(parts[0]), int(parts[1])
        return 0 <= h <= 23 and 0 <= m <= 59
    except Exception:
        return False


def add_medicine(name: str, time: str, reason: str) -> str:
    """Menambahkan jadwal obat baru."""
    time = time.strip()
    if not _validate_time(time):
        return (
            f"Format waktu '{time}' tidak valid. "
            "Gunakan format HH:MM, misalnya 08:00."
        )
    with _lock:
        meds = _load_meds()
        # Cek duplikat
        for m in meds:
            if m['name'].lower() == name.lower() and m['time'] == time:
                return f"Obat {name} pada jam {time} sudah ada di jadwal."

        meds.append({
            "name"      : name,
            "time"      : time,       # Format "HH:MM"
            "reason"    : reason,
            "last_taken": ""          # Format "YYYY-MM-DD"
        })
        # Sort berdasarkan jam
        meds.sort(key=lambda x: x['time'])
        _save_meds(meds)
    return f"Berhasil menambahkan {name} ke jadwal pada pukul {time}."


def get_all_meds() -> list:
    """Mendapatkan daftar semua obat."""
    return _load_meds()


def mark_taken(name: str, time: str) -> bool:
    """Menandai obat sudah diminum hari ini."""
    today = datetime.now(_TZ).strftime("%Y-%m-%d")
    with _lock:
        meds = _load_meds()
        for m in meds:
            if m['name'].lower() == name.lower() and m['time'] == time:
                m['last_taken'] = today
                _save_meds(meds)
                return True
    return False


def get_meds_summary() -> str:
    """Mengembalikan summary daftar obat untuk di-inject ke prompt AI."""
    meds = _load_meds()
    if not meds:
        return ""

    today   = datetime.now(_TZ).strftime("%Y-%m-%d")
    summary = "Jadwal Obat Harian Pengguna:\n"
    for m in meds:
        status   = "Sudah diminum" if m.get('last_taken') == today else "Belum diminum"
        summary += f"- {m['name']} (Jam {m['time']}): {m['reason']} -> Status Hari Ini: {status}\n"
    return summary
