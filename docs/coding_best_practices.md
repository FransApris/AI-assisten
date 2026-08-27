# Panduan Koding & Praktik Terbaik (Coding Best Practices)

Dokumen ini berisi panduan, prinsip dasar, dan rekomendasi topik koding yang sering disarankan untuk pengembangan perangkat lunak modern.

## 1. Prinsip Penulisan Kode yang Bersih (Clean Code)
Menulis kode bukan hanya agar bisa dipahami oleh mesin, tetapi juga agar mudah dibaca dan dikelola oleh manusia (programmer lain atau diri sendiri di masa depan).
- **DRY (Don't Repeat Yourself)**: Hindari menduplikasi kode. Jika logika yang sama digunakan berulang kali, jadikan sebuah fungsi atau modul tersendiri.
- **KISS (Keep It Simple, Stupid)**: Jangan membuat kode menjadi rumit jika ada cara yang sederhana. Kesederhanaan membuat kode lebih minim bug.
- **YAGNI (You Aren't Gonna Need It)**: Jangan menambahkan fungsionalitas sebelum benar-benar dibutuhkan.
- **Penamaan yang Deskriptif**: Gunakan nama variabel dan fungsi yang jelas mendeskripsikan tujuannya (contoh: gunakan `calculateTotalHarga()` dibandingkan sekadar `calc()`).

## 2. Prinsip Desain Perangkat Lunak (SOLID)
Bagi pengembang berorientasi objek (OOP), prinsip SOLID sangat penting:
1. **S**ingle Responsibility Principle: Sebuah kelas (class) hanya boleh memiliki satu alasan untuk berubah (satu tanggung jawab).
2. **O**pen/Closed Principle: Kelas harus terbuka untuk ekstensi (penambahan fitur), tetapi tertutup untuk modifikasi.
3. **L**iskov Substitution Principle: Kelas turunan (child) harus bisa menggantikan kelas induk (parent) tanpa memutus sistem.
4. **I**nterface Segregation Principle: Klien tidak boleh dipaksa bergantung pada antarmuka (interface) yang tidak mereka gunakan.
5. **D**ependency Inversion Principle: Modul tingkat tinggi tidak boleh bergantung pada modul tingkat rendah, keduanya harus bergantung pada abstraksi.

## 3. Topik Koding yang Direkomendasikan untuk Dipelajari
Berdasarkan tren industri perangkat lunak modern, berikut adalah topik dan teknologi (stack) yang sangat disarankan untuk dikuasai:
- **Pengembangan Web (Web Development)**:
  - *Frontend*: React.js, Vue.js, Next.js, Tailwind CSS, TypeScript.
  - *Backend*: Node.js (Express, NestJS), Python (FastAPI, Django), Go (Golang), REST API, GraphQL.
- **Basis Data (Database)**:
  - *Relasional (SQL)*: PostgreSQL, MySQL.
  - *NoSQL*: MongoDB, Redis.
  - *Konsep*: Normalisasi, Indexing, Transaksi (ACID).
- **Infrastruktur & DevOps (Wajib Dikuasai)**:
  - **Git**: Sistem kontrol versi standar industri (GitHub, GitLab).
  - **Docker & Containerization**: Membungkus aplikasi agar bisa berjalan di lingkungan apa saja tanpa konflik.
  - **CI/CD (Continuous Integration/Continuous Deployment)**: Otomatisasi pengujian dan peluncuran (deployment) kode menggunakan GitHub Actions atau Jenkins.
- **Kecerdasan Buatan (AI) & Machine Learning**:
  - Python adalah bahasa wajib. Menguasai API LLM (seperti OpenAI, Gemini), LangChain, dan dasar-dasar Retrieval-Augmented Generation (RAG).

## 4. Tips Debugging dan Penyelesaian Masalah (Problem Solving)
1. **Pahami Masalah Sepenuhnya**: Jangan langsung mengetik kode. Pahami error message atau perilaku tidak normalnya.
2. **Pisahkan dan Taklukkan (Divide and Conquer)**: Jika menghadapi bug yang besar, isolasi kode Anda bagian per bagian hingga menemukan titik masalah aslinya.
3. **Membaca Log (Logging)**: Biasakan menambahkan log yang bermakna, bukan sekadar "test 1" atau "error".
4. **Karet Bebek (Rubber Duck Debugging)**: Jelaskan baris kode Anda secara verbal kepada sebuah objek (misal bebek karet) atau teman. Seringkali solusinya muncul dengan sendirinya saat Anda sedang menjelaskan logikanya.
