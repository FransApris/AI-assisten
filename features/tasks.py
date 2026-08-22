"""
features/tasks.py — Google Tasks Integration
=============================================
Mengelola task/to-do list via Google Tasks API.

Tag sistem prompt:
    <ADD_TASK title="Judul" due="YYYY-MM-DD" notes="Catatan opsional"/>
    <LIST_TASKS/>
    <COMPLETE_TASK title="Judul"/>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _get_service():
    """Buat Google Tasks API service menggunakan credentials yang sudah ada."""
    import google_drive
    from googleapiclient.discovery import build
    creds = google_drive.get_credentials()
    return build("tasks", "v1", credentials=creds)


def _get_default_tasklist_id(service) -> str:
    """Ambil ID tasklist default (@default)."""
    try:
        lists = service.tasklists().list(maxResults=1).execute()
        items = lists.get("items", [])
        return items[0]["id"] if items else "@default"
    except Exception:
        return "@default"


def add_task(title: str, due: str = "", notes: str = "") -> str:
    """
    Tambah task baru ke Google Tasks.
    due format: 'YYYY-MM-DD'
    """
    try:
        service = _get_service()
        tl_id   = _get_default_tasklist_id(service)

        body = {"title": title, "status": "needsAction"}
        if notes:
            body["notes"] = notes
        if due:
            # Google Tasks butuh format RFC 3339
            body["due"] = f"{due}T00:00:00.000Z"

        task = service.tasks().insert(tasklist=tl_id, body=body).execute()
        return f"✅ Task ditambahkan: *{task.get('title', title)}*"
    except Exception as e:
        return f"[Gagal menambahkan task: {e}]"


def list_tasks(max_results: int = 10) -> str:
    """Tampilkan daftar task yang belum selesai."""
    try:
        service = _get_service()
        tl_id   = _get_default_tasklist_id(service)

        result = service.tasks().list(
            tasklist=tl_id,
            showCompleted=False,
            maxResults=max_results,
            showHidden=False
        ).execute()

        tasks = result.get("items", [])
        if not tasks:
            return "Tidak ada task yang tersisa. 🎉"

        lines = ["📋 *Daftar Task:*"]
        for t in tasks:
            due  = t.get("due", "")[:10] if t.get("due") else ""
            due_str = f" _(due: {due})_" if due else ""
            lines.append(f"- {t.get('title', '?')}{due_str}")
        return "\n".join(lines)
    except Exception as e:
        return f"[Gagal mengambil task: {e}]"


def complete_task(title_keyword: str) -> str:
    """Tandai task sebagai selesai berdasarkan keyword judul."""
    try:
        service = _get_service()
        tl_id   = _get_default_tasklist_id(service)

        result = service.tasks().list(
            tasklist=tl_id, showCompleted=False, maxResults=50
        ).execute()
        tasks = result.get("items", [])

        kw = title_keyword.lower()
        matched = [t for t in tasks if kw in t.get("title", "").lower()]
        if not matched:
            return f"Tidak ditemukan task yang mengandung kata '{title_keyword}'."

        task = matched[0]
        task["status"] = "completed"
        service.tasks().update(
            tasklist=tl_id, task=task["id"], body=task
        ).execute()
        return f"✅ Task selesai: *{task.get('title')}*"
    except Exception as e:
        return f"[Gagal menyelesaikan task: {e}]"
