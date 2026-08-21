import base64
from googleapiclient.discovery import build
from google_drive import get_credentials

def get_recent_emails(limit=5):
    """Mengambil email terbaru yang belum dibaca dari Inbox."""
    try:
        creds = get_credentials()
        service = build('gmail', 'v1', credentials=creds)
        
        # Ambil list pesan (UNREAD)
        results = service.users().messages().list(userId='me', labelIds=['INBOX', 'UNREAD'], maxResults=limit).execute()
        messages = results.get('messages', [])
        
        if not messages:
            return "Tidak ada email baru yang belum dibaca di Kotak Masuk."
            
        email_summaries = []
        for msg in messages:
            msg_data = service.users().messages().get(userId='me', id=msg['id'], format='full').execute()
            
            headers = msg_data.get('payload', {}).get('headers', [])
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'Tanpa Subjek')
            sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Tidak diketahui')
            date_str = next((h['value'] for h in headers if h['name'] == 'Date'), '')
            
            # Ekstrak isi pesan (body)
            body = ""
            payload = msg_data.get('payload', {})
            if 'parts' in payload:
                for part in payload['parts']:
                    if part['mimeType'] == 'text/plain':
                        data = part['body'].get('data')
                        if data:
                            body = base64.urlsafe_b64decode(data).decode('utf-8')
                            break
            else:
                data = payload.get('body', {}).get('data')
                if data:
                    body = base64.urlsafe_b64decode(data).decode('utf-8')
                    
            if len(body) > 1000:
                body = body[:1000] + " ...[potongan email]"
                
            email_summaries.append(f"Dari: {sender}\nSubjek: {subject}\nTanggal: {date_str}\nIsi:\n{body.strip()}")
            
        return "\n\n---\n\n".join(email_summaries)
        
    except Exception as e:
        return f"[Gagal mengambil email: {str(e)}]"
