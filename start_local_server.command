#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

URL="http://127.0.0.1:8000"

if [[ ! -x ".venv/bin/uvicorn" ]]; then
  echo "Missing .venv/bin/uvicorn. Create/install the Python virtualenv first."
  echo "Example: python3 -m venv .venv && .venv/bin/python -m pip install -e ."
  exit 1
fi

echo "Starting local World Cup prediction dashboard..."
echo "Open: $URL"
echo "Press Ctrl+C in this window to stop the service."

(sleep 2 && open "$URL") &

exec .venv/bin/uvicorn worldcup_predictor.api:app \
  --app-dir backend \
  --host 127.0.0.1 \
  --port 8000
