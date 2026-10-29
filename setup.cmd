@echo off
title AI-Orchestrator Kurulum Sihirbazi
cd /d "%~dp0agent-manager"
echo ============================================================
echo   AI-Orchestrator: Kurulum ve Sanal Ortam Hazirlaniyor
echo ============================================================

where python >nul 2>nul
if errorlevel 1 (
  echo [HATA] Python sisteminizde bulunamadi. Python 3.11 veya 3.12 kurup tekrar deneyin.
  pause
  exit /b 1
)

if not exist ".venv" (
  echo Python sanal ortami (.venv) olusturuluyor...
  python -m venv .venv
)

echo Paketler yukleniyor (pip install)...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.lock.txt 2>nul || ".venv\Scripts\python.exe" -m pip install -e .

echo.
echo ============================================================
echo   Kurulum Basariyla Tamamlandi!
echo   Masaustu modu icin: desktop.cmd
echo   Uzaktan mobil erisim icin: remote.cmd
echo ============================================================
pause
