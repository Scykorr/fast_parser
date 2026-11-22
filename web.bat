@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Missing .venv — create it with: py -3.11 -m venv .venv
  pause
  exit /b 1
)

set "PY=.venv\Scripts\python.exe"
set "PORT=8765"

rem Read port from settings.yaml when possible.
for /f "delims=" %%p in ('.venv\Scripts\python.exe -c "from fast_parser.config import load_settings; print(load_settings().web.port)"') do set "PORT=%%p"

rem Stop a leftover server on the same port (common after closing the wrong window).
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort %PORT% -State Listen -EA SilentlyContinue | ForEach-Object { Write-Host ('Stopping PID ' + $_.OwningProcess + ' on port ' + %PORT%); Stop-Process -Id $_.OwningProcess -Force -EA SilentlyContinue }"

echo Web UI: http://127.0.0.1:%PORT%
echo Close this window or press Ctrl+C to stop and free the port.
echo.

"%PY%" -m fast_parser web %*
set "EXITCODE=%ERRORLEVEL%"

if %EXITCODE% neq 0 (
  echo.
  echo Web failed with exit code %EXITCODE%.
  pause
)
exit /b %EXITCODE%
