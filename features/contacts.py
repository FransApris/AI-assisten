"""
features/contacts.py — Google Contacts (People API) Integration
===============================================================
Mencari kontak dari Google Contacts.

Tag sistem prompt:
    <SEARCH_CONTACT name="Nama Orang"/>
    <GET_CONTACT_WA name="Nama Orang"/>
"""
import re
import sys
import time
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# ---------------------------------------------------------------------------
# In-memory cache — TTL 5 menit, thread-safe
# ---------------------------------------------------------------------------
_CACHE_TTL   = 300   # detik
_cache: dict = {}    # key → (result, expire_ts)
_cache_lock  = threading.Lock()


def _cache_get(key: str):
    with _cache_lock:
        entry = _cache.get(key)
        if entry and time.time() < entry[1]:
            return entry[0]
        return None


def _cache_set(key: str, value):
    with _cache_lock:
        # Trim cache jika terlalu besar (maks 200 entry)
        if len(_cache) > 200:
            oldest = min(_cache, key=lambda k: _cache[k][1])
            _cache.pop(oldest, None)
        _cache[key] = (value, time.time() + _CACHE_TTL)



def normalize_wa_number(raw: str) -> str:
    """
    Normalisasi nomor telepon ke format Green-API: '628xxx@c.us'

    Mendukung berbagai format input:
      '0812-3456-7890'     → '6281234567890@c.us'
      '+62 812 3456 7890'  → '6281234567890@c.us'
      '62812...'           → '6281234567890@c.us'
      '081234567890'       → '6281234567890@c.us'
      '628xxx@c.us'        → '628xxx@c.us'  (sudah benar, lewati)

    Return: string format Green-API, atau string kosong jika tidak valid.
    """
    if not raw:
        return ""

    # Jika sudah format Green-API
    if raw.endswith("@c.us"):
        return raw

    # Hapus semua karakter non-digit
    digits = re.sub(r"\D", "", raw)

    if not digits:
        return ""

    # Normalisasi prefix
    if digits.startswith("0"):
        digits = "62" + digits[1:]       # 08xxx → 628xxx
    elif digits.startswith("62"):
        pass                              # sudah 62xxx
    elif digits.startswith("8"):
        digits = "62" + digits           # 8xxx → 628xxx
    else:
        digits = "62" + digits           # asumsi Indonesia

    # Validasi panjang minimal (min 10 digit setelah 62)
    if len(digits) < 10:
        return ""

    return f"{digits}@c.us"


def resolve_contact_to_wa(name: str) -> tuple[str, str]:
    """
    Cari kontak berdasarkan nama di Google Contacts dan
    kembalikan nomor WA dalam format Green-API.
    Hasil di-cache 5 menit untuk efisiensi.

    Return: (wa_number, display_name)
    """
    cache_key = f"resolve:{name.lower().strip()}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        service = _get_service()
        result  = service.people().searchContacts(
            query    = name,
            pageSize = 3,
            readMask = "names,phoneNumbers"
        ).execute()

        contacts = result.get("results", [])
        for c in contacts:
            person       = c.get("person", {})
            names        = person.get("names", [])
            phones       = person.get("phoneNumbers", [])
            display_name = names[0].get("displayName", "") if names else ""
            for phone in phones:
                wa_num = normalize_wa_number(phone.get("value", ""))
                if wa_num:
                    _cache_set(cache_key, (wa_num, display_name))
                    return wa_num, display_name

        _cache_set(cache_key, ("", ""))
        return "", ""

    except Exception as e:
        print(f"[Contacts] resolve_contact_to_wa error: {e}")
        return "", ""


