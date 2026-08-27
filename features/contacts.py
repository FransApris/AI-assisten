"""
features/contacts.py — Google Contacts (People API) Integration
===============================================================
Mencari kontak dari Google Contacts.

Tag sistem prompt:
    <SEARCH_CONTACT name="Nama Orang"/>
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


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

    Return: (wa_number, display_name)
      - wa_number  : '628xxx@c.us' jika ditemukan, '' jika tidak
      - display_name: nama lengkap kontak yang ditemukan

    Digunakan oleh SCHEDULE_MSG parser untuk auto-resolve nama → nomor.
    """
    try:
        service = _get_service()
        result  = service.people().searchContacts(
            query    = name,
            pageSize = 3,
            readMask = "names,phoneNumbers"
        ).execute()

        contacts = result.get("results", [])
        if not contacts:
            return "", ""

        # Ambil kontak pertama yang punya nomor telepon
        for c in contacts:
            person = c.get("person", {})
            names  = person.get("names", [])
            phones = person.get("phoneNumbers", [])

            display_name = names[0].get("displayName", "") if names else ""

            for phone in phones:
                raw_num = phone.get("value", "")
                wa_num  = normalize_wa_number(raw_num)
                if wa_num:
                    return wa_num, display_name

        return "", ""

    except Exception as e:
        print(f"[Contacts] resolve_contact_to_wa error: {e}")
        return "", ""


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
    """
    try:
        service = _get_service()

        result = service.people().searchContacts(
            query    = name,
            pageSize = max_results,
            readMask = "names,emailAddresses,phoneNumbers"
        ).execute()

        contacts = result.get("results", [])
        if not contacts:
            return f"Tidak ditemukan kontak dengan nama '{name}'."

        lines = [f"📇 *Hasil pencarian kontak '{name}':*"]
        for c in contacts:
            person = c.get("person", {})
            names  = person.get("names", [{}])
            phones = person.get("phoneNumbers", [])
            emails = person.get("emailAddresses", [])

            display_name = names[0].get("displayName", "?") if names else "?"
            # Tampilkan nomor + format WA-nya
            phone_lines = []
            for p in phones:
                raw = p.get("value", "")
                wa  = normalize_wa_number(raw)
                phone_lines.append(f"{raw}" + (f" → `{wa}`" if wa else ""))
            phone_str = ", ".join(phone_lines) or "—"
            email_str = ", ".join(e.get("value", "") for e in emails) or "—"

            lines.append(f"\n*{display_name}*")
            lines.append(f"  📱 {phone_str}")
            lines.append(f"  ✉️  {email_str}")

        return "\n".join(lines)
    except Exception as e:
        return f"[Gagal mencari kontak: {e}]"
