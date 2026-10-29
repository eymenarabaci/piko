@echo off
title AI-Orchestrator (Agent Manager Desktop)
cd /d "%~dp0agent-manager"
if not exist ".venv\Scripts\python.exe" (
  echo [Kurulum] Ilk calistirma tespit edildi. Bagimliliklar yukleniyor...
  call setup.cmd
)
".venv\Scripts\python.exe" run.py --desktop --open %*
if errorlevel 1 pause