def reverse_lookup_wa(wa_number: str) -> str:
    """
    Cari nama kontak berdasarkan nomor WA (reverse lookup).
    Berguna saat WA masuk dari nomor tak dikenal.

    Input: nomor dalam format sembarang ('0812xxx', '628xxx@c.us', dll)
    Return: nama kontak (str) atau string kosong jika tidak ditemukan.
    """
    # Normalisasi input dulu
    normalized = normalize_wa_number(wa_number)
    if not normalized:
        return ""

    digits = re.sub(r"\D", "", normalized)   # ambil digit saja
    cache_key = f"reverse:{digits}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        service  = _get_service()
        # Cari kontak dengan semua nomor, lalu cocokkan manual
        result   = service.people().listContacts(
            pageSize = 1000,
            readMask = "names,phoneNumbers"
        ).execute()

        for person_entry in result.get("connections", []):
            phones = person_entry.get("phoneNumbers", [])
            for phone in phones:
                phone_digits = re.sub(r"\D", "", phone.get("value", ""))
                # Normalisasi phone_digits juga
                if phone_digits.startswith("0"):
                    phone_digits = "62" + phone_digits[1:]
                elif phone_digits.startswith("8"):
                    phone_digits = "62" + phone_digits
                if phone_digits == digits:
                    names = person_entry.get("names", [])
                    name  = names[0].get("displayName", "") if names else ""
                    _cache_set(cache_key, name)
                    return name

        _cache_set(cache_key, "")
        return ""

    except Exception as e:
        print(f"[Contacts] reverse_lookup_wa error: {e}")
        return ""


def get_contact_wa(name: str) -> str:
    """
    Cari nomor WA kontak dan kembalikan dalam format siap pakai.
    Digunakan oleh tag <GET_CONTACT_WA name="..."/>.

    Return: string pesan yang langsung bisa ditampilkan ke user.
    """
    wa_num, display = resolve_contact_to_wa(name)
    if not wa_num:
        return f"_Nomor WA untuk '{name}' tidak ditemukan di Google Contacts._"

    # Format nomor yang mudah dibaca
    digits  = re.sub(r"\D", "", wa_num)
    readable = f"+{digits}"
    return (
        f"📱 *{display}*\n"
        f"Nomor WA: `{readable}`\n"
        f"Format WA: `{wa_num}`"
    )


def _get_service():
    """Buat Google People API service."""
    import google_drive
    from googleapiclient.discovery import build
    creds = google_drive.get_credentials()
    return build("people", "v1", credentials=creds)


def search_contact(name: str, max_results: int = 5) -> str:
    """
    Cari kontak berdasarkan nama.
    Return string berisi nama, nomor, email.
    Hasil di-cache 5 menit.
    """
    cache_key = f"search:{name.lower().strip()}:{max_results}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        service = _get_service()
        result  = service.people().searchContacts(
            query    = name,
            pageSize = max_results,
            readMask = "names,emailAddresses,phoneNumbers"
        ).execute()

        contacts = result.get("results", [])
        if not contacts:
            out = f"Tidak ditemukan kontak dengan nama '{name}'."
            _cache_set(cache_key, out)
            return out

        lines = [f"📇 *Hasil pencarian kontak '{name}':*"]
        for c in contacts:
            person = c.get("person", {})
            names  = person.get("names", [{}])
            phones = person.get("phoneNumbers", [])
            emails = person.get("emailAddresses", [])

            display_name = names[0].get("displayName", "?") if names else "?"
            phone_lines  = []
            for p in phones:
                raw = p.get("value", "")
                wa  = normalize_wa_number(raw)
                phone_lines.append(f"{raw}" + (f" → `{wa}`" if wa else ""))
            phone_str = ", ".join(phone_lines) or "—"
            email_str = ", ".join(e.get("value", "") for e in emails) or "—"

            lines.append(f"\n*{display_name}*")
            lines.append(f"  📱 {phone_str}")
            lines.append(f"  ✉️  {email_str}")

        out = "\n".join(lines)
        _cache_set(cache_key, out)
        return out

    except Exception as e:
        return f"[Gagal mencari kontak: {e}]"

