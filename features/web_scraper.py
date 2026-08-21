import requests
from bs4 import BeautifulSoup
import re

def extract_urls(text: str) -> list:
    """Ekstrak semua URL dari teks."""
    url_pattern = re.compile(r'(https?://[^\s]+)')
    return url_pattern.findall(text)

def scrape_url_text(url: str, max_chars: int = 15000) -> str:
    """
    Download HTML dari URL dan ekstrak teksnya.
    Batasi panjang hasil untuk menghindari token berlebih.
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Hapus tag script, style, header, footer, nav
        for element in soup(['script', 'style', 'nav', 'header', 'footer', 'aside']):
            element.decompose()
            
        text = soup.get_text(separator='\n', strip=True)
        
        # Hapus baris kosong berlebih
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        cleaned_text = '\n'.join(lines)
        
        if len(cleaned_text) > max_chars:
            cleaned_text = cleaned_text[:max_chars] + "\n...[TEKS TERPOTONG KARENA TERLALU PANJANG]"
            
        return cleaned_text
    except Exception as e:
        return f"[Gagal membaca URL {url}: {str(e)}]"
