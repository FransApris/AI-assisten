import os
from notion_client import Client

def write_to_notion(title: str, content: str) -> str:
    """Menulis halaman baru ke Notion workspace."""
    token = os.getenv("NOTION_API_KEY")
    page_id = os.getenv("NOTION_PAGE_ID")  # ID parent page atau database

    if not token or not page_id:
        return (
            "Gagal menulis ke Notion. Anda belum mengatur `NOTION_API_KEY` dan `NOTION_PAGE_ID`. "
            "Silakan buat integrasi di https://www.notion.so/my-integrations dan tambahkan ke file .env."
        )

    try:
        notion = Client(auth=token)
        
        new_page = {
            "parent": {"page_id": page_id},
            "properties": {
                "title": [
                    {
                        "text": {
                            "content": title
                        }
                    }
                ]
            },
            "children": [
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            {
                                "type": "text",
                                "text": {
                                    "content": content
                                }
                            }
                        ]
                    }
                }
            ]
        }
        
        res = notion.pages.create(**new_page)
        url = res.get("url", "URL tidak tersedia")
        return f"Berhasil membuat catatan di Notion: {url}"
    except Exception as e:
        return f"[Gagal menulis ke Notion: {e}]"
