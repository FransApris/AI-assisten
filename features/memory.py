import json
import threading
from pathlib import Path

# File untuk menyimpan memori, diletakkan di folder parent (asisten-virtual/)
MEMORY_FILE = Path(__file__).parent.parent / "memory.json"
_lock = threading.Lock()   # Proteksi race condition baca/tulis file


def _load_memories() -> list:
    if not MEMORY_FILE.exists():
        return []
    try:
        with open(MEMORY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def _save_memories(mems: list):
    """Simpan memori (harus dipanggil dalam _lock)."""
    with open(MEMORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(mems, f, indent=4, ensure_ascii=False)


def add_memory(fact: str) -> str:
    """Menambahkan memori baru jika belum ada."""
    with _lock:
        mems = _load_memories()
        if fact not in mems:
            mems.append(fact)
            _save_memories(mems)
            return "Telah saya ingat dengan baik."
    return "Saya sudah mengingat hal tersebut sebelumnya."


def remove_memory(fact: str) -> str:
    """Menghapus memori secara fuzzy matching sederhana."""
    with _lock:
        mems      = _load_memories()
        fact_lower = fact.lower()
        new_mems  = []
        removed   = False
        for m in mems:
            # Hapus jika substring cocok (fuzzy)
            if fact_lower in m.lower() or m.lower() in fact_lower:
                removed = True
                continue
            new_mems.append(m)

        if removed:
            _save_memories(new_mems)
            return "Baik, saya telah melupakan hal tersebut."
    return "Saya tidak menemukan ingatan terkait hal tersebut."


def get_all_memories() -> str:
    """Mengembalikan seluruh memori dalam format string untuk dimasukkan ke konteks prompt."""
    mems = _load_memories()
    if not mems:
        return ""
    result = "Fakta tentang Pengguna yang Harus Saya Ingat:\n"
    for idx, m in enumerate(mems, 1):
        result += f"{idx}. {m}\n"
    return result
