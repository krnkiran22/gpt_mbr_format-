#!/bin/bash
# Mac Mini: browser UI — auto-stops any old instance on port 8765
set -e
cd "$(dirname "$0")"

PYTHON=""
for candidate in python3 /usr/bin/python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    PYTHON="$candidate"
    break
  fi
done

if [ -z "$PYTHON" ]; then
  echo "python3 not found"
  exit 1
fi

# Stop previous instance (fixes "Address already in use")
if pgrep -f "python.*web_app.py" >/dev/null 2>&1; then
  echo "Stopping previous SD format tool..."
  pkill -f "python.*web_app.py" 2>/dev/null || true
  sleep 0.5
fi

PORT_PID=$(lsof -ti :8765 2>/dev/null || true)
if [ -n "$PORT_PID" ]; then
  kill -9 $PORT_PID 2>/dev/null || true
  sleep 0.3
fi

echo "Starting SD Card MBR Tool in Safari..."
exec "$PYTHON" web_app.py
