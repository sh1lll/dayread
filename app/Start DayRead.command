#!/bin/bash
# macOS: double-click to start Day Read in your browser.
cd "$(dirname "$0")"
PY="../.venv/bin/python"; [ -x "$PY" ] || PY="$(command -v python3)"
exec "$PY" dayread_app.py
