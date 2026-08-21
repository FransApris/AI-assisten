"""
features/vision.py — Analisis Gambar via Gemini Vision
"""
import os, base64
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
VISION_MODEL   = os.getenv("GEMINI_VISION_MODEL", "models/gemini-2.5-flash")
SUPPORTED      = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

DEFAULT_PROMPT = """Analisis gambar ini dan berikan:
1. Deskripsi singkat isi gambar
2. Detail penting yang terlihat
3. Jika ada teks dalam gambar, transkripsi teksnya
Jawab dalam Bahasa Indonesia yang ringkas."""

MIME_MAP = {".jpg":"image/jpeg",".jpeg":"image/jpeg",".png":"image/png",
            ".webp":"image/webp",".gif":"image/gif",".bmp":"image/bmp"}


def _client():
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY tidak ditemukan")
    from google import genai
    return genai.Client(api_key=GEMINI_API_KEY)


def analyze(image_path: str, prompt: str = None, mode: str = "general") -> dict:
    """Analisis gambar dari file path."""
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"File tidak ditemukan: {image_path}")
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED:
        raise ValueError(f"Format tidak didukung: {suffix}")

    mode_prompts = {
        "general" : DEFAULT_PROMPT,
        "ocr"     : "Baca dan transkripsi SEMUA teks dalam gambar ini secara akurat. Pertahankan format asli. Jawab dalam Bahasa Indonesia.",
        "document": "Analisis dokumen/screenshot ini: 1.Jenis dokumen 2.Isi utama 3.Informasi penting 4.Transkripsi teks. Bahasa Indonesia.",
        "detail"  : "Analisis SANGAT DETAIL: deskripsi, setiap objek, warna, komposisi, teks yang ada. Bahasa Indonesia.",
    }
    final_prompt = prompt or mode_prompts.get(mode, DEFAULT_PROMPT)
    mime_type    = MIME_MAP.get(suffix, "image/jpeg")
    image_data   = base64.b64encode(path.read_bytes()).decode("utf-8")

    from google.genai import types
    response = _client().models.generate_content(
        model=VISION_MODEL,
        contents=[types.Content(role="user", parts=[
            types.Part(inline_data=types.Blob(mime_type=mime_type, data=image_data)),
            types.Part(text=final_prompt),
        ])]
    )
    return {"analysis": response.text.strip(), "model": VISION_MODEL,
            "image": path.name, "mode": mode}


def analyze_bytes(image_bytes: bytes, mime_type: str, prompt: str = None) -> dict:
    """Analisis gambar dari bytes (upload API)."""
    image_data = base64.b64encode(image_bytes).decode("utf-8")
    from google.genai import types
    response = _client().models.generate_content(
        model=VISION_MODEL,
        contents=[types.Content(role="user", parts=[
            types.Part(inline_data=types.Blob(mime_type=mime_type, data=image_data)),
            types.Part(text=prompt or DEFAULT_PROMPT),
        ])]
    )
    return {"analysis": response.text.strip(), "model": VISION_MODEL, "mime_type": mime_type}
