#!/bin/bash
# Launch SD card GPT->MBR tool (standard Tkinter — works on Mac Mini system Python)
set -e
cd "$(dirname "$0")"
export TK_SILENCE_DEPRECATION=1
source .venv/bin/activate
exec python main.py
