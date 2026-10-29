@echo off
title AI-Orchestrator (Remote & Tailscale Mode)
cd /d "%~dp0agent-manager"
if not exist ".venv\Scripts\python.exe" (
  echo [Kurulum] Ilk calistirma tespit edildi. Bagimliliklar yukleniyor...
  call setup.cmd
)
echo [Agent Manager] Mobil ve Uzaktan Erisim Modunda Baslatiliyor (0.0.0.0:8765)...
".venv\Scripts\python.exe" run.py --remote --open %*
if errorlevel 1 pause
