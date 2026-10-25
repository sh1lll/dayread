#!/bin/bash
# Linux / macOS terminal: ./start_dayread.sh   (add --lan to reach it from a phone on the same Wi-Fi)
cd "$(dirname "$0")"
PY="../.venv/bin/python"; [ -x "$PY" ] || PY="$(command -v python3 || command -v python)"
exec "$PY" dayread_app.py "$@"
