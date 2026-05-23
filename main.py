import os
import sys

# macOS system Tk (Command Line Tools) lacks some named colors like "lime".
os.environ.setdefault("TK_SILENCE_DEPRECATION", "1")

import customtkinter as ctk
import subprocess
import threading
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

from PIL import Image

# =========================
# CONFIG
# =========================

HUB_COUNT = 3
PORTS_PER_HUB = 16
POLL_INTERVAL_SEC = 1.5
SD_SIZE_MIN_GB = 26.0
SD_SIZE_MAX_GB = 32.5
SKIP_DISKS = {"disk0", "disk1"}

COLOR_EMPTY = "#B0BEC5"
COLOR_PENDING = "#FFC107"  # yellow — inserted, not MBR yet
COLOR_PROCESSING = "#FF9800"
COLOR_READY = "#4CAF50"  # green — MBR formatted
COLOR_FAILED = "#F44336"
APP_BG = "#F5F5F5"
HUB_BG = "#ECEFF1"
PANEL_BG = "#FFFFFF"

# =========================
# APP SETTINGS
# =========================

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")
ctk.set_widget_scaling(1.0)
ctk.set_window_scaling(1.0)

app = ctk.CTk()
app.geometry("1000x720")
app.minsize(900, 640)
app.title("AUTO SD CARD GPT -> MBR TOOL")
app.configure(fg_color=APP_BG)

# Solid root container — system Tk on Mac Mini breaks with transparent layers.
root_frame = ctk.CTkFrame(app, fg_color=APP_BG, corner_radius=0)
root_frame.pack(fill="both", expand=True)

# =========================
# BACKGROUND IMAGE (optional, kept behind content)
# =========================

_bg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "background.jpg")
if os.path.isfile(_bg_path):
    try:
        bg_image = ctk.CTkImage(
            light_image=Image.open(_bg_path),
            dark_image=Image.open(_bg_path),
            size=(1000, 720),
        )
        bg_label = ctk.CTkLabel(root_frame, image=bg_image, text="")
        bg_label.place(x=0, y=0, relwidth=1, relheight=1)
    except Exception:
        pass

# =========================
# UI
# =========================

title = ctk.CTkLabel(
    root_frame,
    text="AUTO SD CARD MANAGER",
    font=("Helvetica", 26, "bold"),
    fg_color="#1976D2",
    corner_radius=12,
    text_color="#FFFFFF",
    width=480,
    height=46,
)
title.pack(pady=(12, 6))

status_label = ctk.CTkLabel(
    root_frame,
    text="Watching for SD cards…",
    font=("Helvetica", 13),
    fg_color=APP_BG,
    text_color="#212121",
)
status_label.pack(pady=(0, 4))

legend = ctk.CTkLabel(
    root_frame,
    text="Gray = empty   Yellow = GPT inserted   Orange = formatting   Green = MBR ready   Red = failed",
    font=("Helvetica", 11),
    fg_color=APP_BG,
    text_color="#455A64",
)
legend.pack(pady=(0, 6))

hubs_frame = ctk.CTkFrame(root_frame, fg_color=PANEL_BG, corner_radius=10)
hubs_frame.pack(padx=12, pady=4, fill="both", expand=True)

slot_widgets: Dict[Tuple[int, int], ctk.CTkButton] = {}
slot_labels: Dict[Tuple[int, int], ctk.CTkLabel] = {}


def _slot_key(hub: int, port: int) -> Tuple[int, int]:
    return (hub, port)


