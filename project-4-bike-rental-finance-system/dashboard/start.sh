#!/bin/sh
# Starts the backend: the API and the frontend on one port, http://127.0.0.1:8000.
#   ./dashboard/start.sh          the live Google Sheet (needs dashboard/backend/.env)
#   ./dashboard/start.sh --demo   the synthetic demo snapshot: no Google, no keys, no login
cd "$(dirname "$0")/backend"
if [ "$1" = "--demo" ]; then
  export DATA_SOURCE=demo
fi
PYTHON=python3
[ -x ../../.venv/bin/python ] && PYTHON=../../.venv/bin/python
exec "$PYTHON" -m uvicorn app.main:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}"
