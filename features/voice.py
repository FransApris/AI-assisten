"""
features/voice.py — STT (Whisper) + TTS (gTTS)
"""
import os, uuid
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

AUDIO_DIR = Path(os.getenv("AUDIO_OUTPUT_DIR", "./audio_output"))
AUDIO_DIR.mkdir(parents=True, exist_ok=True)
WHISPER_MODEL_NAME = os.getenv("WHISPER_MODEL", "base")

_whisper = None

def _load_whisper():
    global _whisper
    if _whisper is None:
        try:
            import whisper
            _whisper = whisper.load_model(WHISPER_MODEL_NAME)
        except ImportError:
            raise ImportError("Jalankan: pip install openai-whisper")
    return _whisper


def transcribe(audio_path: str, language: str = "id") -> dict:
    """Transkripsi file audio → teks."""
    if not Path(audio_path).exists():
        raise FileNotFoundError(f"File tidak ditemukan: {audio_path}")
    model = _load_whisper()
    result = model.transcribe(audio_path, language=language, fp16=False, verbose=False)
    return {
        "text"    : result["text"].strip(),
        "language": result.get("language", language),
        "duration": round(result.get("duration", 0), 1),
    }


def synthesize(text: str, lang: str = "id") -> dict:
    """Ubah teks → file audio MP3."""
    if not text.strip():
        raise ValueError("Teks kosong.")
    try:
        from gtts import gTTS
    except ImportError:
        raise ImportError("Jalankan: pip install gTTS")

    text = text[:500] + ("..." if len(text) > 500 else "")
    fname = f"apris_tts_{uuid.uuid4().hex[:8]}.mp3"
    fpath = AUDIO_DIR / fname
    gTTS(text=text, lang=lang, slow=False).save(str(fpath))
    return {"file_path": str(fpath.resolve()), "file_name": fname, "chars": len(text)}


def cleanup_audio(max_files: int = 30) -> int:
    files = sorted(AUDIO_DIR.glob("apris_tts_*.mp3"), key=lambda f: f.stat().st_mtime)
    to_del = files[:-max_files] if len(files) > max_files else []
    for f in to_del: f.unlink(missing_ok=True)
    return len(to_del)
