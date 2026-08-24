"""
otp_email.py — Modul Verifikasi OTP via Email (GRATIS)
========================================================
Menggunakan Gmail API yang sudah ada di google_gmail.py
Tidak perlu Twilio, tidak perlu SMTP tambahan.

Fitur:
  - send_otp(email)        : Generate dan kirim OTP ke email
  - verify_otp(email,kode) : Verifikasi kode OTP dari user
  - request_otp(email)     : Alias untuk send_otp
  - invalidate_otp(email)  : Batalkan OTP secara manual
  - check_otp_status(email): Cek status OTP aktif
"""

import random
import string
import time
import sys
import os

# Pastikan path parent bisa diimport
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from google_gmail import send_email

# ---------------------------------------------------------------------------
# Konfigurasi
# ---------------------------------------------------------------------------

OTP_LENGTH      = 6          # Panjang kode OTP
OTP_EXPIRY_SEC  = 300        # Waktu kedaluwarsa OTP (5 menit)
MAX_ATTEMPTS    = 3          # Maksimum percobaan verifikasi per OTP
OTP_SENDER_ACC  = "apris"    # Akun Gmail pengirim (sesuai google_drive.py)

# ---------------------------------------------------------------------------
# In-memory store OTP
# Format: { "email": {"otp": "123456", "exp": timestamp, "attempts": 0} }
# ---------------------------------------------------------------------------
_otp_store: dict = {}


# ---------------------------------------------------------------------------
# Internal Helpers
# ---------------------------------------------------------------------------

def _generate_otp(length: int = OTP_LENGTH) -> str:
    """Generate kode OTP numerik acak."""
    return "".join(random.choices(string.digits, k=length))


def _cleanup_expired():
    """Bersihkan OTP yang sudah kedaluwarsa dari store."""
    now = time.time()
    expired = [email for email, data in _otp_store.items() if data["exp"] < now]
    for email in expired:
        del _otp_store[email]


def _mask_email(email: str) -> str:
    """Sensor sebagian email untuk tampilan aman. Contoh: f***@gmail.com"""
    try:
        local, domain = email.split("@", 1)
        masked_local = local[0] + "*" * (len(local) - 1) if len(local) > 1 else "*"
        return f"{masked_local}@{domain}"
    except Exception:
        return email


# ---------------------------------------------------------------------------
# 1. Kirim OTP
# ---------------------------------------------------------------------------

def send_otp(email: str, account: str = OTP_SENDER_ACC) -> dict:
    """
    Generate dan kirim kode OTP ke alamat email tujuan.

    Args:
        email   : Alamat email penerima OTP.
        account : Akun Gmail pengirim ('apris' / 'fad2beth').

    Returns:
        dict dengan keys: success (bool), message (str), masked_email (str)
    """
    if not email or "@" not in email:
        return {"success": False, "message": "Alamat email tidak valid."}

    _cleanup_expired()

    # Throttle: cek apakah OTP sebelumnya masih aktif
    if email in _otp_store:
        remaining = int(_otp_store[email]["exp"] - time.time())
        if remaining > 0:
            return {
                "success": False,
                "message": f"OTP sudah dikirim. Tunggu {remaining} detik sebelum meminta ulang.",
                "masked_email": _mask_email(email)
            }

    otp = _generate_otp()
    exp = time.time() + OTP_EXPIRY_SEC

    # Simpan ke store
    _otp_store[email] = {
        "otp"      : otp,
        "exp"      : exp,
        "attempts" : 0
    }

    # Buat konten email
    subject = "Kode Verifikasi APRIS"
    body = (
        f"Halo,\n\n"
        f"Kode verifikasi (OTP) kamu adalah:\n\n"
        f"  {otp}\n\n"
        f"Kode ini berlaku selama {OTP_EXPIRY_SEC // 60} menit.\n"
        f"Jangan bagikan kode ini kepada siapapun.\n\n"
        f"Jika kamu tidak meminta kode ini, abaikan email ini.\n\n"
        f"-- APRIS, Asisten Pribadimu"
    )

    result = send_email(to=email, subject=subject, body=body, account=account)

    if "berhasil" in result.lower():
        return {
            "success"      : True,
            "message"      : f"OTP berhasil dikirim ke {_mask_email(email)}. Berlaku {OTP_EXPIRY_SEC // 60} menit.",
            "masked_email" : _mask_email(email)
        }
    else:
        # Hapus dari store jika gagal kirim
        _otp_store.pop(email, None)
        return {
            "success"      : False,
            "message"      : f"Gagal mengirim OTP: {result}",
            "masked_email" : _mask_email(email)
        }


