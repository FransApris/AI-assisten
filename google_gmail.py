"""
google_gmail.py — APRIS Gmail Integration
==========================================
Fitur:
  - get_recent_emails()     : Baca email masuk (UNREAD)
  - search_emails()         : Cari email berdasarkan query
  - summarize_inbox()       : Rangkum semua email baru dalam 1 ringkasan
  - send_email()            : Kirim email baru
  - reply_email()           : Balas email berdasarkan message ID
  - mark_as_read()          : Tandai email sebagai sudah dibaca
"""
import base64
from email.message import EmailMessage
from googleapiclient.discovery import build
from google_drive import get_credentials


# ---------------------------------------------------------------------------
# Helper Internal
# ---------------------------------------------------------------------------

def _get_service():
    """Buat Gmail API service (dipanggil ulang tiap fungsi untuk thread-safety)."""
    creds = get_credentials()
    return build('gmail', 'v1', credentials=creds)


def _extract_body(payload: dict) -> str:
    """Ekstrak teks plain dari payload Gmail API (mendukung multipart)."""
    body = ""
    if 'parts' in payload:
        # Cari text/plain di top-level parts
        for part in payload['parts']:
            if part.get('mimeType') == 'text/plain':
                data = part.get('body', {}).get('data')
                if data:
                    body = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
                    break
        # Jika tidak ditemukan, coba nested multipart
        if not body:
            for part in payload['parts']:
                if 'parts' in part:
                    for sub in part['parts']:
                        if sub.get('mimeType') == 'text/plain':
                            data = sub.get('body', {}).get('data')
                            if data:
                                body = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
                                break
    else:
        data = payload.get('body', {}).get('data')
        if data:
            body = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
    return body.strip()


def _parse_message(msg_data: dict, max_body: int = 1000) -> dict:
    """Parsing satu pesan Gmail menjadi dict yang bersih."""
    headers = msg_data.get('payload', {}).get('headers', [])
    subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'Tanpa Subjek')
    sender  = next((h['value'] for h in headers if h['name'] == 'From'),    'Tidak diketahui')
    to_addr = next((h['value'] for h in headers if h['name'] == 'To'),      '')
    date_str = next((h['value'] for h in headers if h['name'] == 'Date'),   '')
    msg_id_hdr = next((h['value'] for h in headers if h['name'] == 'Message-ID'), '')

    body = _extract_body(msg_data.get('payload', {}))
    if len(body) > max_body:
        body = body[:max_body] + " ...[dipotong]"

    return {
        "id"         : msg_data.get('id', ''),
        "thread_id"  : msg_data.get('threadId', ''),
        "message_id" : msg_id_hdr,
        "subject"    : subject,
        "from"       : sender,
        "to"         : to_addr,
        "date"       : date_str,
        "body"       : body,
        "snippet"    : msg_data.get('snippet', ''),
    }


def _format_email(e: dict, index: int = None) -> str:
    """Format dict email menjadi teks yang siap ditampilkan ke pengguna."""
    prefix = f"[{index}] " if index is not None else ""
    return (
        f"{prefix}📧 *{e['subject']}*\n"
        f"   Dari: {e['from']}\n"
        f"   Tanggal: {e['date']}\n"
        f"   ID: `{e['id']}`\n"
        f"   Isi:\n{e['body'] or e['snippet']}"
    )


# ---------------------------------------------------------------------------
# 1. Baca Email (UNREAD)
# ---------------------------------------------------------------------------

def get_recent_emails(limit: int = 5) -> str:
    """
    Mengambil email terbaru yang belum dibaca dari Inbox.
    Returns teks berformat untuk ditampilkan di chat.
    """
    try:
        service = _get_service()
        results = service.users().messages().list(
            userId='me', labelIds=['INBOX', 'UNREAD'], maxResults=limit
        ).execute()
        messages = results.get('messages', [])

        if not messages:
            return "Tidak ada email baru yang belum dibaca di Kotak Masuk."

        parsed = []
        for i, msg in enumerate(messages, 1):
            msg_data = service.users().messages().get(
                userId='me', id=msg['id'], format='full'
            ).execute()
            parsed.append(_format_email(_parse_message(msg_data), index=i))

        return "\n\n---\n\n".join(parsed)
    except Exception as e:
        return f"[Gagal mengambil email: {str(e)}]"


# ---------------------------------------------------------------------------
# 2. Cari Email
# ---------------------------------------------------------------------------

def search_emails(query: str, limit: int = 5) -> str:
    """
    Mencari email berdasarkan query Gmail (dari, subjek, kata kunci, dll).
    Contoh query: 'from:budi subject:laporan after:2026/08/01'
    """
    if not query or not query.strip():
        return "Query pencarian email tidak boleh kosong."
    try:
        service = _get_service()
        results = service.users().messages().list(
            userId='me', q=query.strip(), maxResults=limit
        ).execute()
        messages = results.get('messages', [])

        if not messages:
            return f"Tidak ada email ditemukan untuk pencarian: '{query}'."

        parsed = []
        for i, msg in enumerate(messages, 1):
            msg_data = service.users().messages().get(
                userId='me', id=msg['id'], format='full'
            ).execute()
            parsed.append(_format_email(_parse_message(msg_data), index=i))

        header = f"Hasil pencarian '{query}' ({len(parsed)} email):\n\n"
        return header + "\n\n---\n\n".join(parsed)
    except Exception as e:
        return f"[Gagal mencari email: {str(e)}]"


# ---------------------------------------------------------------------------
# 3. Rangkum Inbox
# ---------------------------------------------------------------------------

