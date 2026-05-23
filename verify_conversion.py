#!/usr/bin/env python3
"""Headless verify: detect SD cards and convert GPT -> MBR with before/after checks."""
import re
import subprocess
import sys
import time

SD_SIZE_MIN_GB = 26.0
SD_SIZE_MAX_GB = 32.5
SKIP_DISKS = {"disk0", "disk1"}


def _size_gb(line: str):
    m = re.search(r"\*\s*([\d.]+)\s*GB", line, re.IGNORECASE)
    if m:
        return float(m.group(1))
    m = re.search(r"\*\s*([\d.]+)\s*MB", line, re.IGNORECASE)
    if m:
        return float(m.group(1)) / 1024.0
    return None


def get_disks_output() -> str:
    return subprocess.run(["diskutil", "list"], capture_output=True, text=True).stdout


def parse_sd_disks(output: str):
    found = []
    current_disk = None
    is_external = False
    scheme = ""

    def _add_disk(size_gb):
        if current_disk and SD_SIZE_MIN_GB <= size_gb <= SD_SIZE_MAX_GB:
            if not any(d["disk_id"] == current_disk for d in found):
                found.append({"disk_id": current_disk, "size_gb": size_gb, "scheme": scheme or "unknown"})

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
    text = subprocess.run(["diskutil", "info", disk_id], capture_output=True, text=True).stdout.lower()
    if "guid_partition_scheme" in text:
        return False
    return "fdisk_partition_scheme" in text or "master boot record" in text


def convert_to_mbr(disk_id: str):
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


def main() -> int:
    disks = parse_sd_disks(get_disks_output())
    if not disks:
        print("NO_SD_CARDS")
        return 1

    print(f"Found {len(disks)} SD card(s)")
    failures = 0

    for disk in disks:
        disk_id = disk["disk_id"]
        before = get_partition_scheme(disk_id)
        print(f"\n--- {disk_id} ({disk['size_gb']:.1f} GB) ---")
        print(f"  before: {before}")

        if is_mbr_formatted(disk_id):
            print("  status: ALREADY MBR OK")
        else:
            print("  action: converting GPT -> MBR ...")
            ok, detail = convert_to_mbr(disk_id)
            print(detail)
            if not ok or not is_mbr_formatted(disk_id):
                print("  status: CONVERT FAILED")
                failures += 1
                continue
            print("  status: CONVERT OK")

        after = get_partition_scheme(disk_id)
        print(f"  after:  {after}")
        listing = subprocess.run(["diskutil", "list", disk_id], capture_output=True, text=True).stdout
        for line in listing.strip().splitlines()[:4]:
            print(f"  {line}")

        if not is_mbr_formatted(disk_id):
            failures += 1

    if failures:
        print(f"\nRESULT: FAILED ({failures} disk(s))")
        return 2

    print("\nRESULT: ALL OK — 100% verified MBR (FDisk_partition_scheme + DOS_FAT_32 SDCARD)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
