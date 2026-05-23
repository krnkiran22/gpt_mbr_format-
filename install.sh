#!/bin/bash
# Install / update gpt_mbr_format- on a Mac Mini ingest station
set -e
REPO_DIR="$HOME/gpt_mbr_format-"
REPO_URL="https://github.com/krnkiran22/gpt_mbr_format-.git"

if [ -d "$REPO_DIR/.git" ]; then
  cd "$REPO_DIR" && git pull origin main
else
  git clone "$REPO_URL" "$REPO_DIR"
  cd "$REPO_DIR"
fi

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt
echo "Installed at $REPO_DIR"
echo "Run: cd $REPO_DIR && source .venv/bin/activate && python main.py"
