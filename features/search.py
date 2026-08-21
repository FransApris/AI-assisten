"""
features/search.py — Web Search via DuckDuckGo + Gemini Summarizer
"""
import os
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
CHAT_MODEL     = os.getenv("GEMINI_CHAT_MODEL", "models/gemini-2.5-flash")
MAX_RESULTS    = int(os.getenv("SEARCH_MAX_RESULTS", 5))


def _client():
    from google import genai
    return genai.Client(api_key=GEMINI_API_KEY)


def _ddg_text(query: str, n: int) -> list:
    """Search via DuckDuckGo HTML API menggunakan requests."""
    import urllib.parse, requests as req
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
        }
        params = {"q": query, "kl": "id-id", "kp": "-1"}
        r = req.get("https://html.duckduckgo.com/html/",
                    params=params, headers=headers, timeout=10)
        from html.parser import HTMLParser

        class DDGParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.results = []
                self._cur = {}
                self._in_title = self._in_snippet = False

            def handle_starttag(self, tag, attrs):
                a = dict(attrs)
                if tag == "a" and "result__a" in a.get("class", ""):
                    self._cur["href"] = a.get("href", "")
                    self._in_title = True
                elif tag == "a" and "result__snippet" in a.get("class", ""):
                    self._in_snippet = True

            def handle_endtag(self, tag):
                if tag == "a" and self._in_title:
                    self._in_title = False
                    self._in_snippet = False
                    if self._cur.get("title") and self._cur.get("href"):
                        self.results.append(dict(self._cur))
                        self._cur = {}

            def handle_data(self, data):
                if self._in_title and not self._cur.get("title"):
                    self._cur["title"] = data.strip()
                elif self._in_snippet:
                    self._cur["body"] = self._cur.get("body", "") + data

        parser = DDGParser()
        parser.feed(r.text)
        return parser.results[:n] if parser.results else _ddg_text_fallback(query, n)
    except Exception:
        return _ddg_text_fallback(query, n)


def _ddg_text_fallback(query: str, n: int) -> list:
    """Fallback: gunakan DDG API sederhana."""
    import urllib.parse, json, requests as req
    try:
        r = req.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": "1", "no_redirect": "1"},
            timeout=10
        )
        d = r.json()
        results = []
        if d.get("AbstractText"):
            results.append({"title": d.get("Heading",""), "href": d.get("AbstractURL",""), "body": d["AbstractText"]})
        for t in d.get("RelatedTopics", [])[:n]:
            if isinstance(t, dict) and t.get("Text"):
                results.append({"title": t.get("Text","")[:60], "href": t.get("FirstURL",""), "body": t.get("Text","")})
        return results[:n]
    except Exception:
        return []


def _ddg_news(query: str, n: int) -> list:
    """Cari berita via DuckDuckGo News."""
    import requests as req
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        params  = {"q": query, "kl": "id-id", "df": "w"}   # df=w: seminggu terakhir
        r = req.get("https://html.duckduckgo.com/html/",
                    params=params, headers=headers, timeout=10)
        # Parse sama seperti text search
        results = _ddg_text_fallback(query + " berita terbaru", n)
        return results
    except Exception:
        return []


def _summarize(query: str, results: list) -> str:
    if not results:
        return "Tidak ditemukan hasil yang relevan."
    raw = "\n\n".join([
        f"[{i+1}] {r.get('title','')}\nURL: {r.get('href', r.get('url','-'))}\n{r.get('body', r.get('excerpt',''))}"
        for i, r in enumerate(results)
    ])
    prompt = f"""Saya mencari: "{query}"

Hasil pencarian:
{raw}

Tugas: Rangkum informasi paling relevan. Jawab langsung, ringkas (maks 300 kata), Bahasa Indonesia.
Sebutkan sumber jika relevan. Jika hasil tidak relevan, katakan dengan jujur."""
    return _client().models.generate_content(model=CHAT_MODEL, contents=prompt).text.strip()


def search(query: str, max_results: int = None, summarize: bool = True) -> dict:
    """Cari informasi dari internet."""
    n   = max_results or MAX_RESULTS
    raw = _ddg_text(query.strip(), n)
    return {
        "query"       : query,
        "summary"     : _summarize(query, raw) if summarize else None,
        "raw_results" : [{"title":r.get("title",""), "url":r.get("href",""), "snippet":r.get("body","")} for r in raw],
        "result_count": len(raw),
        "timestamp"   : datetime.now().isoformat(),
        "source"      : "DuckDuckGo",
    }


def search_news(query: str, max_results: int = None, summarize: bool = True) -> dict:
    """Cari berita terbaru."""
    n   = max_results or MAX_RESULTS
    raw = _ddg_news(query.strip(), n)
    return {
        "query"       : query,
        "summary"     : _summarize(f"berita tentang {query}", raw) if summarize else None,
        "raw_results" : [{"title":r.get("title",""), "url":r.get("url",""), "snippet":r.get("excerpt",""), "date":r.get("date",""), "source":r.get("source","")} for r in raw],
        "result_count": len(raw),
        "timestamp"   : datetime.now().isoformat(),
        "source"      : "DuckDuckGo News",
    }
