#!/bin/bash
# Mac Mini: use browser UI (system Tk 8.5 shows blank window on macOS 26)
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

echo "Starting SD Card MBR Tool in Safari..."
exec "$PYTHON" web_app.py
