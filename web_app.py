#!/usr/bin/env python3
"""
Browser-based SD card GPT -> MBR tool for Mac Mini.
System Tk 8.5 on macOS 26 renders a blank window — Safari UI always works.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from enum import Enum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional, Set, Tuple

HUB_COUNT = 3
PORTS_PER_HUB = 16
POLL_INTERVAL_SEC = 1.5
SD_SIZE_MIN_GB = 26.0
SD_SIZE_MAX_GB = 32.5
SKIP_DISKS = {"disk0", "disk1"}
HOST = "127.0.0.1"
PORT = 8765

COLOR = {
    "empty": "#B0BEC5",
    "pending": "#FFC107",
    "processing": "#FF9800",
    "ready": "#4CAF50",
    "failed": "#F44336",
}


def _slot_key(hub: int, port: int) -> Tuple[int, int]:
    return (hub, port)


class SlotStatus(str, Enum):
    EMPTY = "empty"
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


@dataclass
class SlotInfo:
    status: SlotStatus = SlotStatus.EMPTY
    disk_id: Optional[str] = None
    detail: str = "-"


@dataclass
class DiskInfo:
    disk_id: str
    size_gb: float
    scheme: str


def _size_gb(line: str) -> Optional[float]:
    m = re.search(r"\*\s*([\d.]+)\s*GB", line, re.IGNORECASE)
    if m:
        return float(m.group(1))
    m = re.search(r"\*\s*([\d.]+)\s*MB", line, re.IGNORECASE)
    if m:
        return float(m.group(1)) / 1024.0
    return None


def get_disks_output() -> str:
    return subprocess.run(["diskutil", "list"], capture_output=True, text=True).stdout


def parse_sd_disks(output: str) -> List[DiskInfo]:
    found: List[DiskInfo] = []
    current_disk: Optional[str] = None
    is_external = False
    scheme = ""

    for line in output.splitlines():
        header = re.match(r"/dev/(disk\d+)\s+\(([^)]+)\):", line.strip())
        if header:
            current_disk = header.group(1)
            flags = header.group(2).lower()
            is_external = "external" in flags and "physical" in flags
            scheme = ""
            continue
        if not current_disk or not is_external or current_disk in SKIP_DISKS:
            continue
        stripped = line.strip()
        if re.match(r"^\d+:", stripped):
            parts = stripped.split()
            if len(parts) >= 2:
                scheme = parts[1]
            continue
        size_gb = _size_gb(line)
        if size_gb is None:
            continue
        if SD_SIZE_MIN_GB <= size_gb <= SD_SIZE_MAX_GB:
            if not any(d.disk_id == current_disk for d in found):
                found.append(DiskInfo(current_disk, size_gb, scheme or "unknown"))
    return found


def get_partition_scheme(disk_id: str) -> str:
    listing = subprocess.run(["diskutil", "list", disk_id], capture_output=True, text=True).stdout
    for line in listing.splitlines():
        stripped = line.strip()
        if re.match(r"^\d+:", stripped):
            parts = stripped.split()
            if len(parts) >= 2:
                return parts[1]
    info = subprocess.run(["diskutil", "info", disk_id], capture_output=True, text=True).stdout
    for line in info.splitlines():
        if "Content (IOContent):" in line:
            return line.split(":", 1)[1].strip()
    return "unknown"


def is_mbr_formatted(disk_id: str) -> bool:
    scheme = get_partition_scheme(disk_id).lower()
    if "guid_partition_scheme" in scheme:
        return False
    if "fdisk_partition_scheme" in scheme:
        return True
    text = subprocess.run(["diskutil", "info", disk_id], capture_output=True, text=True).stdout.lower()
    if "guid_partition_scheme" in text:
        return False
    return "fdisk_partition_scheme" in text or "master boot record" in text


def convert_to_mbr(disk_id: str) -> Tuple[bool, str]:
    before = get_partition_scheme(disk_id)
    lines = [f"{disk_id}: before = {before}"]
    result = subprocess.run(
        ["diskutil", "eraseDisk", "FAT32", "SDCARD", "MBRFormat", disk_id],
        capture_output=True,
        text=True,
    )
    combined = (result.stdout + "\n" + result.stderr).strip()
    ok = result.returncode == 0 and "finished erase on" in combined.lower()
    if not ok:
        lines.append(combined)
        return False, "\n".join(lines)
    time.sleep(0.5)
    after = get_partition_scheme(disk_id)
    lines.append(f"{disk_id}: after = {after}")
    lines.append(combined)
    if is_mbr_formatted(disk_id):
        return True, "\n".join(lines)
    return False, "\n".join(lines) + "\nVerify failed: still not MBR."


class Manager:
    def __init__(self) -> None:
        self.slots: Dict[Tuple[int, int], SlotInfo] = {
            _slot_key(h, p): SlotInfo() for h in range(1, HUB_COUNT + 1) for p in range(1, PORTS_PER_HUB + 1)
        }
        self.disk_to_slot: Dict[str, Tuple[int, int]] = {}
        self.processing: Set[str] = set()
        self.lock = threading.Lock()
        self.poll_running = True
        self.logs: List[str] = [
            "Application started (browser UI).",
            f"Platform: macOS ({sys.platform})",
            f"Layout: {HUB_COUNT} hubs x {PORTS_PER_HUB} ports",
            "Command: diskutil eraseDisk FAT32 SDCARD MBRFormat diskN",
        ]
        self._log_lock = threading.Lock()

    def log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        with self._log_lock:
            self.logs.append(f"[{ts}] {msg}")
            if len(self.logs) > 500:
                self.logs = self.logs[-400:]

    def status_json(self) -> dict:
        counts = {s.value: 0 for s in SlotStatus}
        slots_out = []
        for hub in range(1, HUB_COUNT + 1):
            for port in range(1, PORTS_PER_HUB + 1):
                info = self.slots[_slot_key(hub, port)]
                counts[info.status.value] += 1
                slots_out.append(
                    {
                        "hub": hub,
                        "port": port,
                        "status": info.status.value,
                        "color": COLOR[info.status.value],
                        "detail": info.detail[:12],
                    }
                )
        with self._log_lock:
            logs = list(self.logs[-80:])
        return {
            "counts": counts,
            "slots": slots_out,
            "logs": logs,
            "summary": (
                f"Auto-detect ON | Pending: {counts['pending']} | "
                f"Processing: {counts['processing']} | Ready: {counts['ready']} | "
                f"Failed: {counts['failed']} | Empty: {counts['empty']}"
            ),
        }

    def _next_free_slot(self) -> Optional[Tuple[int, int]]:
        for hub in range(1, HUB_COUNT + 1):
            for port in range(1, PORTS_PER_HUB + 1):
                key = _slot_key(hub, port)
                if self.slots[key].status == SlotStatus.EMPTY:
                    return key
        return None

    def _set_slot(self, key: Tuple[int, int], status: SlotStatus, disk_id: Optional[str], detail: str) -> None:
        self.slots[key] = SlotInfo(status=status, disk_id=disk_id, detail=detail)
        if disk_id:
            self.disk_to_slot[disk_id] = key

    def _clear_slot(self, key: Tuple[int, int]) -> None:
        info = self.slots[key]
        if info.disk_id and info.disk_id in self.disk_to_slot:
            del self.disk_to_slot[info.disk_id]
        self.slots[key] = SlotInfo()

    def process_disk(self, disk_id: str) -> None:
        with self.lock:
            if disk_id in self.processing:
                return
            self.processing.add(disk_id)
        key = self.disk_to_slot.get(disk_id)
        if not key:
            self.processing.discard(disk_id)
            return
        try:
            if is_mbr_formatted(disk_id):
                self._set_slot(key, SlotStatus.READY, disk_id, "MBR OK")
                self.log(f"{disk_id}: already MBR - green")
                return
            self._set_slot(key, SlotStatus.PROCESSING, disk_id, "format...")
            self.log(f"{disk_id}: converting GPT -> MBR...")
            ok, detail = convert_to_mbr(disk_id)
            if ok:
                self._set_slot(key, SlotStatus.READY, disk_id, "MBR OK")
                self.log(f"{disk_id}: MBR SUCCESS - green\n{detail}")
            else:
                self._set_slot(key, SlotStatus.FAILED, disk_id, "failed")
                self.log(f"{disk_id}: FAILED\n{detail}")
        finally:
            self.processing.discard(disk_id)

    def poll_loop(self) -> None:
        while self.poll_running:
            try:
                disks = parse_sd_disks(get_disks_output())
                present_ids = {d.disk_id for d in disks}
                to_process: List[str] = []

                with self.lock:
                    for disk_id in [d for d in list(self.disk_to_slot) if d not in present_ids]:
                        key = self.disk_to_slot.get(disk_id)
                        if key:
                            self.log(f"{disk_id}: removed - slot cleared")
                            self._clear_slot(key)

                    for disk in disks:
                        if disk.disk_id in self.disk_to_slot:
                            key = self.disk_to_slot[disk.disk_id]
                            info = self.slots[key]
                            if info.status == SlotStatus.READY and is_mbr_formatted(disk.disk_id):
                                continue
                            if info.status == SlotStatus.EMPTY:
                                self._set_slot(key, SlotStatus.PENDING, disk.disk_id, disk.disk_id)
                            continue
                        key = self._next_free_slot()
                        if key is None:
                            self.log(f"{disk.disk_id}: no free slot")
                            continue
                        if is_mbr_formatted(disk.disk_id):
                            self._set_slot(key, SlotStatus.READY, disk.disk_id, "MBR OK")
                            self.log(f"{disk.disk_id}: inserted - already MBR")
                        else:
                            self._set_slot(key, SlotStatus.PENDING, disk.disk_id, disk.disk_id)
                            self.log(f"{disk.disk_id}: inserted - GPT (yellow)")

                    for disk_id, key in list(self.disk_to_slot.items()):
                        if disk_id not in present_ids:
                            continue
                        info = self.slots[key]
                        if info.status == SlotStatus.PENDING and disk_id not in self.processing:
                            to_process.append(disk_id)

                for disk_id in to_process:
                    threading.Thread(target=self.process_disk, args=(disk_id,), daemon=True).start()
            except Exception as exc:
                self.log(f"Poll error: {exc}")
            time.sleep(POLL_INTERVAL_SEC)


MANAGER = Manager()

HTML_PAGE = """<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>AUTO SD CARD GPT -> MBR TOOL</title>
<style>
  * { box-sizing: border-box; }
  body { font-family: -apple-system, Helvetica, Arial, sans-serif; margin: 0; background: #f5f5f5; color: #212121; }
  .header { background: #1976D2; color: #fff; text-align: center; padding: 14px; font-size: 24px; font-weight: bold; }
  .status { padding: 10px 16px; font-size: 14px; }
  .legend { padding: 0 16px 10px; font-size: 12px; color: #455A64; }
  .hubs { display: flex; gap: 8px; padding: 0 12px 12px; }
  .hub { flex: 1; background: #eceff1; border: 1px solid #cfd8dc; border-radius: 8px; padding: 8px; }
  .hub h3 { margin: 4px 0 8px; color: #1976D2; text-align: center; }
  .grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 4px; }
  .slot { text-align: center; }
  .port {
    border-radius: 6px; padding: 8px 0; font-weight: bold; font-size: 13px;
    border: 2px solid rgba(0,0,0,0.1); min-height: 36px;
  }
  .detail { font-size: 9px; color: #607D8B; margin-top: 2px; min-height: 14px; }
  .log {
    margin: 0 12px 12px; background: #1b1b1b; color: #0f0;
    font-family: Menlo, monospace; font-size: 11px;
    padding: 10px; border-radius: 8px; height: 160px; overflow-y: auto; white-space: pre-wrap;
  }
</style>
</head><body>
<div class="header">AUTO SD CARD MANAGER</div>
<div class="status" id="status">Loading...</div>
<div class="legend">Gray=empty &nbsp; Yellow=GPT &nbsp; Orange=formatting &nbsp; Green=MBR ready &nbsp; Red=failed</div>
<div class="hubs" id="hubs"></div>
<div class="log" id="log"></div>
<script>
const HUBS = 3, PORTS = 16;

function buildGrid() {
  const root = document.getElementById('hubs');
  root.innerHTML = '';
  for (let h = 1; h <= HUBS; h++) {
    const hub = document.createElement('div');
    hub.className = 'hub';
    hub.innerHTML = '<h3>HUB ' + h + '</h3><div class="grid" id="hub-' + h + '"></div>';
    root.appendChild(hub);
    const grid = hub.querySelector('.grid');
    for (let p = 1; p <= PORTS; p++) {
      const cell = document.createElement('div');
      cell.className = 'slot';
      cell.id = 'slot-' + h + '-' + p;
      cell.innerHTML = '<div class="port">' + p + '</div><div class="detail">-</div>';
      grid.appendChild(cell);
    }
  }
}

async function refresh() {
  try {
    const r = await fetch('/api/status');
    const data = await r.json();
    document.getElementById('status').textContent = data.summary;
    for (const s of data.slots) {
      const el = document.getElementById('slot-' + s.hub + '-' + s.port);
      if (!el) continue;
      const port = el.querySelector('.port');
      const detail = el.querySelector('.detail');
      port.style.background = s.color;
      port.style.color = (s.status === 'empty') ? '#37474F' : '#fff';
      detail.textContent = s.detail;
    }
    document.getElementById('log').textContent = data.logs.join('\\n');
    const logEl = document.getElementById('log');
    logEl.scrollTop = logEl.scrollHeight;
  } catch (e) {
    document.getElementById('status').textContent = 'Connection error - is the app running?';
  }
}

buildGrid();
refresh();
setInterval(refresh, 1500);
</script>
</body></html>
"""


def make_handler(manager: Manager):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                body = HTML_PAGE.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/api/status":
                body = json.dumps(manager.status_json()).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

    return Handler


def open_browser(url: str) -> None:
    try:
        subprocess.run(["open", url], check=False)
    except Exception:
        webbrowser.open(url)


def main() -> None:
    url = f"http://{HOST}:{PORT}/"
    threading.Thread(target=MANAGER.poll_loop, daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), make_handler(MANAGER))
    print(f"SD Card MBR Tool running at {url}")
    print("Press Ctrl+C to stop.")
    threading.Thread(target=lambda: (time.sleep(0.8), open_browser(url)), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        MANAGER.poll_running = False
        server.shutdown()


if __name__ == "__main__":
    main()
