"""
features/messages.py — APRIS Message Templates
===============================================
Semua template pesan WA terpusat di sini.
Edit file ini untuk mengubah teks tanpa menyentuh logika server.
"""
import os

# ---------------------------------------------------------------------------
# Welcome / Onboarding (pengguna baru pertama kali chat)
# ---------------------------------------------------------------------------
WELCOME_ID_TEXT = (
    "Halo! Saya *APRIS* (Adaptive Personal Response & Intelligent System). ⚡\n\n"
    "Siap bantu produktivitas harian, diskusi teknis, coding, riset, atau perapian draf tulisan.\n\n"
    "Ada yang bisa saya bantu sekarang? Kirim saja instruksi atau detail tugas Anda."
)

WELCOME_EN_TEXT = (
    "Hi there! I'm *APRIS*, your AI companion for smart workflows, technical problem-solving, and everyday tasks. ⚡\n\n"
    "Drop your question, task, or context below, and let's get it done."
)

WELCOME_MENU_TEXT = (
    "Halo! *APRIS* siap bantu. Anda bisa langsung chat atau gunakan format singkat berikut:\n\n"
    "🛠️ *[Code]* — Debugging, refactoring, atau query database\n"
    "📝 *[Draft]* — Buat draf email, pesan, atau struktur dokumen\n"
    "💡 *[Ide]* — Brainstorming solusi atau konsep proyek\n"
    "📊 *[Ringkas]* — Tempel teks/artikel panjang untuk diambil poin intinya\n\n"
    "Ketik kebutuhan Anda langsung di bawah ini!"
)

# Mode welcome: 'id' | 'en' | 'menu' | 'off'
WELCOME_MODE = os.getenv("WA_WELCOME_MODE", "id")

def get_welcome_message() -> str:
    """Kembalikan pesan welcome sesuai konfigurasi WA_WELCOME_MODE."""
    mode = WELCOME_MODE.lower()
    if mode == "en":
        return WELCOME_EN_TEXT
    elif mode == "menu":
        return WELCOME_MENU_TEXT
    elif mode == "off":
        return ""
    else:
        return WELCOME_ID_TEXT


# ---------------------------------------------------------------------------
# Processing / Acknowledgment
# ---------------------------------------------------------------------------
def get_ack_message(sender_name: str = "") -> str:
    name_part = f" {sender_name.split()[0]}," if sender_name else ""
    return f"⏳ _Halo{name_part} pesan diterima. APRIS sedang memproses..._"


def get_progress_message() -> str:
    return "⚙️ _Sedang memproses dan menganalisis... Sebentar ya._"


# ---------------------------------------------------------------------------
# Maintenance Mode
# ---------------------------------------------------------------------------
MAINTENANCE_TEXT = (
    "Halo! Saat ini sistem *APRIS* sedang dalam pemeliharaan berkala "
    "agar tetap responsif. Pesan Anda tersimpan dan akan segera diproses "
    "begitu sistem kembali online. 🛠️"
)

MAINTENANCE_MODE = os.getenv("WA_MAINTENANCE_MODE", "false").lower() == "true"


# ---------------------------------------------------------------------------
# Whitelist / Flood
# ---------------------------------------------------------------------------
WHITELIST_BLOCKED = "🚫 Maaf, nomor Anda tidak terdaftar untuk menggunakan APRIS."
FLOOD_WARNING = (
    "⚠️ _Terlalu banyak pesan dalam waktu singkat. "
    "Mohon tunggu beberapa menit sebelum mengirim lagi._"
)
