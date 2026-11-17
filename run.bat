@echo off
rem Starts Snap Ninja and opens it in the browser. Creates the virtual environment on first run.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo First run: installing dependencies...
  python -m venv .venv || goto :fail
  .venv\Scripts\python -m pip install --upgrade pip
  .venv\Scripts\python -m pip install -r requirements.txt || goto :fail
  echo Optional NVIDIA acceleration: .venv\Scripts\python -m pip install -r requirements-gpu.txt
)
start "" http://127.0.0.1:7860
.venv\Scripts\python app.py
goto :eof
:fail
echo Setup failed. Check that Python 3.10+ and ffmpeg are installed and on PATH.
pause
