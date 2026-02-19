#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is not installed on this Mac."
  echo "Install it from https://www.python.org/downloads/ and try again."
  echo
  read -r -p "Press Enter to close..."
  exit 1
fi

echo "Starting Source Registry UI..."
echo "URL: http://127.0.0.1:8765"
echo

# Open browser after short delay so server has time to start.
( sleep 1; open "http://127.0.0.1:8765" >/dev/null 2>&1 || true ) &

python3 code/tools/sources/ui_local.py

echo
read -r -p "Source Registry UI stopped. Press Enter to close..."
