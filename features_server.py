"""
features_server.py — APRIS Level 1 Features API Server
========================================================
Flask REST API untuk semua fitur Level 1 APRIS.

Endpoints:
  GET  /status                  — health check semua fitur
  POST /voice/transcribe        — STT: audio → teks
  POST /voice/synthesize        — TTS: teks → audio MP3
  POST /vision/analyze          — analisis gambar (upload file)
  POST /vision/analyze-url      — analisis gambar dari URL
  POST /search                  — web search
  POST /search/news             — cari berita terbaru
  POST /remind/set              — buat pengingat
  GET  /remind/list             — daftar semua reminder
  DELETE /remind/<id>           — batalkan reminder

Jalankan: python features_server.py
Port    : 5051 (lokal) / PORT env var (Railway)
"""

import os
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timedelta

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SERVER_PORT = int(os.getenv("PORT", os.getenv("FEATURES_SERVER_PORT", 5051)))
IS_RAILWAY  = bool(os.getenv("RAILWAY_ENVIRONMENT"))

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo(os.getenv("TZ", "Asia/Jakarta"))
except Exception:
    from datetime import timezone
    TZ = timezone(timedelta(hours=7))

# ---------------------------------------------------------------------------
# Flask App
# ---------------------------------------------------------------------------
app = Flask(__name__)
CORS(app)

FEATURES_STATUS = {
    "voice"   : False,
    "vision"  : False,
    "search"  : False,
    "reminder": False,
}


def _check_features():
    """Cek fitur mana yang tersedia (dependensi terinstall)."""
    import importlib.util
    # whisper & gtts adalah dependensi opsional (voice/TTS) — tidak perlu diinstall lokal
    if (importlib.util.find_spec("whisper") is not None and
            importlib.util.find_spec("gtts") is not None):
        FEATURES_STATUS["voice"] = True
    try:
        from google import genai
        FEATURES_STATUS["vision"] = True
    except ImportError:
        pass
    try:
        from duckduckgo_search import DDGS
        FEATURES_STATUS["search"] = True
    except ImportError:
        pass
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        FEATURES_STATUS["reminder"] = True
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# Health Check
# ---------------------------------------------------------------------------
@app.route("/status")
@app.route("/health")
def status():
    _check_features()
    return jsonify({
        "status"  : "online",
        "version" : "APRIS Features Server v1.0",
        "env"     : "railway" if IS_RAILWAY else "local",
        "features": FEATURES_STATUS,
        "time"    : datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S WIB"),
        "endpoints": {
            "voice_stt"      : "POST /voice/transcribe",
            "voice_tts"      : "POST /voice/synthesize",
            "vision_analyze" : "POST /vision/analyze",
            "web_search"     : "POST /search",
            "news_search"    : "POST /search/news",
            "set_reminder"   : "POST /remind/set",
            "list_reminders" : "GET /remind/list",
            "cancel_reminder": "DELETE /remind/<id>",
        }
    })


