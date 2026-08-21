import datetime
from googleapiclient.discovery import build
import google_drive

def get_calendar_service():
    """Mendapatkan instance service Google Calendar."""
    creds = google_drive.get_credentials()
    return build('calendar', 'v3', credentials=creds)

def create_event(summary: str, start_time_iso: str, end_time_iso: str, description: str = "") -> str:
    """
    Membuat event baru di Google Calendar.
    Format waktu: ISO 8601 (contoh: 2026-08-21T10:00:00+07:00)
    """
    service = get_calendar_service()
    
    event = {
        'summary': summary,
        'description': description,
        'start': {
            'dateTime': start_time_iso,
            'timeZone': 'Asia/Jakarta',
        },
        'end': {
            'dateTime': end_time_iso,
            'timeZone': 'Asia/Jakarta',
        },
    }
    
    try:
        event = service.events().insert(calendarId='primary', body=event).execute()
        return f"Event berhasil dibuat: {event.get('htmlLink')}"
    except Exception as e:
        return f"Gagal membuat event: {str(e)}"

def get_upcoming_events(max_results: int = 10) -> str:
    """
    Mengambil daftar event mendatang (hari ini dan seterusnya).
    """
    service = get_calendar_service()
    now = datetime.datetime.utcnow().isoformat() + 'Z'  # 'Z' indicates UTC time
    
    try:
        events_result = service.events().list(
            calendarId='primary', timeMin=now,
            maxResults=max_results, singleEvents=True,
            orderBy='startTime'
        ).execute()
        events = events_result.get('items', [])
        
        if not events:
            return "Tidak ada jadwal mendatang yang ditemukan."
            
        result = "Jadwal mendatang Anda:\n"
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            result += f"- {start}: {event['summary']}\n"
        return result
    except Exception as e:
        return f"Gagal mengambil jadwal: {str(e)}"

if __name__ == "__main__":
    # Test script jika dijalankan langsung
    print("Mengecek jadwal...")
    print(get_upcoming_events(3))
