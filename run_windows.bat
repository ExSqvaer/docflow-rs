@echo off
setlocal
cd /d %~dp0
if not exist .venv (py -m venv .venv 2>nul || python -m venv .venv)
call .venv\Scripts\activate.bat
python -m pip install -r requirements.txt
if not defined SESSION_SECRET set SESSION_SECRET=docflow-rs-local-demo-change-me
start "" http://127.0.0.1:8000/login
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