# ---------------------------------------------------------------------------
# VOICE — Speech to Text
# ---------------------------------------------------------------------------
@app.route("/voice/transcribe", methods=["POST"])
def voice_transcribe():
    """
    Transkripsi file audio → teks.
    Form-data: file=<audio file>, language=id (opsional)
    """
    if "file" not in request.files:
        return jsonify({"error": "Tidak ada file audio. Kirim dengan key 'file'."}), 400

    audio_file = request.files["file"]
    language   = request.form.get("language", "id")

    # Simpan file sementara
    suffix = Path(audio_file.filename).suffix or ".ogg"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        audio_file.save(tmp.name)
        tmp_path = tmp.name

    try:
        from features.voice import transcribe
        result = transcribe(tmp_path, language=language)
        return jsonify({"status": "ok", **result})
    except ImportError as e:
        return jsonify({"error": str(e), "hint": "pip install openai-whisper"}), 503
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        Path(tmp_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# VOICE — Text to Speech
# ---------------------------------------------------------------------------
@app.route("/voice/synthesize", methods=["POST"])
def voice_synthesize():
    """
    Ubah teks → audio MP3.
    JSON: {"text": "...", "lang": "id"}
    Returns: file MP3 (download) atau JSON dengan path jika return_path=true
    """
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    lang = data.get("lang", "id")
    return_path = data.get("return_path", False)

    if not text:
        return jsonify({"error": "Field 'text' tidak boleh kosong."}), 400

    try:
        from features.voice import synthesize
        result = synthesize(text, lang=lang)
        if return_path:
            return jsonify({"status": "ok", **result})
        return send_file(result["file_path"], mimetype="audio/mpeg",
                         as_attachment=True, download_name=result["file_name"])
    except ImportError as e:
        return jsonify({"error": str(e), "hint": "pip install gTTS"}), 503
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# VISION — Analyze Image (upload)
# ---------------------------------------------------------------------------
@app.route("/vision/analyze", methods=["POST"])
def vision_analyze():
    """
    Analisis gambar yang diupload.
    Form-data: file=<image>, prompt=... (opsional), mode=general|ocr|document|detail
    """
    data   = request.form
    prompt = data.get("prompt", None)
    mode   = data.get("mode", "general")

    if "file" in request.files:
        img_file  = request.files["file"]
        mime_type = img_file.mimetype or "image/jpeg"
        img_bytes = img_file.read()
        try:
            from features.vision import analyze_bytes
            result = analyze_bytes(img_bytes, mime_type, prompt=prompt)
            return jsonify({"status": "ok", **result})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # JSON mode dengan path lokal
    json_data = request.get_json(silent=True) or {}
    img_path  = json_data.get("path")
    if img_path:
        try:
            from features.vision import analyze
            result = analyze(img_path, prompt=prompt, mode=mode)
            return jsonify({"status": "ok", **result})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    return jsonify({"error": "Kirim file gambar dengan key 'file' atau JSON dengan 'path'."}), 400


# ---------------------------------------------------------------------------
# VISION — Analyze from URL
# ---------------------------------------------------------------------------
@app.route("/vision/analyze-url", methods=["POST"])
def vision_analyze_url():
    """
    Analisis gambar dari URL.
    JSON: {"url": "https://...", "prompt": "...", "mode": "general"}
    """
    data   = request.get_json(silent=True) or {}
    url    = data.get("url", "").strip()
    prompt = data.get("prompt")
    mode   = data.get("mode", "general")

    if not url:
        return jsonify({"error": "Field 'url' tidak boleh kosong."}), 400

    try:
        import urllib.request, mimetypes
        with urllib.request.urlopen(url, timeout=10) as r:
            img_bytes = r.read()
            content_type = r.headers.get("Content-Type", "image/jpeg").split(";")[0]

        from features.vision import analyze_bytes
        result = analyze_bytes(img_bytes, content_type, prompt=prompt)
        return jsonify({"status": "ok", "url": url, **result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# SEARCH — Web Search
# ---------------------------------------------------------------------------
@app.route("/search", methods=["POST"])
def web_search():
    """
    Cari informasi dari internet.
    JSON: {"query": "...", "max_results": 5, "summarize": true}
    """
    data        = request.get_json(silent=True) or {}
    query       = (data.get("query") or "").strip()
    max_results = data.get("max_results", 5)
    summarize   = data.get("summarize", True)

    if not query:
        return jsonify({"error": "Field 'query' tidak boleh kosong."}), 400

    try:
        from features.search import search
        result = search(query, max_results=max_results, summarize=summarize)
        return jsonify({"status": "ok", **result})
    except ImportError as e:
        return jsonify({"error": str(e), "hint": "pip install duckduckgo-search"}), 503
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# SEARCH — News
# ---------------------------------------------------------------------------
@app.route("/search/news", methods=["POST"])
def news_search():
    """
    Cari berita terbaru.
    JSON: {"query": "...", "max_results": 5}
    """
    data        = request.get_json(silent=True) or {}
    query       = (data.get("query") or "").strip()
    max_results = data.get("max_results", 5)

    if not query:
        return jsonify({"error": "Field 'query' tidak boleh kosong."}), 400

    try:
        from features.search import search_news
        result = search_news(query, max_results=max_results, summarize=True)
        return jsonify({"status": "ok", **result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# REMINDER — Set
# ---------------------------------------------------------------------------
@app.route("/remind/set", methods=["POST"])
def remind_set():
    """
    Buat pengingat baru.
    JSON: {
      "message"       : "Teks pengingat",
      "target"        : "console" | nomor WA,
      "run_at"        : "2025-12-31 08:00",   ← salah satu wajib
      "delay_minutes" : 30,                    ←
      "repeat"        : "daily" | "hourly" | "weekly" | null
    }
    """
    data = request.get_json(silent=True) or {}
    msg  = (data.get("message") or "").strip()
    if not msg:
        return jsonify({"error": "Field 'message' tidak boleh kosong."}), 400

    try:
        from features.reminder import set_reminder
        result = set_reminder(
            message       = msg,
            target        = data.get("target", "console"),
            run_at        = data.get("run_at"),
            delay_minutes = data.get("delay_minutes"),
            repeat        = data.get("repeat"),
        )
        return jsonify({"status": "ok", **result})
    except ImportError as e:
        return jsonify({"error": str(e), "hint": "pip install apscheduler sqlalchemy"}), 503
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# REMINDER — List
# ---------------------------------------------------------------------------
@app.route("/remind/list", methods=["GET"])
def remind_list():
    """Ambil semua reminder yang aktif."""
    try:
        from features.reminder import list_reminders
        reminders = list_reminders()
        return jsonify({"status": "ok", "count": len(reminders), "reminders": reminders})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# REMINDER — Cancel
# ---------------------------------------------------------------------------
@app.route("/remind/<rid>", methods=["DELETE"])
def remind_cancel(rid: str):
    """Batalkan reminder berdasarkan ID."""
    try:
        from features.reminder import cancel_reminder
        result = cancel_reminder(rid)
        return jsonify({"status": "ok", **result})
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    _check_features()
    available = [k for k, v in FEATURES_STATUS.items() if v]
    missing   = [k for k, v in FEATURES_STATUS.items() if not v]

    print(f"\n{'='*52}")
    print(f"  APRIS Features Server v1.0")
    print(f"  Port    : {SERVER_PORT}")
    print(f"  Env     : {'Railway' if IS_RAILWAY else 'Lokal'}")
    print(f"  Aktif   : {', '.join(available) if available else 'tidak ada'}")
    if missing:
        print(f"  Butuh install: {', '.join(missing)}")
    if not IS_RAILWAY:
        print(f"  URL     : http://localhost:{SERVER_PORT}")
        print(f"  Status  : http://localhost:{SERVER_PORT}/status")
    print(f"{'='*52}\n")

    app.run(host="0.0.0.0", port=SERVER_PORT, debug=False)
