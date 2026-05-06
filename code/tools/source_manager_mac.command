#!/bin/bash
set -euo pipefail

pause() {
  echo
  read -r -p "Press Enter to close..."
}

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

HOST="${SOURCE_MANAGER_HOST:-127.0.0.1}"
PORT_START="${SOURCE_MANAGER_PORT:-8765}"

echo "ADAM SSM - Sleepless Source Manager"
echo "Working directory: $REPO_ROOT"
echo "Requested bind: $HOST:$PORT_START"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is not installed on this Mac."
  echo "Install it from https://www.python.org/downloads/ and try again."
  pause
  exit 1
fi

PYTHON_PATH="$(command -v python3)"
echo "Python path: $PYTHON_PATH"
python3 --version
echo

set +e
python3 code/tools/sources/launch_source_manager.py --host "$HOST" --port "$PORT_START"
exit_code=$?
set -e

echo
echo "Launcher exit code: $exit_code"
pause
exit "$exit_code"
