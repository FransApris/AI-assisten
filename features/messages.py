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
    "Halo! Saya *APRIS* — asisten virtual berbasis AI yang siap membantu Anda. 👋\n\n"
    "Saya bisa membantu dengan:\n"
    "- Pertanyaan & riset informasi\n"
    "- Produktivitas & manajemen tugas\n"
    "- Coding & debugging\n"
    "- Penulisan & ringkasan dokumen\n"
    "- Dan banyak lagi!\n\n"
    "Langsung ketik pertanyaan atau instruksi Anda, saya siap membantu! 💡"
)

WELCOME_EN_TEXT = (
    "Hi there! I'm *APRIS*, your AI assistant ready to help. 👋\n\n"
    "I can assist with research, productivity, coding, writing, and much more.\n\n"
    "Just type your question or task below — let's get it done! 💡"
)

WELCOME_MENU_TEXT = (
    "Halo! *APRIS* siap bantu. Anda bisa langsung chat atau gunakan format singkat berikut:\n\n"
    "🛠️ *[Code]* — Debugging, refactoring, atau query database\n"
    "📝 *[Draft]* — Buat draf email, pesan, atau struktur dokumen\n"
    "💡 *[Ide]* — Brainstorming solusi atau konsep proyek\n"
    "📊 *[Ringkas]* — Tempel teks/artikel panjang untuk diambil poin intinya\n\n"
    "Ketik kebutuhan Anda langsung di bawah ini!"
)

def get_welcome_message() -> str:
    """Kembalikan pesan welcome sesuai konfigurasi WA_WELCOME_MODE."""
    mode = os.getenv("WA_WELCOME_MODE", "id").lower()
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
    return f"_Halo{name_part} pesan diterima. APRIS sedang memproses..._"


def get_progress_message() -> str:
    return "_Sedang memproses dan menganalisis... Sebentar ya._"


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
# Whitelist / Invite / Flood
# ---------------------------------------------------------------------------
def get_invite_prompt() -> str:
    """Pesan untuk user baru yang belum terdaftar (invite-only mode)."""
    return (
        "Halo! 👋 Saya *APRIS*, asisten virtual berbasis AI.\n\n"
        "Untuk menggunakan APRIS, Anda memerlukan *kode undangan*.\n\n"
        "Silakan *balas pesan ini* dengan kode undangan yang Anda terima dari admin.\n\n"
        "_Belum punya kode undangan? Hubungi administrator APRIS._"
    )

def get_registered_message(name: str = "") -> str:
    """Pesan konfirmasi setelah user berhasil daftar."""
    name_part = f" *{name.split()[0]}*" if name else ""
    return (
        f"Selamat datang{name_part}! Akses Anda telah diaktifkan. ✅\n\n"
        "Saya *APRIS*, asisten virtual AI Anda. Langsung ketik pertanyaan "
        "atau instruksi pertama Anda!"
    )

def get_invalid_code_message() -> str:
    """Pesan saat kode undangan salah."""
    return (
        "Kode undangan tidak valid. Silakan periksa kembali kode Anda dan coba lagi. ❌\n\n"
        "_Butuh bantuan? Hubungi administrator APRIS._"
    )

WHITELIST_BLOCKED = "Maaf, nomor Anda belum terdaftar untuk menggunakan APRIS."
FLOOD_WARNING = (
    "Terlalu banyak pesan dalam waktu singkat. "
    "Mohon tunggu beberapa menit sebelum mengirim lagi."
)


# ---------------------------------------------------------------------------
# Interactive Welcome Payload (WhatsApp Cloud API format → dikonversi backend)
# ---------------------------------------------------------------------------
def get_welcome_interactive() -> dict:
    """
    Kembalikan payload JSON interactive buttons untuk welcome message.
    Dikirim via green_api.send_interactive_from_cloud_api().
    """
    return {
        "messaging_product": "whatsapp",
        "type"             : "interactive",
        "interactive"      : {
            "type"  : "button",
            "header": {"type": "text", "text": "⚡ Selamat datang di APRIS"},
            "body"  : {
                "text": (
                    "Saya siap membantu produktivitas harian, diskusi teknis, "
                    "coding, riset, dan banyak lagi.\n\n"
                    "Mulai dari mana?"
                )
            },
            "footer": {"text": "APRIS v4.1 — Adaptive Personal Response & Intelligent System"},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": "welcome_productivity", "title": "📋 Produktivitas"}},
                    {"type": "reply", "reply": {"id": "welcome_info",         "title": "🔍 Cek Info"}},
                    {"type": "reply", "reply": {"id": "welcome_freeask",      "title": "💬 Tanya Bebas"}},
                ]
            }
        }
    }