def summarize_inbox(limit: int = 10) -> str:
    """
    Mengambil email terbaru yang belum dibaca, lalu mengembalikan
    data terstruktur (list of dict) agar bisa dirangkum oleh LLM.
    """
    try:
        service = _get_service()
        results = service.users().messages().list(
            userId='me', labelIds=['INBOX', 'UNREAD'], maxResults=limit
        ).execute()
        messages = results.get('messages', [])

        if not messages:
            return "Tidak ada email baru yang belum dibaca untuk dirangkum."

        lines = [f"Ringkasan Inbox ({len(messages)} email baru belum dibaca):\n"]
        for i, msg in enumerate(messages, 1):
            msg_data = service.users().messages().get(
                userId='me', id=msg['id'], format='metadata',
                metadataHeaders=['Subject', 'From', 'Date']
            ).execute()
            headers   = msg_data.get('payload', {}).get('headers', [])
            subject   = next((h['value'] for h in headers if h['name'] == 'Subject'), 'Tanpa Subjek')
            sender    = next((h['value'] for h in headers if h['name'] == 'From'), '?')
            snippet   = msg_data.get('snippet', '')[:120]
            lines.append(f"{i}. *{subject}*\n   Dari: {sender}\n   Preview: {snippet}...\n")

        return "\n".join(lines)
    except Exception as e:
        return f"[Gagal merangkum inbox: {str(e)}]"


# ---------------------------------------------------------------------------
# 4. Kirim Email
# ---------------------------------------------------------------------------

def send_email(to: str, subject: str, body: str) -> str:
    """
    Mengirim email baru melalui Gmail API.
    'to' dapat berisi lebih dari satu alamat (pisah dengan koma).
    """
    if not to or not subject or not body:
        return "Gagal: 'to', 'subject', dan 'body' semuanya wajib diisi."
    try:
        service = _get_service()

        message = EmailMessage()
        message.set_content(body)
        message['To']      = to
        message['Subject'] = subject
        message['From']    = 'me'

        encoded  = base64.urlsafe_b64encode(message.as_bytes()).decode()
        result   = service.users().messages().send(
            userId="me", body={'raw': encoded}
        ).execute()
        return f"Email berhasil dikirim ke {to} (ID: {result['id']})"
    except Exception as e:
        return f"Gagal mengirim email: {str(e)}"


# ---------------------------------------------------------------------------
# 5. Balas Email (Reply)
# ---------------------------------------------------------------------------

def reply_email(message_id: str, body: str) -> str:
    """
    Membalas email berdasarkan Gmail Message ID (bukan Message-ID header).
    'message_id' adalah ID hex pendek yang terlihat di output get_recent_emails/search_emails.
    """
    if not message_id or not body:
        return "Gagal: 'message_id' dan 'body' balasan wajib diisi."
    try:
        service = _get_service()

        # Ambil pesan asli untuk mendapatkan thread_id dan header
        orig = service.users().messages().get(
            userId='me', id=message_id, format='full'
        ).execute()
        parsed  = _parse_message(orig)
        thread_id = orig.get('threadId', '')

        # Bangun email balasan dengan header Reply-To yang benar
        reply = EmailMessage()
        reply.set_content(body)
        reply['To']          = parsed['from']
        reply['Subject']     = f"Re: {parsed['subject']}" if not parsed['subject'].startswith('Re:') else parsed['subject']
        reply['From']        = 'me'
        reply['In-Reply-To'] = parsed['message_id']
        reply['References']  = parsed['message_id']

        encoded = base64.urlsafe_b64encode(reply.as_bytes()).decode()
        result  = service.users().messages().send(
            userId="me",
            body={'raw': encoded, 'threadId': thread_id}
        ).execute()
        return f"Balasan berhasil dikirim ke {parsed['from']} (ID: {result['id']})"
    except Exception as e:
        return f"Gagal membalas email: {str(e)}"


# ---------------------------------------------------------------------------
# 6. Tandai Sudah Dibaca
# ---------------------------------------------------------------------------

def mark_as_read(message_id: str) -> str:
    """
    Menandai satu email sebagai sudah dibaca (hapus label UNREAD).
    """
    if not message_id:
        return "Gagal: 'message_id' wajib diisi."
    try:
        service = _get_service()
        service.users().messages().modify(
            userId='me',
            id=message_id,
            body={'removeLabelIds': ['UNREAD']}
        ).execute()
        return f"Email {message_id} berhasil ditandai sebagai sudah dibaca."
    except Exception as e:
        return f"Gagal menandai email: {str(e)}"


# ---------------------------------------------------------------------------
# 7. Tandai Semua Inbox sebagai Dibaca
# ---------------------------------------------------------------------------

def mark_all_inbox_read() -> str:
    """Menandai semua email UNREAD di Inbox sebagai sudah dibaca (batch)."""
    try:
        service = _get_service()
        results = service.users().messages().list(
            userId='me', labelIds=['INBOX', 'UNREAD'], maxResults=500
        ).execute()
        messages = results.get('messages', [])
        if not messages:
            return "Tidak ada email yang perlu ditandai (inbox sudah bersih)."

        ids = [m['id'] for m in messages]
        service.users().messages().batchModify(
            userId='me',
            body={'ids': ids, 'removeLabelIds': ['UNREAD']}
        ).execute()
        return f"Berhasil menandai {len(ids)} email sebagai sudah dibaca."
    except Exception as e:
        return f"Gagal menandai semua email: {str(e)}"
