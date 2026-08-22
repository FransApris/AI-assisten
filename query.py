"""
query.py — APRIS RAG Query Tool (CLI)
=======================================
Test tanya-jawab berbasis dokumen PDF sebelum integrasi ke WhatsApp.

Cara pakai:
    python query.py                    # mode interaktif
    python query.py --q "apa itu XYZ"  # langsung tanya dari command line
    python query.py --list             # tampilkan dokumen yang tersimpan
"""

import os
import sys
import argparse

import chromadb
from google import genai
from google.genai import types as gtypes
from dotenv import load_dotenv
from colorama import init, Fore, Style

# ─── Init ────────────────────────────────────────────────────────────────────
init(autoreset=True)
load_dotenv()

GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY")
CHAT_MODEL        = os.getenv("GEMINI_CHAT_MODEL", "gemini-2.5-flash")
EMBEDDING_MODEL   = os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
CHROMA_DB_PATH    = os.getenv("CHROMA_DB_PATH", "./vectorstore")
CHROMA_COLLECTION = os.getenv("CHROMA_COLLECTION_NAME", "apris_knowledge")
TOP_K             = int(os.getenv("TOP_K_RESULTS", 5))

# Lazy Gemini client (SDK baru)
_genai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


# ─── Helper ───────────────────────────────────────────────────────────────────

def print_header():
    print(f"\n{Fore.CYAN}{'═'*55}")
    print(f"{Fore.CYAN}  APRIS RAG — Query Tool")
    print(f"{Fore.CYAN}  Ketik 'keluar' atau 'exit' untuk berhenti")
    print(f"{Fore.CYAN}{'═'*55}{Style.RESET_ALL}\n")


def get_chroma_collection():
    """Sambungkan ke ChromaDB yang sudah ada."""
    if not os.path.exists(CHROMA_DB_PATH):
        print(f"{Fore.RED}  ✗ Database belum ada. Jalankan dulu: python ingest.py")
        sys.exit(1)
    client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
    try:
        collection = client.get_collection(CHROMA_COLLECTION)
        return collection
    except Exception:
        print(f"{Fore.RED}  ✗ Collection '{CHROMA_COLLECTION}' tidak ditemukan.")
        print(f"     Jalankan dulu: python ingest.py")
        sys.exit(1)


def get_query_embedding(text: str) -> list[float]:
    """Buat embedding untuk query."""
    result = _genai_client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=text,
        config=gtypes.EmbedContentConfig(task_type="RETRIEVAL_QUERY")
    )
    return result.embeddings[0].values


def search_documents(query: str, collection, top_k: int = TOP_K) -> list[dict]:
    """Cari chunks yang paling relevan dari ChromaDB."""
    query_emb = get_query_embedding(query)
    results   = collection.query(
        query_embeddings=[query_emb],
        n_results=min(top_k, collection.count()),
        include=["documents", "metadatas", "distances"]
    )

    chunks = []
    if results and results["documents"]:
        for i, (doc, meta, dist) in enumerate(zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0]
        )):
            chunks.append({
                "rank":       i + 1,
                "text":       doc,
                "source":     meta.get("source", "unknown"),
                "page_num":   meta.get("page_num", "?"),
                "language":   meta.get("language", "?"),
                "score":      round(1 - dist, 4)   # konversi distance → similarity
            })
    return chunks


def build_prompt(query: str, chunks: list[dict]) -> str:
    """Buat prompt untuk Gemini berdasarkan konteks dokumen."""
    context_parts = []
    for c in chunks:
        context_parts.append(
            f"[Sumber: {c['source']}, Hal. {c['page_num']}, Relevansi: {c['score']}]\n{c['text']}"
        )
    context = "\n\n---\n\n".join(context_parts)

    prompt = f"""Kamu adalah APRIS, asisten pribadi cerdas berbahasa Indonesia.
Jawablah pertanyaan pengguna HANYA berdasarkan konteks dokumen yang diberikan.
Jika jawaban tidak ada dalam dokumen, katakan dengan jujur bahwa informasi tersebut tidak tersedia dalam dokumen yang diupload.
Sebutkan sumber dokumen (nama file dan halaman) saat menjawab.
Gunakan bahasa yang sama dengan pertanyaan pengguna (Indonesia/Inggris/campuran).
Jangan mengarang informasi yang tidak ada dalam konteks.

═══════════════════════════════
KONTEKS DOKUMEN:
═══════════════════════════════
{context}

═══════════════════════════════
PERTANYAAN:
═══════════════════════════════
{query}

═══════════════════════════════
JAWABAN:
═══════════════════════════════"""
    return prompt