for hub_idx in range(1, HUB_COUNT + 1):
    hub_col = ctk.CTkFrame(hubs_frame, corner_radius=8, fg_color=HUB_BG)
    hub_col.pack(side="left", expand=True, fill="both", padx=4, pady=6)

    ctk.CTkLabel(
        hub_col,
        text=f"HUB {hub_idx}",
        font=("Helvetica", 15, "bold"),
        fg_color=HUB_BG,
        text_color="#1976D2",
    ).pack(pady=(6, 4))

    ports_grid = ctk.CTkFrame(hub_col, fg_color=HUB_BG)
    ports_grid.pack(padx=6, pady=(0, 6))

    for port_idx in range(1, PORTS_PER_HUB + 1):
        row = (port_idx - 1) // 4
        col = (port_idx - 1) % 4
        key = _slot_key(hub_idx, port_idx)

        cell = ctk.CTkFrame(ports_grid, fg_color=HUB_BG)
        cell.grid(row=row, column=col, padx=2, pady=2)

        btn = ctk.CTkButton(
            cell,
            text=str(port_idx),
            width=48,
            height=32,
            font=("Helvetica", 11, "bold"),
            fg_color=COLOR_EMPTY,
            hover_color=COLOR_EMPTY,
            text_color="#37474F",
            corner_radius=6,
            state="disabled",
        )
        btn.pack()

        lbl = ctk.CTkLabel(
            cell,
            text="—",
            font=("Helvetica", 8),
            fg_color=HUB_BG,
            text_color="#607D8B",
            width=48,
        )
        lbl.pack()

        slot_widgets[key] = btn
        slot_labels[key] = lbl

textbox = ctk.CTkTextbox(
    root_frame,
    width=960,
    height=140,
    font=("Menlo", 11),
    fg_color="#000000",
    text_color="#00FF00",
    corner_radius=8,
)
textbox.pack(pady=(6, 10), padx=12)
textbox.insert("end", "Application started.\n")
textbox.insert("end", f"Platform: macOS ({sys.platform})\n")
textbox.insert(
    "end",
    (
        f"Layout: {HUB_COUNT} hubs x {PORTS_PER_HUB} ports\n"
        f"Command: diskutil eraseDisk FAT32 SDCARD MBRFormat diskN\n"
    ),
)

root_frame.lift()

# =========================
# DISK / MBR LOGIC
# =========================


class SlotStatus(str, Enum):
    EMPTY = "empty"
    PENDING = "pending"  # inserted, GPT / not MBR
    PROCESSING = "processing"
    READY = "ready"  # verified MBR
    FAILED = "failed"


@dataclass
class SlotInfo:
    status: SlotStatus = SlotStatus.EMPTY
    disk_id: Optional[str] = None
    detail: str = "—"


@dataclass
class DiskInfo:
    disk_id: str
    size_gb: float
    scheme: str  # GUID_partition_scheme, FDisk_partition_scheme, etc.


@dataclass
class AppState:
    slots: Dict[Tuple[int, int], SlotInfo] = field(
        default_factory=lambda: {
            _slot_key(h, p): SlotInfo() for h in range(1, HUB_COUNT + 1) for p in range(1, PORTS_PER_HUB + 1)
        }
    )
    disk_to_slot: Dict[str, Tuple[int, int]] = field(default_factory=dict)
    processing: Set[str] = field(default_factory=set)
    lock: threading.Lock = field(default_factory=threading.Lock)
    poll_running: bool = True


state = AppState()
_log_lock = threading.Lock()


def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")

    def _append() -> None:
        with _log_lock:
            textbox.insert("end", f"[{ts}] {msg}\n")
            textbox.see("end")

    app.after(0, _append)


def _size_gb(line: str) -> Optional[float]:
    m = re.search(r"\*\s*([\d.]+)\s*GB", line, re.IGNORECASE)
    if m:
        return float(m.group(1))
    m = re.search(r"\*\s*([\d.]+)\s*MB", line, re.IGNORECASE)
    if m:
        return float(m.group(1)) / 1024.0
    return None


def get_disks_output() -> str:
    result = subprocess.run(
        ["diskutil", "list"],
        capture_output=True,
        text=True,
    )
    return result.stdout


def parse_sd_disks(output: str) -> List[DiskInfo]:
    """Find external ~29 GB SD cards and read partition scheme."""
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
                found.append(
                    DiskInfo(
                        disk_id=current_disk,
                        size_gb=size_gb,
                        scheme=scheme or "unknown",
                    )
                )

    return found


def get_partition_scheme(disk_id: str) -> str:
    """Return partition scheme string from diskutil, e.g. GUID_partition_scheme."""
    listing = subprocess.run(
        ["diskutil", "list", disk_id],
        capture_output=True,
        text=True,
    ).stdout

    for line in listing.splitlines():
        stripped = line.strip()
        if re.match(r"^\d+:", stripped):
            parts = stripped.split()
            if len(parts) >= 2:
                return parts[1]

    result = subprocess.run(
        ["diskutil", "info", disk_id],
        capture_output=True,
        text=True,
    )
    for line in result.stdout.splitlines():
        if "Content (IOContent):" in line:
            return line.split(":", 1)[1].strip()

    return "unknown"


