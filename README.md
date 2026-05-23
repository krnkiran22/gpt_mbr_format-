# AUTO SD CARD GPT → MBR Tool (macOS)

GUI tool for Mac Mini ingest stations. Scans all connected USB SD hubs, detects ~29 GB external SD cards, and converts each from **GPT → MBR**.

Designed for **9 hubs × 16 ports** setups — insert cards, click **AUTO DETECT & CONVERT**, and all detected cards are processed in one pass.

## Requirements

- macOS (Mac Mini Server 1 / Server 2)
- Python 3.10+
- Admin password (disk erase requires privileges)

## Setup on Mac Mini

```bash
git clone https://github.com/YOUR_USER/sd-card-gpt-mbr-tool.git
cd sd-card-gpt-mbr-tool

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Optional: add `background.jpg` (900×650) in the project folder for the UI background.

## Run

```bash
source .venv/bin/activate
python main.py
```

If erase fails with permission errors, run from Terminal with an admin session or grant Full Disk Access to Terminal in **System Settings → Privacy & Security**.

## What it does

1. Runs `diskutil list` (macOS equivalent of Windows `diskpart list disk`)
2. Finds **external physical** disks around **26–32 GB** (~29 GB SD cards)
3. For each disk: `diskutil eraseDisk MBRFormat SDCARD diskN`
4. Logs success/failure in the green terminal-style UI

## Notes

- **Do not remove cards** while processing.
- Internal disks (`disk0`, `disk1`) are skipped.
- Original Windows version used `diskpart`; this repo is the **macOS** port for ingest Mac Minis.
