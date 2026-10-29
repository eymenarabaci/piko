@echo off
setlocal
cd /d "%~dp0"
if not exist "vendor\cao-upstream\pyproject.toml" (
  echo [HATA] Gomulu CAO kaynagi bulunamadi.
  exit /b 1
)
@REM Install bundled MCP packages from the pinned lockfile.
where npm >nul 2>nul
if errorlevel 1 (
  echo [HATA] MCP paketleri icin Node.js ve npm gerekli.
  exit /b 1
)
pushd "tools\mcp-packages"
call npm ci
if errorlevel 1 (
  popd
  echo [HATA] MCP paket kurulumu basarisiz.
  exit /b 1
)
popd
call "agent-manager\setup.cmd"
if errorlevel 1 exit /b 1
echo Kurulum tamamlandi. desktop.cmd ile baslatin.
exit /b 0