# Alias yang lebih ekspresif
request_otp = send_otp


# ---------------------------------------------------------------------------
# 2. Verifikasi OTP
# ---------------------------------------------------------------------------

def verify_otp(email: str, kode: str) -> dict:
    """
    Verifikasi kode OTP yang dimasukkan pengguna.

    Args:
        email : Alamat email yang OTP-nya dikirim.
        kode  : Kode OTP yang dimasukkan user.

    Returns:
        dict dengan keys: success (bool), verified (bool), message (str)
    """
    if not email or not kode:
        return {"success": False, "verified": False, "message": "Email dan kode OTP wajib diisi."}

    _cleanup_expired()

    if email not in _otp_store:
        return {
            "success"  : False,
            "verified" : False,
            "message"  : "OTP tidak ditemukan atau sudah kedaluwarsa. Minta OTP baru."
        }

    data = _otp_store[email]

    # Cek kedaluwarsa
    if time.time() > data["exp"]:
        del _otp_store[email]
        return {
            "success"  : False,
            "verified" : False,
            "message"  : "OTP sudah kedaluwarsa. Silakan minta OTP baru."
        }

    # Cek jumlah percobaan
    if data["attempts"] >= MAX_ATTEMPTS:
        del _otp_store[email]
        return {
            "success"  : False,
            "verified" : False,
            "message"  : f"Terlalu banyak percobaan gagal ({MAX_ATTEMPTS}x). OTP dibatalkan. Minta OTP baru."
        }

    # Verifikasi kode
    kode = kode.strip()
    if kode == data["otp"]:
        del _otp_store[email]  # Hapus OTP setelah berhasil digunakan (single-use)
        return {
            "success"  : True,
            "verified" : True,
            "message"  : f"Verifikasi berhasil untuk {_mask_email(email)}!"
        }
    else:
        data["attempts"] += 1
        sisa = MAX_ATTEMPTS - data["attempts"]
        return {
            "success"  : False,
            "verified" : False,
            "message"  : f"Kode OTP salah. Sisa percobaan: {sisa}x."
        }


# ---------------------------------------------------------------------------
# 3. Batalkan / Hapus OTP
# ---------------------------------------------------------------------------

def invalidate_otp(email: str) -> dict:
    """
    Batalkan OTP aktif untuk email tertentu secara manual.

    Args:
        email : Alamat email yang OTP-nya ingin dibatalkan.
    """
    if email in _otp_store:
        del _otp_store[email]
        return {"success": True, "message": f"OTP untuk {_mask_email(email)} berhasil dibatalkan."}
    return {"success": False, "message": "Tidak ada OTP aktif untuk email tersebut."}


# ---------------------------------------------------------------------------
# 4. Cek Status OTP
# ---------------------------------------------------------------------------

def check_otp_status(email: str) -> dict:
    """
    Cek apakah ada OTP aktif untuk email tertentu.

    Args:
        email : Alamat email yang ingin dicek.
    """
    _cleanup_expired()
    if email not in _otp_store:
        return {"active": False, "message": "Tidak ada OTP aktif."}

    data      = _otp_store[email]
    remaining = max(0, int(data["exp"] - time.time()))
    return {
        "active"    : True,
        "remaining" : remaining,
        "attempts"  : data["attempts"],
        "message"   : f"OTP aktif, kedaluwarsa dalam {remaining} detik. Percobaan: {data['attempts']}/{MAX_ATTEMPTS}."
    }


# ---------------------------------------------------------------------------
# Quick Test — jalankan: python otp_email.py
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_email = input("Masukkan email untuk test OTP: ").strip()
    if not test_email:
        print("Email tidak boleh kosong.")
        sys.exit(1)

    print(f"\n[1] Mengirim OTP ke {test_email}...")
    result = send_otp(test_email)
    print(f"    -> {result['message']}")

    if result["success"]:
        kode = input("\n[2] Masukkan kode OTP yang diterima: ").strip()
        verify = verify_otp(test_email, kode)
        print(f"    -> {verify['message']}")
