@echo off
rem Developer launcher: runs the app from the project virtual environment.
rem (The Phase 13 installer replaces this with a proper shortcut.)
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Virtual environment not found. Run:  py -3.11 -m venv .venv ^&^& .venv\Scripts\pip install -r requirements-dev.txt
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m ledsync
