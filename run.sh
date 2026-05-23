#!/bin/bash
# Launch SD card GPT→MBR tool (works with macOS system Tk)
set -e
cd "$(dirname "$0")"
export TK_SILENCE_DEPRECATION=1
source .venv/bin/activate
exec python main.py
