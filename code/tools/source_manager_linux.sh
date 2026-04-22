#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

HOST="${SOURCE_MANAGER_HOST:-127.0.0.1}"
PORT_START="${SOURCE_MANAGER_PORT:-8765}"
LOG_PATH="${SOURCE_MANAGER_LOG:-/tmp/source_manager_linux.log}"

echo "ADAM SSM launcher log: $LOG_PATH"
{
  echo
  echo "=== $(date -Iseconds) ==="
  echo "Launcher: $0"
  echo "Repo root: $REPO_ROOT"
  echo "Host: $HOST"
  echo "Requested port: $PORT_START"
} >>"$LOG_PATH" 2>/dev/null || true

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is not installed on this Linux system."
  echo "Install it using your package manager and try again."
  echo
  read -r -p "Press Enter to close..."
  exit 1
fi

is_source_manager_running() {
  python3 - "$HOST" "$1" <<'PY'
import sys
from urllib.request import urlopen

host = sys.argv[1]
port = sys.argv[2]
try:
    with urlopen(f"http://{host}:{port}/api/ping", timeout=0.5) as response:
        raise SystemExit(0 if response.status == 200 else 1)
except Exception:
    raise SystemExit(1)
PY
}

find_available_port() {
  python3 - "$HOST" "$PORT_START" <<'PY'
import socket
import sys

host = sys.argv[1]
start = int(sys.argv[2])
try:
    for port in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
            except OSError:
                continue
            print(port)
            raise SystemExit(0)
except PermissionError:
    print(start)
    raise SystemExit(0)
raise SystemExit(1)
PY
}

is_port_available() {
  python3 - "$HOST" "$1" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
try:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            raise SystemExit(1)
except PermissionError:
    raise SystemExit(0)
raise SystemExit(0)
PY
}

open_source_manager_url() {
  local url="$1"
  if command -v xdg-open >/dev/null 2>&1; then
    if xdg-open "$url" >>"$LOG_PATH" 2>&1; then
      return 0
    fi
  elif command -v gio >/dev/null 2>&1; then
    if gio open "$url" >>"$LOG_PATH" 2>&1; then
      return 0
    fi
  fi
  echo "Could not open the browser automatically. Open this URL manually:"
  echo "$url"
  return 1
}

if is_source_manager_running "$PORT_START"; then
  URL="http://$HOST:$PORT_START"
  echo "ADAM SSM - Sleepless Source Manager is already running."
  echo "URL: $URL"
  echo
  if ! open_source_manager_url "$URL"; then
    echo
    echo "Browser open details were written to: $LOG_PATH"
    read -r -p "Press Enter to close..."
  fi
  exit 0
fi

if ! PORT="$(find_available_port)"; then
  PORT="$PORT_START"
fi
URL="http://$HOST:$PORT"

echo "Starting ADAM SSM - Sleepless Source Manager..."
echo "URL: $URL"
if [ "$PORT" != "$PORT_START" ]; then
  echo "Port $PORT_START was unavailable, so the app is using port $PORT."
elif ! is_port_available "$PORT"; then
  echo "Port $PORT is already in use and does not appear to be ADAM SSM."
  echo "Close the process using that port, or run with SOURCE_MANAGER_PORT set to another port."
  echo
  read -r -p "Press Enter to close..."
  exit 1
fi
echo

# Open browser after short delay so server has time to start.
(
  sleep 1
  open_source_manager_url "$URL" || true
) &

python3 code/tools/sources/ui_local.py --host "$HOST" --port "$PORT" 2>&1 | tee -a "$LOG_PATH"

echo
read -r -p "ADAM SSM - Sleepless Source Manager stopped. Press Enter to close..."
