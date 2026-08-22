"""
Script untuk login akun fad2beth@gmail.com dan verifikasi kedua akun.
Jalankan: python setup_accounts.py
"""
import sys
sys.path.insert(0, r"d:\APRIS FILE\AI Assisten\asisten-virtual")

from googleapiclient.discovery import build
from google_drive import get_credentials, ACCOUNTS

print("=" * 50)
print("APRIS Multi-Account Setup")
print("=" * 50)

for alias, token_file in ACCOUNTS.items():
    print(f"\n[{alias.upper()}] Mengecek koneksi...")
    try:
        creds = get_credentials(alias)
        # Test dengan Gmail profile
        svc = build('gmail', 'v1', credentials=creds)
        profile = svc.users().getProfile(userId='me').execute()
        print(f"  [OK] Terkoneksi  : {profile['emailAddress']}")
        print(f"       Total pesan : {profile['messagesTotal']}")
    except Exception as e:
        print(f"  [ERROR]          : {e}")

print("\n" + "=" * 50)
print("Setup selesai!")

