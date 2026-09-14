@echo off
cd /d "%~dp0backend"
if not exist .venv\Scripts\python.exe (
  python -m venv .venv
)
call .venv\Scripts\activate.bat
pip install -r requirements.txt
if not exist .env copy .env.example .env >nul
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
