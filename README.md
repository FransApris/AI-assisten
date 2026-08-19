# APRIS RAG Knowledge Base

Sistem RAG (Retrieval-Augmented Generation) untuk APRIS — memungkinkan AI menjawab pertanyaan berdasarkan dokumen PDF.

## Menjalankan Lokal

**Double-click `start.bat`** — otomatis jalankan server + buka browser.

Atau manual:
```bash
python rag_server.py
# Buka: http://localhost:5050/admin
```

---

## Deploy ke Railway (Online)

### Prasyarat
- Akun [Railway.app](https://railway.app) (gratis)
- Akun [GitHub](https://github.com) (untuk push kode)
- Git terinstall di PC

### Langkah 1 — Push ke GitHub

```bash
cd "d:\APRIS FILE\AI Assisten\rag-knowledge"

# Init git (pertama kali)
git init
git add .
git commit -m "Initial commit: APRIS RAG Server"

# Buat repo baru di github.com, lalu:
git remote add origin https://github.com/USERNAME/apris-rag.git
git push -u origin main
```

> ⚠️ File `.env` sudah ada di `.gitignore` — API key aman, tidak akan terupload ke GitHub.

### Langkah 2 — Buat Project di Railway

1. Buka [railway.app](https://railway.app) → Login
2. Klik **"New Project"**
3. Pilih **"Deploy from GitHub repo"**
4. Pilih repo `apris-rag` yang sudah dibuat
5. Railway otomatis mendeteksi Python & `Procfile`

### Langkah 3 — Tambah Railway Volume (Penyimpanan Persisten)

> ⚠️ **PENTING!** Tanpa Volume, data PDF dan ChromaDB akan hilang setiap kali deploy ulang.

1. Di Railway project → Klik service `apris-rag`
2. Tab **"Volumes"** → **"Add Volume"**
3. Isi:
   - **Mount Path**: `/data`
   - **Size**: 5 GB (cukup untuk ratusan PDF)
4. Klik **"Create Volume"**

### Langkah 4 — Set Environment Variables

Di Railway → Service → Tab **"Variables"** → tambahkan:

| Variable | Nilai |
|----------|-------|
| `GEMINI_API_KEY` | API key Gemini kamu |
| `GEMINI_CHAT_MODEL` | `gemini-1.5-flash` |
| `GEMINI_EMBEDDING_MODEL` | `models/text-embedding-004` |
| `CHROMA_DB_PATH` | `/data/vectorstore` |
| `DOCS_FOLDER` | `/data/docs` |
| `TOP_K_RESULTS` | `5` |

> **`PORT`** tidak perlu diisi — Railway set otomatis.

### Langkah 5 — Deploy!

Railway otomatis deploy setelah setting Variables.
Tunggu ~2 menit, lalu klik URL yang diberikan Railway:

```
https://apris-rag-production.up.railway.app/admin
```

---

## Struktur File

```
rag-knowledge/
├── docs/               <- PDF (lokal) / /data/docs (Railway)
├── vectorstore/        <- ChromaDB (lokal) / /data/vectorstore (Railway)
├── admin.html          <- Admin Dashboard UI
├── rag_server.py       <- Flask API Server
├── ingest.py           <- Proses PDF -> ChromaDB
├── query.py            <- CLI test query
├── requirements.txt    <- Dependencies Python
├── Procfile            <- Railway: cara jalankan server
├── railway.toml        <- Railway: konfigurasi build & deploy
├── runtime.txt         <- Python version (3.11)
├── start.bat           <- Windows: double-click untuk jalankan lokal
├── .env                <- Config lokal (JANGAN commit ke GitHub!)
├── .env.example        <- Template config
└── .gitignore          <- File yang dikecualikan dari Git
```

---

## Biaya Railway

| Plan | Harga | Kapasitas |
|------|-------|-----------|
| Hobby (Free Trial) | $0 / 30 hari | $5 credit |
| Hobby | $5/bulan | 8GB RAM, 100GB storage |
| Pro | $20/bulan | Resources lebih besar |

Untuk penggunaan ringan (1-5 pengguna), **Hobby $5/bulan sudah lebih dari cukup**.
