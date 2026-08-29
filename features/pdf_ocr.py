"""
features/pdf_ocr.py — APRIS PDF Reader & OCR
==============================================
Membaca teks dari file PDF dengan dua strategi:

1. EKSTRAKSI LANGSUNG (PyPDF2) — untuk PDF digital/selectable text
   → Cepat, tidak membutuhkan GPU atau API tambahan

2. OCR via Gemini Vision (PyMuPDF) — untuk PDF scan/gambar
   → Render halaman ke PNG → kirim ke Gemini Vision → ekstrak teks
   → Tidak butuh Tesseract atau sistem package tambahan

Cara pakai:
    from features.pdf_ocr import extract_pdf_text

    result = extract_pdf_text(file_bytes, filename="dokumen.pdf", gemini_client=client)
    print(result["text"])     # teks hasil ekstraksi
    print(result["method"])   # "pypdf2" | "gemini_ocr" | "error"
    print(result["pages"])    # jumlah halaman
"""

import io
import os
import base64
import time
from typing import Optional

# ---------------------------------------------------------------------------
# Konstanta
# ---------------------------------------------------------------------------
MAX_TEXT_CHARS   = 14000   # batas karakter teks yang dikirim ke Gemini (context window)
MAX_OCR_PAGES    = 6       # maks halaman yang di-OCR (hemat token)
MIN_TEXT_CHARS   = 80      # jika PyPDF2 hasilkan < ini per halaman → anggap scan/kosong
CHAT_MODEL       = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")


# ---------------------------------------------------------------------------
# Fungsi Utama
# ---------------------------------------------------------------------------
def extract_pdf_text(
    file_bytes: bytes,
    filename: str = "document.pdf",
    gemini_client=None,
) -> dict:
    """
    Ekstrak teks dari PDF.

    Args:
        file_bytes    : isi file PDF dalam bytes
        filename      : nama file (untuk pesan error yang informatif)
        gemini_client : instance google.genai.Client (diperlukan untuk OCR)

    Returns:
        {
          "text"   : str,             # teks hasil ekstraksi
          "method" : str,             # "pypdf2" | "gemini_ocr" | "error"
          "pages"  : int,             # jumlah halaman
          "chars"  : int,             # jumlah karakter
          "truncated": bool,          # apakah dipotong
          "error"  : str | None,      # pesan error jika ada
        }
    """
    # -----------------------------------------------------------------------
    # Langkah 1: Coba PyPDF2 (cepat, untuk PDF digital)
    # -----------------------------------------------------------------------
    try:
        from PyPDF2 import PdfReader
        reader     = PdfReader(io.BytesIO(file_bytes))
        total_pages = len(reader.pages)
        pages_text  = []

        for i, page in enumerate(reader.pages, 1):
            txt = (page.extract_text() or "").strip()
            if txt:
                pages_text.append(f"[Halaman {i}]\n{txt}")

        full_text = "\n\n".join(pages_text)
        avg_chars = len(full_text) / max(total_pages, 1)

        # Teks cukup → gunakan hasil PyPDF2
        if full_text and avg_chars >= MIN_TEXT_CHARS:
            truncated = False
            if len(full_text) > MAX_TEXT_CHARS:
                full_text = full_text[:MAX_TEXT_CHARS] + (
                    f"\n\n_...(dipotong, total {len(full_text):,} karakter dari {total_pages} halaman)_"
                )
                truncated = True
            return {
                "text"     : full_text,
                "method"   : "pypdf2",
                "pages"    : total_pages,
                "chars"    : len(full_text),
                "truncated": truncated,
                "error"    : None,
            }

        # Teks minim → kemungkinan PDF scan, lanjut ke OCR
        print(f"[PDFReader] '{filename}': teks minimal ({avg_chars:.0f} char/hlm) → coba OCR", flush=True)

    except Exception as e:
        print(f"[PDFReader] PyPDF2 gagal untuk '{filename}': {e}", flush=True)
        total_pages = 0

    # -----------------------------------------------------------------------
    # Langkah 2: OCR via Gemini Vision (untuk PDF scan/gambar)
    # -----------------------------------------------------------------------
    if gemini_client is None:
        return {
            "text"     : "",
            "method"   : "error",
            "pages"    : total_pages,
            "chars"    : 0,
            "truncated": False,
            "error"    : (
                "PDF ini berisi gambar/scan (bukan teks digital). "
                "OCR memerlukan koneksi AI — pastikan Gemini API tersedia."
            ),
        }

    return _ocr_with_gemini(file_bytes, filename, gemini_client, total_pages)


