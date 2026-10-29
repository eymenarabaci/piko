@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m pip install -r requirements.lock.txt
if errorlevel 1 exit /b 1
where uv >nul 2>nul
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m pip install uv
  if errorlevel 1 exit /b 1
  set "UV_EXEC=.venv\Scripts\uv.exe"
) else (
  set "UV_EXEC=uv"
)
"%UV_EXEC%" sync --frozen --no-dev --project "..\vendor\cao-upstream"
if errorlevel 1 exit /b 1
echo Kurulum tamamlandi. desktop.cmd ile baslatin.
