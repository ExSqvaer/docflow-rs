#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
: "${SESSION_SECRET:=docflow-rs-local-demo-change-me}"
export SESSION_SECRET
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
