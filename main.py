#!/usr/bin/env python3
"""SD card GPT -> MBR tool for Mac Mini ingest stations (standard Tkinter UI)."""
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from enum import Enum
from tkinter import font as tkfont
from typing import Dict, List, Optional, Set, Tuple

os.environ.setdefault("TK_SILENCE_DEPRECATION", "1")

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
COLOR_PENDING = "#FFC107"
COLOR_PROCESSING = "#FF9800"
COLOR_READY = "#4CAF50"
COLOR_FAILED = "#F44336"
APP_BG = "#F5F5F5"
HUB_BG = "#ECEFF1"
PANEL_BG = "#FFFFFF"
TITLE_BG = "#1976D2"


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


@dataclass
class AppState:
    slots: Dict[Tuple[int, int], SlotInfo] = field(
        default_factory=lambda: {
            _slot_key(h, p): SlotInfo()
            for h in range(1, HUB_COUNT + 1)
            for p in range(1, PORTS_PER_HUB + 1)
        }
    )
    disk_to_slot: Dict[str, Tuple[int, int]] = field(default_factory=dict)
    processing: Set[str] = field(default_factory=set)
    lock: threading.Lock = field(default_factory=threading.Lock)
    poll_running: bool = True


# =========================
# DISK / MBR LOGIC
# =========================


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

    def _add_disk(size_gb: float) -> None:
        if current_disk and SD_SIZE_MIN_GB <= size_gb <= SD_SIZE_MAX_GB:
            if not any(d.disk_id == current_disk for d in found):
                found.append(
                    DiskInfo(
                        disk_id=current_disk,
                        size_gb=size_gb,
                        scheme=scheme or "unknown",
                    )
                )

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
            size_gb = _size_gb(stripped)
            if size_gb is not None:
                _add_disk(size_gb)
            continue

        size_gb = _size_gb(line)
        if size_gb is not None:
            _add_disk(size_gb)

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
    if "master boot record" in scheme:
        return True

    text = subprocess.run(["diskutil", "info", disk_id], capture_output=True, text=True).stdout.lower()
    if "guid_partition_scheme" in text:
        return False
    return "fdisk_partition_scheme" in text or "master boot record" in text


def convert_to_mbr(disk_id: str) -> Tuple[bool, str]:
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

    time.sleep(0.5)
    after = get_partition_scheme(disk_id)
    log_lines.append(f"{disk_id}: after = {after}")
    log_lines.append(combined)

    if is_mbr_formatted(disk_id):
        return True, "\n".join(log_lines)
    return False, "\n".join(log_lines) + "\nVerify failed: still not MBR."


# =========================
# GUI
# =========================


