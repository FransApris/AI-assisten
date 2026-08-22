"""
auth_fad2beth.py — OAuth setup khusus untuk fad2beth@gmail.com
Jalankan: python auth_fad2beth.py
"""
import os
import sys
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/documents',
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/tasks',
]

BASE_DIR      = Path(__file__).parent
CLIENT_SECRET = str(BASE_DIR / "client_secret.json")
TOKEN_FILE    = str(BASE_DIR / "token_fad2beth.json")


def main():
    # Hapus token lama agar login fresh
    if os.path.exists(TOKEN_FILE):
        os.remove(TOKEN_FILE)
        print(f"[INFO] Token lama dihapus: {TOKEN_FILE}")

    print("=" * 55)
    print("  APRIS OAuth — fad2beth@gmail.com")
    print("=" * 55)
    print()
    print("[INFO] Membuka browser untuk login fad2beth@gmail.com...")
    print("[INFO] Pastikan Anda memilih akun fad2beth@gmail.com di browser!")
    print()

    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES)

    # port=0 → OS pilih port tersedia secara otomatis (tidak ada konflik)
    creds = flow.run_local_server(
        port=0,
        open_browser=True,
        access_type='offline',
        prompt='consent',
    )

    # Simpan token
    with open(TOKEN_FILE, 'w') as f:
        f.write(creds.to_json())
    print(f"[OK] Token tersimpan: {TOKEN_FILE}")

    # Verifikasi koneksi
    print()
    print("[INFO] Verifikasi koneksi Gmail...")
    svc     = build('gmail', 'v1', credentials=creds)
    profile = svc.users().getProfile(userId='me').execute()
    print(f"  ✅ Terkoneksi sebagai : {profile['emailAddress']}")
    print(f"     Total pesan       : {profile['messagesTotal']}")
    print()
    print("=" * 55)
    print("  Setup fad2beth selesai!")
    print("=" * 55)


if __name__ == "__main__":
    main()
