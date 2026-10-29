@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Once setup.cmd dosyasini calistirin.
  pause
  exit /b 1
)
echo [Agent Manager] Mobil ve Uzaktan Erisim Modunda Baslatiliyor (Tailscale / 0.0.0.0)...
".venv\Scripts\python.exe" run.py --remote --open %*
if errorlevel 1 pause
