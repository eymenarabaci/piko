@echo off
title AI-Orchestrator (Agent Manager Desktop)
cd /d "%~dp0"
if not exist "agent-manager\.venv\Scripts\python.exe" (
  echo [Kurulum] Ilk calistirma tespit edildi.
  call setup.cmd
  if errorlevel 1 exit /b 1
)
cd /d "agent-manager"
".venv\Scripts\python.exe" run.py --desktop --open %*
if errorlevel 1 pause
