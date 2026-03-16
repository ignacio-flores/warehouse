#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is not installed on this Linux system."
  echo "Install it using your package manager and try again."
  echo
  read -r -p "Press Enter to close..."
  exit 1
fi

echo "Starting ADAM SSM - Sleepless Source Manager..."
echo "URL: http://127.0.0.1:8765"
echo

# Open browser after short delay so server has time to start.
(
  sleep 1
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "http://127.0.0.1:8765" >/dev/null 2>&1 || true
  elif command -v gio >/dev/null 2>&1; then
    gio open "http://127.0.0.1:8765" >/dev/null 2>&1 || true
  fi
) &

python3 code/tools/sources/ui_local.py

echo
read -r -p "ADAM SSM - Sleepless Source Manager stopped. Press Enter to close..."
