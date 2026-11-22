@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m fast_parser process %*
) else (
  py -3.11 -m fast_parser process %*
)