def ask(query: str, collection) -> str:
    """Pipeline lengkap: query → search → generate answer."""
    print(f"\n{Fore.YELLOW}  🔍 Mencari di database...", end="", flush=True)
    chunks = search_documents(query, collection)

    if not chunks:
        return "Tidak ada dokumen relevan ditemukan. Pastikan sudah upload & ingest PDF."

    print(f"\r{Fore.GREEN}  ✓ Ditemukan {len(chunks)} bagian relevan dari: "
          f"{', '.join(set(c['source'] for c in chunks))}")

    # Tampilkan sumber yang digunakan
    print(f"\n{Fore.CYAN}  📚 Sumber yang digunakan:")
    for c in chunks:
        print(f"     [{c['rank']}] {c['source']} — Hal. {c['page_num']} "
              f"(relevansi: {c['score']})")

    # Generate jawaban
    print(f"\n{Fore.YELLOW}  💬 Membuat jawaban...\n")
    prompt = build_prompt(query, chunks)
    resp = _genai_client.models.generate_content(
        model=CHAT_MODEL,
        contents=prompt
    )
    return resp.text


def list_documents(collection):
    """Tampilkan semua dokumen yang tersimpan di database."""
    total = collection.count()
    print(f"\n{Fore.CYAN}  📁 Database berisi {total} chunks total\n")

    if total == 0:
        print(f"{Fore.YELLOW}  Database kosong. Jalankan: python ingest.py")
        return

    results = collection.get(limit=1000, include=["metadatas"])
    sources = {}
    for meta in results["metadatas"]:
        src = meta.get("source", "unknown")
        if src not in sources:
            sources[src] = {"chunks": 0, "pages": set(), "lang": meta.get("language", "?")}
        sources[src]["chunks"] += 1
        sources[src]["pages"].add(meta.get("page_num", 0))

    print(f"  {'No':<4} {'Nama File':<40} {'Chunks':<8} {'Halaman':<8} {'Bahasa'}")
    print(f"  {'─'*4} {'─'*40} {'─'*8} {'─'*8} {'─'*6}")
    for i, (src, info) in enumerate(sorted(sources.items()), 1):
        print(f"  {i:<4} {src:<40} {info['chunks']:<8} {len(info['pages']):<8} {info['lang']}")
    print()


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="APRIS RAG — CLI Query Tool")
    parser.add_argument("--q",    type=str, help="Pertanyaan langsung")
    parser.add_argument("--list", action="store_true", help="Tampilkan daftar dokumen")
    parser.add_argument("--top",  type=int, default=TOP_K, help=f"Jumlah hasil (default: {TOP_K})")
    args = parser.parse_args()

    print_header()
    collection = get_chroma_collection()
    doc_count  = collection.count()
    print(f"  🗄  Database  : {os.path.abspath(CHROMA_DB_PATH)}")
    print(f"  📄 Total data: {doc_count} chunks\n")

    if doc_count == 0:
        print(f"{Fore.YELLOW}  ⚠ Database kosong. Letakkan PDF di folder 'docs/' lalu jalankan:")
        print(f"     python ingest.py\n")
        sys.exit(0)

    if args.list:
        list_documents(collection)
        sys.exit(0)

    if args.q:
        # Mode satu pertanyaan
        answer = ask(args.q, collection)
        print(f"{Fore.WHITE}{'─'*55}")
        print(f"{Fore.GREEN}  JAWABAN APRIS:\n")
        print(f"  {answer.replace(chr(10), chr(10)+'  ')}")
        print(f"{Fore.WHITE}{'─'*55}\n")
    else:
        # Mode interaktif
        print(f"  Tanya apa saja tentang dokumen PDF yang sudah diupload.")
        print(f"  Ketik 'list' untuk melihat dokumen tersedia.\n")
        while True:
            try:
                query = input(f"{Fore.CYAN}  Kamu: {Style.RESET_ALL}").strip()
                if not query:
                    continue
                if query.lower() in ["keluar", "exit", "quit", "q"]:
                    print(f"\n{Fore.CYAN}  Sampai jumpa!\n")
                    break
                if query.lower() == "list":
                    list_documents(collection)
                    continue

                answer = ask(query, collection)
                print(f"\n{Fore.GREEN}  APRIS: {Style.RESET_ALL}{answer}\n")
                print(f"  {'─'*51}\n")

            except KeyboardInterrupt:
                print(f"\n\n{Fore.CYAN}  Keluar...\n")
                break


if __name__ == "__main__":
    main()
