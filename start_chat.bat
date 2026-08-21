@echo off
title APRIS Web Chat Server
color 0B

echo.
echo  ============================================
echo   APRIS - Asisten Pribadi Cerdas
echo   Web Chat Server
echo  ============================================
echo.

cd /d "%~dp0"

:: Cek Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python tidak ditemukan! Install Python 3.10+ terlebih dahulu.
    pause
    exit /b
)

:: Install dependencies jika belum
echo [*] Memeriksa dependencies...
pip install -q flask flask-cors google-genai python-dotenv
echo [OK] Dependencies siap.
echo.

:: Jalankan server
echo [*] Memulai APRIS Web Chat Server...
echo [*] Buka browser ke: http://localhost:5052
echo.
echo  Tekan Ctrl+C untuk menghentikan server.
echo  ============================================
echo.

start "" "http://localhost:5052"
python chat_server.py

pause