def is_mbr_formatted(disk_id: str) -> bool:
    """True when disk uses Master Boot Record (FDisk) and is not GPT."""
    scheme = get_partition_scheme(disk_id).lower()
    if "guid_partition_scheme" in scheme:
        return False
    if "fdisk_partition_scheme" in scheme:
        return True
    if "master boot record" in scheme:
        return True

    text = subprocess.run(
        ["diskutil", "info", disk_id],
        capture_output=True,
        text=True,
    ).stdout.lower()
    if "guid_partition_scheme" in text:
        return False
    return "fdisk_partition_scheme" in text or "master boot record" in text


def convert_to_mbr(disk_id: str) -> Tuple[bool, str]:
    """
    Erase disk with MBR partition map + FAT32 volume.

    Correct macOS syntax:
      diskutil eraseDisk FAT32 SDCARD MBRFormat diskN
    (MBRFormat is the partition scheme, not the filesystem format.)
    """
    before = get_partition_scheme(disk_id)
    log_lines = [f"{disk_id}: before = {before}"]

    result = subprocess.run(
        ["diskutil", "eraseDisk", "FAT32", "SDCARD", "MBRFormat", disk_id],
        capture_output=True,
        text=True,
    )
    combined = (result.stdout + "\n" + result.stderr).strip()
    ok = result.returncode == 0 and "finished erase on" in combined.lower()

    if not ok:
        log_lines.append(combined)
        return False, "\n".join(log_lines)

    # Re-read after a short pause so diskutil state is stable.
    time.sleep(0.5)
    after = get_partition_scheme(disk_id)
    log_lines.append(f"{disk_id}: after = {after}")
    log_lines.append(combined)

    if is_mbr_formatted(disk_id):
        return True, "\n".join(log_lines)

    return False, "\n".join(log_lines) + "\nVerify failed: still not MBR."


def _next_free_slot() -> Optional[Tuple[int, int]]:
    for hub in range(1, HUB_COUNT + 1):
        for port in range(1, PORTS_PER_HUB + 1):
            key = _slot_key(hub, port)
            if state.slots[key].status == SlotStatus.EMPTY:
                return key
    return None


def _set_slot(key: Tuple[int, int], status: SlotStatus, disk_id: Optional[str], detail: str) -> None:
    state.slots[key] = SlotInfo(status=status, disk_id=disk_id, detail=detail)
    if disk_id:
        state.disk_to_slot[disk_id] = key
    app.after(0, lambda: refresh_slot_ui(key))


def _clear_slot(key: Tuple[int, int]) -> None:
    info = state.slots[key]
    if info.disk_id and info.disk_id in state.disk_to_slot:
        del state.disk_to_slot[info.disk_id]
    state.slots[key] = SlotInfo()
    app.after(0, lambda: refresh_slot_ui(key))


def refresh_slot_ui(key: Tuple[int, int]) -> None:
    info = state.slots[key]
    btn = slot_widgets[key]
    lbl = slot_labels[key]

    color_map = {
        SlotStatus.EMPTY: COLOR_EMPTY,
        SlotStatus.PENDING: COLOR_PENDING,
        SlotStatus.PROCESSING: COLOR_PROCESSING,
        SlotStatus.READY: COLOR_READY,
        SlotStatus.FAILED: COLOR_FAILED,
    }
    text_color = "#FFFFFF" if info.status != SlotStatus.EMPTY else "#37474F"

    btn.configure(
        fg_color=color_map[info.status],
        hover_color=color_map[info.status],
        text_color=text_color,
    )
    lbl.configure(text=info.detail[:12])


def refresh_all_ui() -> None:
    for key in state.slots:
        refresh_slot_ui(key)


