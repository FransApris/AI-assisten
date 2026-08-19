# 📁 Folder Dokumen PDF — APRIS Knowledge Base

Letakkan file PDF kamu di folder ini. Sistem RAG akan membaca dan mempelajari isinya.

## Format yang Didukung
- ✅ PDF (`.pdf`) — teks bisa diekstrak
- ✅ PDF multi-halaman
- ✅ Bahasa Indonesia, Inggris, atau campuran
- ❌ PDF berupa scan/gambar murni (perlu OCR tambahan)

## Cara Menambah Dokumen

1. **Copy/paste** file PDF ke folder ini
2. Buka terminal di folder `rag-knowledge\`
3. Jalankan:
   ```bash
   python ingest.py
   ```
4. Tunggu hingga proses selesai

## Tips
- File akan otomatis dilewati jika sudah pernah diproses (tidak berubah)
- Untuk proses ulang semua: `python ingest.py --reset`
- Untuk proses file tertentu: `python ingest.py --file namafile.pdf`

## Contoh Dokumen yang Cocok
- Buku panduan / manual
- Dokumen kebijakan perusahaan
- Laporan penelitian
- Modul pelatihan
- Peraturan / regulasi
- Catatan kuliah / slide
