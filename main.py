import customtkinter as ctk
import subprocess
import threading
import re
import os
import sys
from PIL import Image

# =========================
# APP SETTINGS
# =========================

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

app = ctk.CTk()
app.geometry("900x650")
app.title("AUTO SD CARD GPT -> MBR TOOL")

# =========================
# BACKGROUND IMAGE
# =========================

# =========================
# BACKGROUND IMAGE
# =========================

_bg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "background.jpg")
if os.path.isfile(_bg_path):
    bg_image = ctk.CTkImage(
        light_image=Image.open(_bg_path),
        dark_image=Image.open(_bg_path),
        size=(900, 650),
    )

    bg_label = ctk.CTkLabel(
        app,
        image=bg_image,
        text="",
    )

    bg_label.place(
        relx=0,
        rely=0,
        relwidth=1,
        relheight=1,
    )

    bg_label.place(x=0, y=0)

# =========================
# TITLE
# =========================

title = ctk.CTkLabel(
    app,
    text="AUTO SD CARD MANAGER",
    font=("Arial", 30, "bold"),
    fg_color="#1976D2",
    corner_radius=12,
    text_color="white",
    width=500,
    height=50,
)

title.pack(pady=20)

# =========================
# TEXTBOX
# =========================

textbox = ctk.CTkTextbox(
    app,
    width=760,
    height=400,
    font=("Consolas", 14),
    fg_color="black",
    text_color="lime",
    corner_radius=10,
)

textbox.pack(pady=20)

textbox.insert("end", "Application Started...\n")
textbox.insert("end", f"Platform: macOS ({sys.platform})\n")

# =========================
# GET DISKS (macOS diskutil)
# =========================


def get_disks():
    result = subprocess.run(
        ["diskutil", "list"],
        capture_output=True,
        text=True,
    )
    return result.stdout


def _size_gb(line: str) -> float | None:
    """Parse '*29.8 GB' style size from diskutil list line."""
    m = re.search(r"\*\s*([\d.]+)\s*GB", line, re.IGNORECASE)
    if m:
        return float(m.group(1))
    m = re.search(r"\*\s*([\d.]+)\s*MB", line, re.IGNORECASE)
    if m:
        return float(m.group(1)) / 1024.0
    return None


def detect_sd_disks(output: str) -> list[str]:
    """
    Find external ~29 GB SD cards (typical 32 GB cards report ~29–31 GB).
    Returns disk identifiers like disk5, disk6 (not disk5s1).
    """
    detected: list[str] = []
    current_disk: str | None = None
    is_external = False

    for line in output.splitlines():
        header = re.match(r"/dev/(disk\d+)\s+\(([^)]+)\):", line.strip())
        if header:
            current_disk = header.group(1)
            flags = header.group(2).lower()
            is_external = "external" in flags and "physical" in flags
            continue

        if not current_disk or not is_external:
            continue

        size_gb = _size_gb(line)
        if size_gb is None:
            continue

        # Match Windows tool logic: ~29 GB removable SD cards
        if 26.0 <= size_gb <= 32.5 and current_disk not in detected:
            # Skip internal boot disk (usually disk0 / disk1)
            if current_disk not in ("disk0", "disk1"):
                detected.append(current_disk)

    return detected


# =========================
# AUTO DETECT + CONVERT
# =========================

is_running = False


def auto_convert():
    textbox.insert("end", "\nButton Clicked...\n")

    global is_running

    if is_running:
        return

    is_running = True

    textbox.delete("1.0", "end")

    textbox.insert(
        "end",
        "========== AUTO DETECT STARTED ==========\n\n",
    )

    output = get_disks()

    textbox.insert("end", output)

    detected_disks = detect_sd_disks(output)

    if not detected_disks:
        textbox.insert(
            "end",
            "\nNo ~29 GB external SD cards detected.\n"
            "Check USB hubs are connected and cards are inserted.\n",
        )
        textbox.insert(
            "end",
            "\n========== ALL PROCESS COMPLETED ==========\n",
        )
        is_running = False
        return

    textbox.insert(
        "end",
        f"\nDetected disks: {', '.join(detected_disks)}\n",
    )

    textbox.insert(
        "end",
        "\n========== PROCESS STARTED ==========\n",
    )

    for d in detected_disks:

        textbox.insert(
            "end",
            f"\nProcessing {d}...\n",
        )

        # macOS equivalent of: clean + convert mbr
        # Erases the whole disk and applies MBR partition scheme
        result = subprocess.run(
            ["diskutil", "eraseDisk", "MBRFormat", "SDCARD", d],
            capture_output=True,
            text=True,
        )

        combined = (result.stdout + result.stderr).lower()

        if result.returncode == 0 or "finished erase" in combined:

            textbox.insert(
                "end",
                f"{d} : GPT -> MBR SUCCESS\n",
            )

        else:

            textbox.insert(
                "end",
                f"{d} : FAILED\n{result.stdout}{result.stderr}\n",
            )

    textbox.insert(
        "end",
        "\n========== ALL PROCESS COMPLETED ==========\n",
    )

    is_running = False


btn = ctk.CTkButton(
    app,
    text="AUTO DETECT & CONVERT",
    width=320,
    height=55,
    font=("Arial", 18, "bold"),
    fg_color="#1976D2",
    hover_color="#1565C0",
    text_color="white",
    corner_radius=12,
    command=lambda: threading.Thread(
        target=auto_convert,
    ).start(),
)

btn.pack(pady=20)


# =========================
# RUN APP
# =========================

app.mainloop()