def update_status_bar() -> None:
    counts = {s: 0 for s in SlotStatus}
    for info in state.slots.values():
        counts[info.status] += 1

    status_label.configure(
        text=(
            f"Auto-detect ON  |  "
            f"Pending: {counts[SlotStatus.PENDING]}  "
            f"Processing: {counts[SlotStatus.PROCESSING]}  "
            f"Ready (MBR): {counts[SlotStatus.READY]}  "
            f"Failed: {counts[SlotStatus.FAILED]}  "
            f"Empty: {counts[SlotStatus.EMPTY]}"
        )
    )


def process_disk(disk_id: str) -> None:
    with state.lock:
        if disk_id in state.processing:
            return
        state.processing.add(disk_id)

    key = state.disk_to_slot.get(disk_id)
    if not key:
        state.processing.discard(disk_id)
        return

    try:
        if is_mbr_formatted(disk_id):
            _set_slot(key, SlotStatus.READY, disk_id, disk_id)
            log(f"{disk_id}: already MBR — green")
            return

        _set_slot(key, SlotStatus.PROCESSING, disk_id, "format…")
        log(f"{disk_id}: converting GPT → MBR…")
        app.after(0, update_status_bar)

        ok, detail = convert_to_mbr(disk_id)
        if ok:
            _set_slot(key, SlotStatus.READY, disk_id, "MBR OK")
            log(f"{disk_id}: MBR format SUCCESS — green\n{detail}")
        else:
            _set_slot(key, SlotStatus.FAILED, disk_id, "failed")
            log(f"{disk_id}: FAILED\n{detail}")
    finally:
        state.processing.discard(disk_id)
        app.after(0, update_status_bar)


def poll_and_process() -> None:
    while state.poll_running:
        try:
            output = get_disks_output()
            disks = parse_sd_disks(output)
            present_ids = {d.disk_id for d in disks}

            with state.lock:
                # Remove disks that were unplugged
                removed = [did for did in list(state.disk_to_slot) if did not in present_ids]
                for disk_id in removed:
                    key = state.disk_to_slot.get(disk_id)
                    if key:
                        log(f"{disk_id}: removed — slot cleared")
                        _clear_slot(key)

                # Assign new disks
                for disk in disks:
                    if disk.disk_id in state.disk_to_slot:
                        key = state.disk_to_slot[disk.disk_id]
                        info = state.slots[key]
                        if info.status == SlotStatus.READY and is_mbr_formatted(disk.disk_id):
                            continue
                        if info.status == SlotStatus.EMPTY:
                            _set_slot(key, SlotStatus.PENDING, disk.disk_id, disk.disk_id)
                        continue

                    key = _next_free_slot()
                    if key is None:
                        log(f"{disk.disk_id}: no free slot (all {HUB_COUNT * PORTS_PER_HUB} ports full)")
                        continue

                    if is_mbr_formatted(disk.disk_id):
                        _set_slot(key, SlotStatus.READY, disk.disk_id, disk.disk_id)
                        log(f"{disk.disk_id}: inserted — already MBR (green)")
                    else:
                        _set_slot(key, SlotStatus.PENDING, disk.disk_id, disk.disk_id)
                        log(f"{disk.disk_id}: inserted — GPT detected (yellow)")

                # Queue conversion for pending disks
                to_process = []
                for disk_id, key in list(state.disk_to_slot.items()):
                    if disk_id not in present_ids:
                        continue
                    info = state.slots[key]
                    if info.status == SlotStatus.PENDING and disk_id not in state.processing:
                        to_process.append(disk_id)

            app.after(0, update_status_bar)

            for disk_id in to_process:
                threading.Thread(target=process_disk, args=(disk_id,), daemon=True).start()

        except Exception as exc:
            log(f"Poll error: {exc}")

        time.sleep(POLL_INTERVAL_SEC)


def on_close() -> None:
    state.poll_running = False
    app.destroy()


def _force_layout() -> None:
    """System Tk on Mac Mini sometimes needs a relayout pass to paint widgets."""
    app.update_idletasks()
    w, h = app.winfo_width(), app.winfo_height()
    if w > 1 and h > 1:
        app.geometry(f"{w}x{h}")
    root_frame.lift()


app.protocol("WM_DELETE_WINDOW", on_close)

# Start background poller
threading.Thread(target=poll_and_process, daemon=True).start()
refresh_all_ui()
update_status_bar()
app.update_idletasks()
app.after(200, _force_layout)

app.mainloop()
