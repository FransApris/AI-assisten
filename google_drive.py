import os
import shutil
from pathlib import Path
from googleapiclient.discovery import build
try:
    from PyPDF2 import PdfReader
except ImportError:
    pass

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# Scopes yang dibutuhkan untuk membaca dan menulis ke Google Drive & Docs
SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/documents',
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/gmail.modify',  # Baca, kirim, balas, tandai, label
    'https://www.googleapis.com/auth/tasks',
]

CLIENT_SECRET_FILE = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET",
                   str(Path(__file__).parent / "client_secret.json"))

# ---------------------------------------------------------------------------
# Multi-Account Support
# ---------------------------------------------------------------------------
# Daftar akun yang didukung. Key = alias akun, Value = file token-nya.
ACCOUNTS = {
    "apris":    str(Path(__file__).parent / "token_apris.json"),
    "fad2beth": str(Path(__file__).parent / "token_fad2beth.json"),
}
DEFAULT_ACCOUNT = "apris"

# Mapping hint email → alias (untuk kemudahan)
EMAIL_ALIAS = {
    "apris@komunio.org":  "apris",
    "fad2beth@gmail.com": "fad2beth",
}

# Token lama (single-account) untuk backward-compatibility
TOKEN_FILE = str(Path(__file__).parent / "token.json")

# ---------------------------------------------------------------------------
# Cloud Deployment Support (Railway/Heroku)
# ---------------------------------------------------------------------------
# Jika ada env var berisi JSON, tulis ke file agar bisa dibaca oleh library
env_to_file = {
    "GOOGLE_CLIENT_SECRET_JSON": CLIENT_SECRET_FILE,
    "GOOGLE_TOKEN_APRIS_JSON": ACCOUNTS["apris"],
    "GOOGLE_TOKEN_FAD2BETH_JSON": ACCOUNTS["fad2beth"]
}

for env_var, file_path in env_to_file.items():
    env_content = os.getenv(env_var)
    if env_content and not os.path.exists(file_path):
        try:
            with open(file_path, "w") as f:
                f.write(env_content)
            print(f"[Init] {os.path.basename(file_path)} dibuat dari environment variable.")
        except Exception as e:
            print(f"[Init] Gagal membuat {os.path.basename(file_path)} dari env var: {e}")



def _resolve_account(account: str) -> str:
    """Resolve alias atau email address menjadi key di ACCOUNTS."""
    # Jika berupa email, konversi ke alias
    if account in EMAIL_ALIAS:
        account = EMAIL_ALIAS[account]
    if account not in ACCOUNTS:
        raise ValueError(f"Akun '{account}' tidak dikenal. Pilih: {list(ACCOUNTS.keys())}")
    return account


def get_credentials(account: str = DEFAULT_ACCOUNT):
    """
    Autentikasi OAuth2 untuk akun tertentu dan kembalikan credentials.

    Args:
        account: alias akun ('apris' / 'fad2beth') atau email lengkap.

    Flow:
        - token ada & valid       → langsung pakai (tanpa login)
        - token expired           → refresh otomatis (tanpa login)
        - refresh gagal / no token → buka browser untuk login ulang
    """
    account = _resolve_account(account)
    token_file = ACCOUNTS[account]

    # Migrasi token lama (token.json) → token_apris.json jika belum ada
    if account == DEFAULT_ACCOUNT and not os.path.exists(token_file) and os.path.exists(TOKEN_FILE):
        shutil.copy(TOKEN_FILE, token_file)
        print(f"[OAuth] Token lama dimigrasikan ke {token_file}")

    creds = None
    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                print(f"[OAuth:{account}] Token di-refresh otomatis ✅")
            except Exception as e:
                print(f"[OAuth:{account}] Refresh gagal: {e}. Login ulang...")
                os.remove(token_file)
                creds = None

        if not creds or not creds.valid:
            if not os.path.exists(CLIENT_SECRET_FILE):
                raise FileNotFoundError(f"client_secret.json tidak ditemukan: {CLIENT_SECRET_FILE}")
            print(f"[OAuth:{account}] Membuka browser untuk login akun '{account}'...")
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
            creds = flow.run_local_server(
                port=0,
                open_browser=True,
                access_type='offline',
                prompt='consent',
            )
            print(f"[OAuth:{account}] Login berhasil ✅")

        with open(token_file, 'w') as f:
            f.write(creds.to_json())

    return creds


