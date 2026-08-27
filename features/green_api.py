"""
green_api.py — APRIS Green-API Handler
==================================================
Modul untuk berinteraksi dengan Green-API (Unofficial WhatsApp API).
Mengirim pesan teks, media, dan pesan interaktif (buttons/list).
"""

import os
import base64
import mimetypes
import requests

GREEN_API_ID    = os.getenv("GREEN_API_ID", "")
GREEN_API_TOKEN = os.getenv("GREEN_API_TOKEN", "")
# Prefix host: ambil 4 karakter pertama jika tersedia, fallback ke default
_id_prefix = GREEN_API_ID[:4] if len(GREEN_API_ID) >= 4 else ""
GREEN_API_HOST = f"https://{_id_prefix}.api.greenapi.com" if _id_prefix else "https://api.green-api.com"


def _base_url(endpoint: str) -> str:
    return f"{GREEN_API_HOST}/waInstance{GREEN_API_ID}/{endpoint}/{GREEN_API_TOKEN}"


def send_message(chat_id: str, message: str) -> dict:
    """Mengirim pesan teks ke WhatsApp melalui Green-API."""
    if not GREEN_API_ID or not GREEN_API_TOKEN:
        print("[Green-API] Error: GREEN_API_ID atau TOKEN belum diset.")
        return {}

    try:
        res = requests.post(
            _base_url("sendMessage"),
            json={"chatId": chat_id, "message": message},
            timeout=15
        )
        res.raise_for_status()
        return res.json()
    except Exception as e:
        print(f"[Green-API] Gagal mengirim pesan: {e}")
        return {}


def send_buttons(chat_id: str, header: str, body: str, buttons: list,
                 footer: str = "") -> dict:
    """
    Kirim pesan dengan tombol interaktif (max 3 tombol).

    buttons format (WhatsApp Cloud API):
      [{"type":"reply","reply":{"id":"btn_1","title":"Teks tombol"}}]

    Atau format sederhana:
      [{"id": "btn_1", "title": "Teks tombol"}]

    Fallback otomatis ke teks biasa jika Green-API menolak.
    """
    if not GREEN_API_ID or not GREEN_API_TOKEN:
        return send_message(chat_id, body)

    # Normalisasi format tombol
    normalized = []
    for b in buttons[:3]:
        if "reply" in b:  # WhatsApp Cloud API format
            btn = b["reply"]
        else:
            btn = b
        normalized.append({
            "buttonId"   : str(btn.get("id", btn.get("buttonId", ""))),
            "buttonText" : {"displayText": str(btn.get("title", btn.get("buttonText", {}).get("displayText", "")))},
        })

    payload = {
        "chatId" : chat_id,
        "message": body,
        "footer" : footer,
        "buttons": normalized,
    }

    try:
        res = requests.post(_base_url("sendButtons"), json=payload, timeout=15)
        res.raise_for_status()
        print(f"[Green-API] Interactive buttons terkirim ke {chat_id}")
        return res.json()
    except Exception as e:
        print(f"[Green-API] send_buttons gagal ({e}), fallback ke teks biasa")
        # Fallback: konversi ke teks biasa dengan daftar opsi
        btn_text = "\n".join(
            f"  *{i+1}.* {b['buttonText']['displayText']}"
            for i, b in enumerate(normalized)
        )
        fallback = f"*{header}*\n\n{body}\n\n{btn_text}"
        if footer:
            fallback += f"\n\n_{footer}_"
        return send_message(chat_id, fallback)


def send_list(chat_id: str, header: str, body: str,
              button_text: str, sections: list, footer: str = "") -> dict:
    """
    Kirim pesan dengan list menu interaktif (max 10 rows total).

    sections format:
      [{"title": "Kategori", "rows": [{"id":"r1","title":"...","description":"..."}]}]

    Fallback otomatis ke teks biasa jika Green-API menolak.
    """
    if not GREEN_API_ID or not GREEN_API_TOKEN:
        return send_message(chat_id, body)

    payload = {
        "chatId"    : chat_id,
        "message"   : body,
        "footer"    : footer,
        "buttonText": button_text,
        "sections"  : sections,
    }

    try:
        res = requests.post(_base_url("sendList"), json=payload, timeout=15)
        res.raise_for_status()
        print(f"[Green-API] Interactive list terkirim ke {chat_id}")
        return res.json()
    except Exception as e:
        print(f"[Green-API] send_list gagal ({e}), fallback ke teks biasa")
        # Fallback: konversi ke teks terformat
        lines = [f"*{header}*\n\n{body}\n"]
        for section in sections:
            lines.append(f"*{section.get('title', '')}*")
            for row in section.get("rows", []):
                desc = f" — _{row['description']}_" if row.get("description") else ""
                lines.append(f"  • *{row['title']}*{desc}")
        if footer:
            lines.append(f"\n_{footer}_")
        return send_message(chat_id, "\n".join(lines))


def send_interactive_from_cloud_api(chat_id: str, payload: dict) -> dict:
    """
    Konversi WhatsApp Cloud API interactive payload ke Green-API format dan kirim.

    Mendukung type: 'button' dan 'list'.
    Input payload adalah dict dari JSON yang dihasilkan Gemini.
    """
    interactive = payload.get("interactive", {})
    itype       = interactive.get("type", "")
    header_obj  = interactive.get("header", {})
    header_text = header_obj.get("text", "") if header_obj.get("type") == "text" else ""
    body_text   = interactive.get("body", {}).get("text", "")
    footer_text = interactive.get("footer", {}).get("text", "")
    action      = interactive.get("action", {})

    if itype == "button":
        buttons = action.get("buttons", [])
        return send_buttons(chat_id, header_text, body_text, buttons, footer_text)

    elif itype == "list":
        button_text = action.get("button", "Pilih Opsi")
        sections    = action.get("sections", [])
        return send_list(chat_id, header_text, body_text, button_text, sections, footer_text)

    else:
        print(f"[Green-API] Interactive type tidak dikenal: {itype}")
        return send_message(chat_id, body_text or str(payload))


def media_to_base64(download_url: str, mime_type: str = None) -> dict:
    """
    Download media dari URL Green-API dan konversi ke base64 untuk Gemini.
    """
    try:
        response = requests.get(download_url, timeout=30)
        response.raise_for_status()
        data = response.content

        # Tentukan mime type
        if not mime_type:
            mime_type = response.headers.get("Content-Type", "application/octet-stream")
            mime_type = mime_type.split(";")[0].strip()

        # Tentukan ekstensi
        ext = mimetypes.guess_extension(mime_type) or ".bin"
        ext_map = {".jpe": ".jpg", ".jpeg": ".jpg"}
        ext = ext_map.get(ext, ext)

        file_name = f"wa_media{ext}"

        # Validasi ukuran (max 10MB)
        max_bytes = int(os.getenv("MAX_FILE_BYTES", 10 * 1024 * 1024))
        if len(data) > max_bytes:
            raise ValueError(f"File terlalu besar ({len(data) // 1024}KB). Batas: {max_bytes // (1024*1024)}MB")

        return {
            "file_base64": base64.b64encode(data).decode("utf-8"),
            "file_mime"  : mime_type,
            "file_name"  : file_name,
            "size_bytes" : len(data),
        }
    except Exception as e:
        raise RuntimeError(f"Gagal memproses media Green-API: {e}")

