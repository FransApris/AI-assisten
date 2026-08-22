"""
features/contacts.py — Google Contacts (People API) Integration
===============================================================
Mencari kontak dari Google Contacts.

Tag sistem prompt:
    <SEARCH_CONTACT name="Nama Orang"/>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


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
            query=name,
            pageSize=max_results,
            readMask="names,emailAddresses,phoneNumbers"
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
            phone_str = ", ".join(p.get("value", "") for p in phones) or "—"
            email_str = ", ".join(e.get("value", "") for e in emails) or "—"

            lines.append(f"\n*{display_name}*")
            lines.append(f"  📱 {phone_str}")
            lines.append(f"  ✉️  {email_str}")

        return "\n".join(lines)
    except Exception as e:
        return f"[Gagal mencari kontak: {e}]"