def get_drive_service(account: str = DEFAULT_ACCOUNT):
    """Kembalikan service Google Drive API untuk akun tertentu."""
    return build('drive', 'v3', credentials=get_credentials(account))


def get_docs_service(account: str = DEFAULT_ACCOUNT):
    """Kembalikan service Google Docs API untuk akun tertentu."""
    return build('docs', 'v1', credentials=get_credentials(account))

def get_apris_brain_id(service):
    """Mencari folder 'APRIS_Brain' yang dibagikan ke Service Account."""
    results = service.files().list(
        q="name='APRIS_Brain' and mimeType='application/vnd.google-apps.folder'",
        spaces='drive',
        fields="files(id, name)"
    ).execute()
    
    items = results.get('files', [])
    if not items:
        return None
    return items[0]['id']

def setup_folders(account: str = DEFAULT_ACCOUNT) -> tuple:
    """
    Memastikan sub-folder Knowledge_Base, Generated_Docs, Backups ada
    di Google Drive akun yang dipilih.

    Args:
        account: alias akun ('apris' / 'fad2beth') atau email lengkap.
    """
    service = get_drive_service(account)
    brain_id = get_apris_brain_id(service)

    if not brain_id:
        return False, "Folder 'APRIS_Brain' belum ditemukan."

    subfolders = ['Knowledge_Base', 'Generated_Docs', 'Backups']
    created = []

    for sub in subfolders:
        res = service.files().list(
            q=f"name='{sub}' and '{brain_id}' in parents and mimeType='application/vnd.google-apps.folder'",
            spaces='drive',
            fields="files(id, name)"
        ).execute()

        if not res.get('files', []):
            folder_metadata = {
                'name': sub,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [brain_id]
            }
            service.files().create(body=folder_metadata, fields='id').execute()
            created.append(sub)

    msg = f"Setup selesai. ID APRIS_Brain: {brain_id}."
    if created:
        msg += f" Dibuat sub-folder baru: {', '.join(created)}"
    else:
        msg += " (Sub-folder sudah lengkap)"

    return True, msg

def create_google_doc(title: str, content: str, account: str = DEFAULT_ACCOUNT) -> str:
    """
    Membuat file Google Docs baru di folder Generated_Docs milik akun yang dipilih,
    mengisinya dengan konten, dan mengembalikan URL file tersebut.

    Args:
        title:   Judul dokumen.
        content: Konten teks dokumen.
        account: Akun pemilik ('apris' / 'fad2beth') atau email lengkap.
    """
    drive_service = get_drive_service(account)
    docs_service  = get_docs_service(account)

    brain_id = get_apris_brain_id(drive_service)
    if not brain_id:
        return "Error: Folder APRIS_Brain tidak ditemukan."

    # Cari folder Generated_Docs
    res = drive_service.files().list(
        q=f"name='Generated_Docs' and '{brain_id}' in parents and mimeType='application/vnd.google-apps.folder'",
        spaces='drive',
        fields="files(id)"
    ).execute()

    items = res.get('files', [])
    if not items:
        return "Error: Folder Generated_Docs tidak ditemukan."
    gen_folder_id = items[0]['id']

    # 1. Buat dokumen kosong di dalam folder tersebut
    file_metadata = {
        'name': title,
        'mimeType': 'application/vnd.google-apps.document',
        'parents': [gen_folder_id]
    }
    doc    = drive_service.files().create(body=file_metadata, fields='id').execute()
    doc_id = doc.get('id')

    # 2. Masukkan konten teks ke dalam dokumen (via Docs API)
    requests = [
        {
            'insertText': {
                'location': {'index': 1},
                'text': content
            }
        }
    ]
    docs_service.documents().batchUpdate(
        documentId=doc_id, body={'requests': requests}
    ).execute()

    # 3. Kembalikan link
    return f"https://docs.google.com/document/d/{doc_id}/edit"

if __name__ == "__main__":
    try:
        ok, msg = setup_folders()
        print(msg)
    except Exception as e:
        print(f"Error: {e}")