class SDCardApp:
    def __init__(self) -> None:
        self.state = AppState()
        self._log_lock = threading.Lock()
        self.slot_widgets: Dict[Tuple[int, int], tk.Label] = {}
        self.slot_labels: Dict[Tuple[int, int], tk.Label] = {}

        self.root = tk.Tk()
        self.root.title("AUTO SD CARD GPT -> MBR TOOL")
        self.root.geometry("1000x720")
        self.root.minsize(900, 640)
        self.root.configure(bg=APP_BG)

        self.title_font = tkfont.Font(family="Helvetica", size=22, weight="bold")
        self.hub_font = tkfont.Font(family="Helvetica", size=13, weight="bold")
        self.port_font = tkfont.Font(family="Helvetica", size=10, weight="bold")
        self.small_font = tkfont.Font(family="Helvetica", size=8)
        self.status_font = tkfont.Font(family="Helvetica", size=11)
        self.log_font = tkfont.Font(family="Menlo", size=10)

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self) -> None:
        header = tk.Frame(self.root, bg=TITLE_BG, padx=12, pady=10)
        header.pack(fill="x", padx=12, pady=(12, 6))
        tk.Label(
            header,
            text="AUTO SD CARD MANAGER",
            font=self.title_font,
            bg=TITLE_BG,
            fg="#FFFFFF",
        ).pack()

        self.status_label = tk.Label(
            self.root,
            text="Watching for SD cards...",
            font=self.status_font,
            bg=APP_BG,
            fg="#212121",
            anchor="w",
        )
        self.status_label.pack(fill="x", padx=16, pady=(0, 2))

        tk.Label(
            self.root,
            text="Gray=empty  Yellow=GPT  Orange=formatting  Green=MBR ready  Red=failed",
            font=self.small_font,
            bg=APP_BG,
            fg="#455A64",
            anchor="w",
        ).pack(fill="x", padx=16, pady=(0, 8))

        hubs_outer = tk.Frame(self.root, bg=PANEL_BG, bd=1, relief=tk.GROOVE)
        hubs_outer.pack(fill="both", expand=True, padx=12, pady=4)

        for hub_idx in range(1, HUB_COUNT + 1):
            hub_col = tk.Frame(hubs_outer, bg=HUB_BG, bd=1, relief=tk.RIDGE)
            hub_col.pack(side="left", fill="both", expand=True, padx=4, pady=6)

            tk.Label(
                hub_col,
                text=f"HUB {hub_idx}",
                font=self.hub_font,
                bg=HUB_BG,
                fg=TITLE_BG,
            ).pack(pady=(6, 4))

            ports_grid = tk.Frame(hub_col, bg=HUB_BG)
            ports_grid.pack(padx=4, pady=(0, 6))

            for port_idx in range(1, PORTS_PER_HUB + 1):
                row = (port_idx - 1) // 4
                col = (port_idx - 1) % 4
                key = _slot_key(hub_idx, port_idx)

                cell = tk.Frame(ports_grid, bg=HUB_BG)
                cell.grid(row=row, column=col, padx=2, pady=2)

                btn = tk.Label(
                    cell,
                    text=str(port_idx),
                    font=self.port_font,
                    width=3,
                    height=1,
                    bg=COLOR_EMPTY,
                    fg="#37474F",
                    relief=tk.RAISED,
                    bd=2,
                )
                btn.pack()

                lbl = tk.Label(
                    cell,
                    text="-",
                    font=self.small_font,
                    bg=HUB_BG,
                    fg="#607D8B",
                    width=6,
                )
                lbl.pack()

                self.slot_widgets[key] = btn
                self.slot_labels[key] = lbl

        log_frame = tk.Frame(self.root, bg=APP_BG)
        log_frame.pack(fill="x", padx=12, pady=(6, 12))

        self.textbox = tk.Text(
            log_frame,
            height=8,
            font=self.log_font,
            bg="#1B1B1B",
            fg="#00FF00",
            insertbackground="#00FF00",
            wrap=tk.WORD,
            bd=1,
            relief=tk.SUNKEN,
        )
        self.textbox.pack(fill="x")
        self.textbox.insert("end", "Application started.\n")
        self.textbox.insert("end", f"Platform: macOS ({sys.platform})\n")
        self.textbox.insert(
            "end",
            f"Layout: {HUB_COUNT} hubs x {PORTS_PER_HUB} ports\n"
            f"Command: diskutil eraseDisk FAT32 SDCARD MBRFormat diskN\n",
        )
        self.textbox.configure(state=tk.DISABLED)

    def log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")

        def _append() -> None:
            with self._log_lock:
                self.textbox.configure(state=tk.NORMAL)
                self.textbox.insert("end", f"[{ts}] {msg}\n")
                self.textbox.see("end")
                self.textbox.configure(state=tk.DISABLED)

        self.root.after(0, _append)

    def refresh_slot_ui(self, key: Tuple[int, int]) -> None:
        info = self.state.slots[key]
        btn = self.slot_widgets[key]
        lbl = self.slot_labels[key]

        color_map = {
            SlotStatus.EMPTY: COLOR_EMPTY,
            SlotStatus.PENDING: COLOR_PENDING,
            SlotStatus.PROCESSING: COLOR_PROCESSING,
            SlotStatus.READY: COLOR_READY,
            SlotStatus.FAILED: COLOR_FAILED,
        }
        fg = "#FFFFFF" if info.status != SlotStatus.EMPTY else "#37474F"
        btn.configure(bg=color_map[info.status], fg=fg)
        lbl.configure(text=info.detail[:12])

    def refresh_all_ui(self) -> None:
        for key in self.state.slots:
            self.refresh_slot_ui(key)

    def update_status_bar(self) -> None:
        counts = {s: 0 for s in SlotStatus}
        for info in self.state.slots.values():
            counts[info.status] += 1

        self.status_label.configure(
            text=(
                f"Auto-detect ON  |  Pending: {counts[SlotStatus.PENDING]}  "
                f"Processing: {counts[SlotStatus.PROCESSING]}  "
                f"Ready (MBR): {counts[SlotStatus.READY]}  "
                f"Failed: {counts[SlotStatus.FAILED]}  "
                f"Empty: {counts[SlotStatus.EMPTY]}"
            )
        )

    def _next_free_slot(self) -> Optional[Tuple[int, int]]:
        for hub in range(1, HUB_COUNT + 1):
            for port in range(1, PORTS_PER_HUB + 1):
                key = _slot_key(hub, port)
                if self.state.slots[key].status == SlotStatus.EMPTY:
                    return key
        return None

    def _set_slot(
        self, key: Tuple[int, int], status: SlotStatus, disk_id: Optional[str], detail: str
    ) -> None:
        self.state.slots[key] = SlotInfo(status=status, disk_id=disk_id, detail=detail)
        if disk_id:
            self.state.disk_to_slot[disk_id] = key
        self.root.after(0, lambda: self.refresh_slot_ui(key))

    def _clear_slot(self, key: Tuple[int, int]) -> None:
        info = self.state.slots[key]
        if info.disk_id and info.disk_id in self.state.disk_to_slot:
            del self.state.disk_to_slot[info.disk_id]
        self.state.slots[key] = SlotInfo()
        self.root.after(0, lambda: self.refresh_slot_ui(key))

    def process_disk(self, disk_id: str) -> None:
        with self.state.lock:
            if disk_id in self.state.processing:
                return
            self.state.processing.add(disk_id)

        key = self.state.disk_to_slot.get(disk_id)
        if not key:
            self.state.processing.discard(disk_id)
            return

        try:
            if is_mbr_formatted(disk_id):
                self._set_slot(key, SlotStatus.READY, disk_id, "MBR OK")
                self.log(f"{disk_id}: already MBR - green")
                return

            self._set_slot(key, SlotStatus.PROCESSING, disk_id, "format...")
            self.log(f"{disk_id}: converting GPT -> MBR...")
            self.root.after(0, self.update_status_bar)

            ok, detail = convert_to_mbr(disk_id)
            if ok:
                self._set_slot(key, SlotStatus.READY, disk_id, "MBR OK")
                self.log(f"{disk_id}: MBR format SUCCESS - green\n{detail}")
            else:
                self._set_slot(key, SlotStatus.FAILED, disk_id, "failed")
                self.log(f"{disk_id}: FAILED\n{detail}")
        finally:
            self.state.processing.discard(disk_id)
            self.root.after(0, self.update_status_bar)

    def poll_and_process(self) -> None:
        while self.state.poll_running:
            try:
                output = get_disks_output()
                disks = parse_sd_disks(output)
                present_ids = {d.disk_id for d in disks}

                with self.state.lock:
                    removed = [did for did in list(self.state.disk_to_slot) if did not in present_ids]
                    for disk_id in removed:
                        key = self.state.disk_to_slot.get(disk_id)
                        if key:
                            self.log(f"{disk_id}: removed - slot cleared")
                            self._clear_slot(key)

                    for disk in disks:
                        if disk.disk_id in self.state.disk_to_slot:
                            key = self.state.disk_to_slot[disk.disk_id]
                            info = self.state.slots[key]
                            if info.status == SlotStatus.READY and is_mbr_formatted(disk.disk_id):
                                continue
                            if info.status == SlotStatus.EMPTY:
                                self._set_slot(key, SlotStatus.PENDING, disk.disk_id, disk.disk_id)
                            continue

                        key = self._next_free_slot()
                        if key is None:
                            self.log(
                                f"{disk.disk_id}: no free slot "
                                f"(all {HUB_COUNT * PORTS_PER_HUB} ports full)"
                            )
                            continue

                        if is_mbr_formatted(disk.disk_id):
                            self._set_slot(key, SlotStatus.READY, disk.disk_id, "MBR OK")
                            self.log(f"{disk.disk_id}: inserted - already MBR (green)")
                        else:
                            self._set_slot(key, SlotStatus.PENDING, disk.disk_id, disk.disk_id)
                            self.log(f"{disk.disk_id}: inserted - GPT detected (yellow)")

                    to_process = []
                    for disk_id, key in list(self.state.disk_to_slot.items()):
                        if disk_id not in present_ids:
                            continue
                        info = self.state.slots[key]
                        if info.status == SlotStatus.PENDING and disk_id not in self.state.processing:
                            to_process.append(disk_id)

                self.root.after(0, self.update_status_bar)

                for disk_id in to_process:
                    threading.Thread(target=self.process_disk, args=(disk_id,), daemon=True).start()

            except Exception as exc:
                self.log(f"Poll error: {exc}")

            time.sleep(POLL_INTERVAL_SEC)

    def on_close(self) -> None:
        self.state.poll_running = False
        self.root.destroy()

    def run(self) -> None:
        threading.Thread(target=self.poll_and_process, daemon=True).start()
        self.refresh_all_ui()
        self.update_status_bar()
        self.root.mainloop()


def main() -> None:
    SDCardApp().run()


if __name__ == "__main__":
    main()
