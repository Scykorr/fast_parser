@echo off
rem Sentiment, once, from PowerShell in this folder. CPU build, no CUDA:
rem   .\.venv\Scripts\Activate.ps1
rem   python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
rem   python -m pip install -e ".[ml]" --upgrade-strategy only-if-needed
rem   python -c "import torch; print(torch.__version__); print('cuda', torch.cuda.is_available())"
rem   fast-parser process
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m fast_parser run
) else (
  py -3.11 -m fast_parser run
)
