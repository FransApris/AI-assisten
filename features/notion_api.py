import os
from notion_client import Client

# Notion API membatasi satu rich_text block maksimal 2000 karakter
_NOTION_MAX_BLOCK = 1900


def _split_content(content: str) -> list[str]:
    """Pecah konten panjang menjadi potongan <= _NOTION_MAX_BLOCK karakter."""
    if len(content) <= _NOTION_MAX_BLOCK:
        return [content]
    chunks = []
    while content:
        chunks.append(content[:_NOTION_MAX_BLOCK])
        content = content[_NOTION_MAX_BLOCK:]
    return chunks


def write_to_notion(title: str, content: str) -> str:
    """Menulis halaman baru ke Notion workspace."""
    token   = os.getenv("NOTION_API_KEY")
    page_id = os.getenv("NOTION_PAGE_ID")  # ID parent page atau database

    if not token or not page_id:
        return (
            "Gagal menulis ke Notion. Anda belum mengatur `NOTION_API_KEY` dan `NOTION_PAGE_ID`. "
            "Silakan buat integrasi di https://www.notion.so/my-integrations dan tambahkan ke file .env."
        )

    try:
        notion = Client(auth=token)

        # Pecah konten menjadi beberapa paragraph block (maks 2000 char/block)
        content_chunks  = _split_content(content)
        children_blocks = []
        for chunk in content_chunks:
            children_blocks.append({
                "object": "block",
                "type"  : "paragraph",
                "paragraph": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {"content": chunk}
                        }
                    ]
                }
            })

        new_page = {
            "parent"    : {"page_id": page_id},
            "properties": {
                "title": [{"text": {"content": title}}]
            },
            "children": children_blocks
        }

        res = notion.pages.create(**new_page)
        url = res.get("url", "URL tidak tersedia")
        return f"Berhasil membuat catatan di Notion: {url}"
    except Exception as e:
        return f"[Gagal menulis ke Notion: {e}]"
