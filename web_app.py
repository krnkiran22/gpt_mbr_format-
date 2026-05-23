#!/usr/bin/env python3
"""
Browser-based SD card GPT -> MBR tool for Mac Mini.
System Tk 8.5 on macOS 26 renders a blank window — Safari UI always works.
"""
import json
import re
import socket
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

PORT_VISUAL = {
    "empty": {"bg": "#f4f4f5", "border": "#e4e4e7", "text": "#a1a1aa", "glow": ""},
    "pending": {"bg": "#eab308", "border": "#facc15", "text": "#422006", "glow": ""},
    "processing": {"bg": "#eab308", "border": "#facc15", "text": "#422006", "glow": ""},
    "ready": {
        "bg": "#15803d",
        "border": "#4ade80",
        "text": "rgba(255,255,255,0.95)",
        "glow": "0 0 0 1px rgba(34,197,94,0.25), 0 0 8px rgba(34,197,94,0.22)",
    },
    "failed": {
        "bg": "#dc2626",
        "border": "#f87171",
        "text": "rgba(255,255,255,0.95)",
        "glow": "0 0 0 1px rgba(220,38,38,0.25), 0 0 8px rgba(220,38,38,0.22)",
    },
}

STATION_BY_MINI = {
    1: 1, 2: 1, 3: 1,
    4: 2, 5: 2, 6: 2,
    7: 3, 8: 3, 9: 3,
    10: 4, 11: 4,
}


