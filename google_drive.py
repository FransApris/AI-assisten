import os
import io
from pathlib import Path
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
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
    'https://www.googleapis.com/auth/calendar'
]

CLIENT_SECRET_FILE = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", 
                   str(Path(__file__).parent / "client_secret.json"))
TOKEN_FILE = str(Path(__file__).parent / "token.json")

def get_credentials():
    """Autentikasi menggunakan OAuth2 dan kembalikan credentials."""
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CLIENT_SECRET_FILE):
                raise FileNotFoundError(f"File kredensial OAuth tidak ditemukan: {CLIENT_SECRET_FILE}")
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
            creds = flow.run_local_server(port=8080, open_browser=False)
            
        # Simpan credentials untuk penggunaan berikutnya
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
            
    return creds

def get_drive_service():
    """Autentikasi dan kembalikan service Google Drive API."""
    creds = get_credentials()
    return build('drive', 'v3', credentials=creds)

def get_docs_service():
    """Autentikasi dan kembalikan service Google Docs API."""
    creds = get_credentials()
    return build('docs', 'v1', credentials=creds)

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

def setup_folders():
    """Memastikan sub-folder Knowledge_Base, Generated_Docs, Backups ada."""
    service = get_drive_service()
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
            folder = service.files().create(body=folder_metadata, fields='id').execute()
            created.append(sub)
            
    msg = f"Setup selesai. ID APRIS_Brain: {brain_id}."
    if created:
        msg += f" Dibuat sub-folder baru: {', '.join(created)}"
    else:
        msg += " (Sub-folder sudah lengkap)"
        
    return True, msg

def create_google_doc(title: str, content: str) -> str:
    """
    Membuat file Google Docs baru di folder Generated_Docs, 
    mengisinya dengan konten, dan mengembalikan URL file tersebut.
    """
    drive_service = get_drive_service()
    docs_service = get_docs_service()
    
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
    doc = drive_service.files().create(body=file_metadata, fields='id').execute()
    doc_id = doc.get('id')
    
    # 2. Masukkan konten teks ke dalam dokumen (via Docs API)
    requests = [
        {
            'insertText': {
                'location': {
                    'index': 1,
                },
                'text': content
            }
        }
    ]
    docs_service.documents().batchUpdate(
        documentId=doc_id, body={'requests': requests}).execute()
        
    # 3. Kembalikan link
    return f"https://docs.google.com/document/d/{doc_id}/edit"

if __name__ == "__main__":
    try:
        ok, msg = setup_folders()
        print(msg)
    except Exception as e:
        print(f"Error: {e}")

