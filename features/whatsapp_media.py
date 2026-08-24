"""
whatsapp_media.py — APRIS WhatsApp Media Handler
==================================================
Mendownload dan memproses media (foto, audio, dokumen) yang dikirim
user melalui WhatsApp via Twilio, lalu mengkonversinya ke format
yang bisa diterima oleh Gemini AI (base64).
"""

import os
import base64
import mimetypes
import requests
from pathlib import Path


# Twilio auth untuk download media
TWILIO_SID   = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")


def download_media(media_url: str) -> tuple[bytes, str]:
    """
    Download media dari URL Twilio.
    
    Args:
        media_url: URL media dari Twilio (memerlukan HTTP Basic Auth)
    
    Returns:
        Tuple (bytes data, mime_type string)
    """
    try:
        response = requests.get(
            media_url,
            auth=(TWILIO_SID, TWILIO_TOKEN),
            timeout=30,
        )
        response.raise_for_status()
        
        # Ambil mime type dari header atau tebak dari URL
        content_type = response.headers.get("Content-Type", "application/octet-stream")
        # Bersihkan parameter (e.g. "image/jpeg; charset=utf-8" → "image/jpeg")
        mime_type = content_type.split(";")[0].strip()
        
        return response.content, mime_type
    except Exception as e:
        raise RuntimeError(f"Gagal mendownload media Twilio: {e}")


def media_to_base64(media_url: str) -> dict:
    """
    Download media dari Twilio dan konversi ke base64.
    
    Returns:
        dict dengan keys: file_base64, file_mime, file_name, size_bytes
    """
    data, mime_type = download_media(media_url)
    
    # Tentukan ekstensi file
    ext = mimetypes.guess_extension(mime_type) or ".bin"
    # Fix beberapa ekstensi yang aneh dari mimetypes library
    ext_map = {
        ".jpe": ".jpg",
        ".jpeg": ".jpg",
    }
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


def get_media_description(mime_type: str) -> str:
    """
    Mengembalikan deskripsi singkat jenis media untuk log.
    """
    if mime_type.startswith("image/"):
        return "🖼️ Gambar"
    elif mime_type.startswith("audio/"):
        return "🎵 Audio"
    elif mime_type.startswith("video/"):
        return "🎬 Video"
    elif "pdf" in mime_type:
        return "📄 PDF"
    elif "word" in mime_type or "document" in mime_type:
        return "📝 Dokumen"
    else:
        return "📎 File"