def host_info() -> dict:
    hostname = socket.gethostname().lower()
    mini_num = 0
    station_id = 0
    m = re.search(r"ingest-mini-(\d+)", hostname)
    if m:
        mini_num = int(m.group(1))
        station_id = STATION_BY_MINI.get(mini_num, 0)
    return {
        "hostname": hostname,
        "mini_num": mini_num,
        "station_id": station_id,
        "hub_base": (mini_num - 1) * 3 if mini_num else 0,
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
        host = host_info()
        for hub in range(1, HUB_COUNT + 1):
            for port in range(1, PORTS_PER_HUB + 1):
                info = self.slots[_slot_key(hub, port)]
                counts[info.status.value] += 1
                visual = PORT_VISUAL[info.status.value]
                slots_out.append(
                    {
                        "hub": hub,
                        "port": port,
                        "hub_label": host["hub_base"] + hub if host["hub_base"] else hub,
                        "status": info.status.value,
                        "bg": visual["bg"],
                        "border": visual["border"],
                        "text": visual["text"],
                        "glow": visual["glow"],
                        "detail": info.detail[:12],
                        "pulse": info.status == SlotStatus.PROCESSING,
                    }
                )
        with self._log_lock:
            logs = list(self.logs[-80:])
        inserted = counts["pending"] + counts["processing"] + counts["ready"] + counts["failed"]
        return {
            "host": host,
            "counts": counts,
            "slots": slots_out,
            "logs": logs,
            "inserted": inserted,
            "summary": (
                f"Auto-detect ON  ·  {inserted} inserted  ·  "
                f"{counts['ready']} MBR ready  ·  {counts['processing']} formatting  ·  "
                f"{counts['pending']} GPT  ·  {counts['failed']} failed"
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
HOST_INFO = host_info()

HTML_PAGE = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Build.ai SD Format</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{ height: 100%; overflow: hidden; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
    background: #ffffff; color: #09090b;
    display: flex; flex-direction: column;
  }}
  .top {{ padding: 20px 24px 12px; text-align: center; flex-shrink: 0; }}
  .brand {{ font-size: 18px; letter-spacing: -0.01em; margin-bottom: 6px; }}
  .brand-strong {{ font-weight: 700; letter-spacing: 0.2em; }}
  .brand-muted {{ font-weight: 400; color: #a1a1aa; }}
  .stats {{
    display: flex; flex-wrap: wrap; align-items: center; justify-content: center;
    gap: 6px 8px; font-size: 11px; color: #71717a;
  }}
  .dot {{
    width: 8px; height: 8px; border-radius: 50%; background: #16a34a;
    box-shadow: 0 0 6px rgba(22,163,106,0.4); display: inline-block;
  }}
  .stat-val {{ color: #09090b; font-weight: 500; }}
  .badges {{
    display: flex; flex-wrap: wrap; justify-content: center; gap: 8px; margin-top: 10px;
  }}
  .badge {{
    display: inline-flex; align-items: center; gap: 6px; min-height: 26px;
    padding: 0 10px; border-radius: 6px; border: 1px solid #e4e4e7; background: #fafafa;
  }}
  .badge-label {{
    font-size: 10px; font-weight: 700; letter-spacing: 0.08em;
    text-transform: uppercase; color: #71717a;
  }}
  .badge-val {{ font-size: 13px; font-weight: 700; font-variant-numeric: tabular-nums; }}
  .station-bar {{
    border-bottom: 1px solid #e4e4e7; padding: 6px 24px; text-align: center;
    font-size: 11px; letter-spacing: 0.08em; font-weight: 600; color: #71717a;
    flex-shrink: 0;
  }}
  .main {{
    flex: 1; display: flex; flex-direction: column; align-items: center;
    justify-content: center; overflow: auto; padding: 16px 24px 8px;
  }}
  .mini-wrap {{
    display: flex; flex-direction: column; align-items: center; gap: 10px;
  }}
  .mini-meta {{
    display: flex; align-items: center; gap: 6px; font-size: 11px; font-weight: 700;
    color: #333; letter-spacing: 0.5px;
  }}
  .mini-dot {{
    width: 7px; height: 7px; border-radius: 50%; background: #16a34a;
    box-shadow: 0 0 6px rgba(22,163,106,0.4);
  }}
  .hubs-row {{ display: flex; gap: 3px; align-items: flex-start; }}
  .hub-strip {{ display: inline-flex; flex-direction: column; align-items: center; gap: 8px; }}
  .hub-body {{
    display: flex; flex-direction: column; background: #fafafa; border-radius: 8px;
    padding: 8px 6px; position: relative;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08), 0 1px 2px rgba(0,0,0,0.04);
    border: 1px solid #e4e4e7;
  }}
  .hub-body::after {{
    content: ""; position: absolute; bottom: -6px; left: 50%; transform: translateX(-50%);
    width: 22px; height: 6px; background: #fafafa; border-radius: 0 0 3px 3px;
    border: 1px solid #e4e4e7; border-top: none;
  }}
  .port-col {{ display: flex; flex-direction: column; gap: 3px; }}
  .port {{
    width: 36px; height: 24px; border-radius: 4px; border: 1.75px solid #e4e4e7;
    background: #f4f4f5; display: flex; align-items: center; justify-content: center;
    position: relative; overflow: hidden; transition: all 0.2s ease;
  }}
  .port-num {{
    font-size: 10px; font-weight: 700; line-height: 1; user-select: none; z-index: 2;
    color: #a1a1aa;
  }}
  .port.pulse::before {{
    content: ""; position: absolute; inset: 0; background: rgba(255,255,255,0.25);
    animation: pulse 1.2s ease-in-out infinite; z-index: 1;
  }}
  @keyframes pulse {{ 0%,100% {{ opacity: 0.2; }} 50% {{ opacity: 0.7; }} }}
  .hub-label {{
    font-size: 14px; font-weight: 700; color: #71717a; text-align: center; user-select: none;
  }}
  .legend {{
    margin-top: 14px; font-size: 10px; color: #a1a1aa; letter-spacing: 0.06em;
    text-transform: uppercase; text-align: center;
  }}
  .legend span {{ margin: 0 8px; }}
  .swatch {{
    display: inline-block; width: 10px; height: 10px; border-radius: 2px;
    vertical-align: middle; margin-right: 4px; border: 1px solid rgba(0,0,0,0.08);
  }}
  .log-wrap {{
    flex-shrink: 0; border-top: 1px solid #e4e4e7; background: #fafafa; padding: 10px 16px 14px;
  }}
  .log-title {{
    font-size: 10px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;
    color: #71717a; margin-bottom: 6px;
  }}
  .log {{
    height: 120px; overflow-y: auto; background: #ffffff; border: 1px solid #e4e4e7;
    border-radius: 6px; padding: 8px 10px; font-family: ui-monospace, Menlo, monospace;
    font-size: 11px; line-height: 1.45; color: #52525b; white-space: pre-wrap;
  }}
</style>
</head><body>
  <div class="top">
    <div class="brand">
      <span class="brand-strong">BUILD AI</span>
      <span class="brand-muted">SD CARD FORMAT</span>
    </div>
    <div class="stats" id="stats-row">
      <span class="dot"></span>
      <span>Auto-detect active</span>
      <span>·</span>
      <span id="host-label">{HOST_INFO['hostname'] or 'ingest-mini'}</span>
    </div>
    <div class="badges" id="badges"></div>
  </div>

  <div class="station-bar" id="station-bar">STATION -- · GPT → MBR</div>

  <div class="main">
    <div class="mini-wrap">
      <div class="mini-meta">
        <span class="mini-dot"></span>
        <span id="mini-label">MINI --</span>
      </div>
      <div class="hubs-row" id="hubs"></div>
      <div class="legend">
        <span><i class="swatch" style="background:#f4f4f5"></i>Empty</span>
        <span><i class="swatch" style="background:#eab308"></i>GPT / Formatting</span>
        <span><i class="swatch" style="background:#15803d"></i>MBR Ready</span>
        <span><i class="swatch" style="background:#dc2626"></i>Failed</span>
      </div>
    </div>
  </div>

  <div class="log-wrap">
    <div class="log-title">Activity Log</div>
    <div class="log" id="log"></div>
  </div>

<script>
const HUBS = 3, PORTS = 16;

function buildGrid() {{
  const root = document.getElementById('hubs');
  root.innerHTML = '';
  for (let h = 1; h <= HUBS; h++) {{
    const strip = document.createElement('div');
    strip.className = 'hub-strip';
    strip.id = 'hub-strip-' + h;

    const body = document.createElement('div');
    body.className = 'hub-body';
    const col = document.createElement('div');
    col.className = 'port-col';
    col.id = 'hub-' + h;

    for (let p = PORTS; p >= 1; p--) {{
      const port = document.createElement('div');
      port.className = 'port';
      port.id = 'slot-' + h + '-' + p;
      port.innerHTML = '<span class="port-num">' + p + '</span>';
      col.appendChild(port);
    }}

    body.appendChild(col);
    strip.appendChild(body);

    const label = document.createElement('div');
    label.className = 'hub-label';
    label.id = 'hub-label-' + h;
    label.textContent = String(h);
    strip.appendChild(label);

    root.appendChild(strip);
  }}
}}

function renderBadges(data) {{
  const c = data.counts;
  const items = [
    ['Inserted', c.pending + c.processing + c.ready + c.failed, '#09090b', '#fafafa', '#e4e4e7'],
    ['GPT', c.pending, '#92400e', '#fff7ed', '#fdba74'],
    ['Formatting', c.processing, '#92400e', '#fff7ed', '#fdba74'],
    ['MBR Ready', c.ready, '#166534', '#f0fdf4', '#bbf7d0'],
    ['Failed', c.failed, '#991b1b', '#fee2e2', '#fecaca'],
  ];
  document.getElementById('badges').innerHTML = items.map(([label, val, color, bg, border]) =>
    '<div class="badge" style="background:' + bg + ';border-color:' + border + '">' +
    '<span class="badge-label">' + label + '</span>' +
    '<span class="badge-val" style="color:' + color + '">' + val + '</span></div>'
  ).join('');
}}

async function refresh() {{
  try {{
    const r = await fetch('/api/status');
    const data = await r.json();

    if (data.host) {{
      const station = data.host.station_id ? 'STATION ' + data.host.station_id : 'STATION --';
      document.getElementById('station-bar').textContent = station + ' · GPT → MBR AUTO FORMAT';
      document.getElementById('mini-label').textContent =
        data.host.mini_num ? ('MINI ' + data.host.mini_num) : data.host.hostname.toUpperCase();
    }}

    renderBadges(data);

    for (const s of data.slots) {{
      const el = document.getElementById('slot-' + s.hub + '-' + s.port);
      if (!el) continue;
      el.style.background = s.bg;
      el.style.borderColor = s.border;
      el.style.boxShadow = s.glow || 'none';
      el.classList.toggle('pulse', !!s.pulse);
      const num = el.querySelector('.port-num');
      if (num) num.style.color = s.text;
      const hubLabel = document.getElementById('hub-label-' + s.hub);
      if (hubLabel && s.hub_label) hubLabel.textContent = String(s.hub_label);
    }}

    document.getElementById('log').textContent = data.logs.join('\\n');
    const logEl = document.getElementById('log');
    logEl.scrollTop = logEl.scrollHeight;
  }} catch (e) {{
    document.getElementById('log').textContent = 'Connection error — is ./run.sh still running?';
  }}
}}

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
