"""
green_api.py — APRIS Green-API Handler
==================================================
Modul untuk berinteraksi dengan Green-API (Unofficial WhatsApp API).
Mengirim pesan dan mendownload media.
"""

import os
import base64
import mimetypes
import requests

GREEN_API_ID = os.getenv("GREEN_API_ID", "")
GREEN_API_TOKEN = os.getenv("GREEN_API_TOKEN", "")
GREEN_API_HOST = f"https://{GREEN_API_ID[:4]}.api.greenapi.com" if GREEN_API_ID else "https://api.green-api.com"


def send_message(chat_id: str, message: str) -> dict:
    """Mengirim pesan teks ke WhatsApp melalui Green-API."""
    if not GREEN_API_ID or not GREEN_API_TOKEN:
        print("[Green-API] Error: GREEN_API_ID atau TOKEN belum diset.")
        return {}

    url = f"{GREEN_API_HOST}/waInstance{GREEN_API_ID}/sendMessage/{GREEN_API_TOKEN}"
    payload = {
        "chatId": chat_id,
        "message": message
    }
    
    try:
        res = requests.post(url, json=payload, timeout=15)
        res.raise_for_status()
        return res.json()
    except Exception as e:
        print(f"[Green-API] Gagal mengirim pesan: {e}")
        return {}


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