def _ocr_with_gemini(
    file_bytes: bytes,
    filename: str,
    client,
    total_pages: int = 0,
) -> dict:
    """
    OCR menggunakan PyMuPDF untuk render halaman + Gemini Vision untuk membaca.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return {
            "text"     : "",
            "method"   : "error",
            "pages"    : total_pages,
            "chars"    : 0,
            "truncated": False,
            "error"    : "PyMuPDF belum terinstall. Tambahkan 'pymupdf' ke requirements.txt",
        }

    try:
        from google.genai import types as _gtypes
    except ImportError:
        return {
            "text": "", "method": "error", "pages": total_pages,
            "chars": 0, "truncated": False,
            "error": "google-genai tidak tersedia untuk OCR.",
        }

    ocr_results = []
    doc = None

    try:
        doc         = fitz.open(stream=file_bytes, filetype="pdf")
        total_pages = doc.page_count
        pages_to_ocr = min(total_pages, MAX_OCR_PAGES)

        print(f"[PDFOcr] '{filename}': {total_pages} halaman → OCR {pages_to_ocr} hlm via Gemini", flush=True)

        for page_num in range(pages_to_ocr):
            page = doc[page_num]
            # Render halaman ke gambar (DPI 150 — cukup untuk OCR, tidak terlalu berat)
            mat  = fitz.Matrix(150 / 72, 150 / 72)
            pix  = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
            img_bytes = pix.tobytes("png")
            img_b64   = base64.b64encode(img_bytes).decode()

            # Kirim ke Gemini Vision untuk OCR
            try:
                resp = client.models.generate_content(
                    model=CHAT_MODEL,
                    contents=[
                        _gtypes.Part.from_bytes(
                            data=img_bytes,
                            mime_type="image/png",
                        ),
                        _gtypes.Part.from_text(
                            "Ekstrak semua teks dari gambar halaman PDF ini. "
                            "Pertahankan struktur asli (judul, paragraf, daftar). "
                            "Jika tidak ada teks, balas: [halaman kosong]"
                        ),
                    ]
                )
                page_text = (resp.text or "").strip()
                if page_text and page_text.lower() != "[halaman kosong]":
                    ocr_results.append(f"[Halaman {page_num + 1}]\n{page_text}")
                print(f"[PDFOcr] Halaman {page_num+1}: {len(page_text)} char", flush=True)
                time.sleep(0.5)   # rate limit

            except Exception as e:
                print(f"[PDFOcr] Halaman {page_num+1} gagal: {e}", flush=True)
                ocr_results.append(f"[Halaman {page_num+1}] _(Gagal OCR: {e})_")

        full_text  = "\n\n".join(ocr_results)
        truncated  = False

        if pages_to_ocr < total_pages:
            full_text += (
                f"\n\n_...(OCR dibatasi {MAX_OCR_PAGES} dari {total_pages} halaman "
                f"untuk efisiensi. Kirim halaman tertentu jika butuh lebih.)_"
            )
            truncated = True

        if len(full_text) > MAX_TEXT_CHARS:
            full_text  = full_text[:MAX_TEXT_CHARS] + "\n\n_(dipotong)_"
            truncated  = True

        return {
            "text"     : full_text,
            "method"   : "gemini_ocr",
            "pages"    : total_pages,
            "chars"    : len(full_text),
            "truncated": truncated,
            "error"    : None,
        }

    except Exception as e:
        return {
            "text"     : "",
            "method"   : "error",
            "pages"    : total_pages,
            "chars"    : 0,
            "truncated": False,
            "error"    : f"OCR gagal: {e}",
        }
    finally:
        if doc:
            doc.close()
