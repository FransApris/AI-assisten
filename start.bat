@echo off
chcp 65001 > nul
title APRIS RAG Server

echo.
echo =====================================================
echo   APRIS RAG - Starting Server...
echo =====================================================
echo.

cd /d "%~dp0"

:: Cek apakah Python tersedia
python --version > nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python tidak ditemukan! Install Python 3.11+ terlebih dahulu.
    pause
    exit /b 1
)

:: Cek apakah requirements sudah terinstall
python -c "import flask, chromadb" > nul 2>&1
if errorlevel 1 (
    echo [INFO] Menginstall dependencies...
    pip install -r requirements.txt -q
    echo [OK] Dependencies terinstall.
    echo.
)

:: Jalankan server di background
echo [INFO] Menjalankan server di http://localhost:5050
echo [INFO] Admin Dashboard: http://localhost:5050/admin
echo.
echo Jangan tutup jendela ini selama server berjalan!
echo Tekan Ctrl+C untuk menghentikan server.
echo.

:: Tunggu 2 detik lalu buka browser
start "" /min cmd /c "timeout /t 2 /nobreak > nul & start http://localhost:5050/admin"

:: Jalankan server (foreground agar bisa Ctrl+C)
python rag_server.py

echo.
echo [INFO] Server dihentikan.
pause
