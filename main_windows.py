import customtkinter as ctk
import subprocess
import threading
import re
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

bg_image = ctk.CTkImage(
    light_image=Image.open("background.jpg"),
    dark_image=Image.open("background.jpg"),
    size=(900, 650)
)

bg_label = ctk.CTkLabel(
    app,
    image=bg_image,
    text=""
)

bg_label.place(
    relx=0,
    rely=0,
    relwidth=1,
    relheight=1
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
    height=50
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
    corner_radius=10
)

textbox.pack(pady=20)

textbox.insert("end", "Application Started...\n")

# =========================
# GET DISKS
# =========================

def get_disks():

    command = "list disk\n"

    with open("list.txt", "w") as f:
        f.write(command)

    result = subprocess.run(
        ["diskpart", "/s", "list.txt"],
        capture_output=True,
        text=True
    )

    return result.stdout

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
        "========== AUTO DETECT STARTED ==========\n\n"
    )

    output = get_disks()

    textbox.insert("end", output)

    lines = output.splitlines()

    detected_disks = []

    for line in lines:

        if "Online" in line and "29 GB" in line:

            numbers = re.findall(r'\d+', line)

            if len(numbers) >= 1:

                disk_number = numbers[0]

                if disk_number != "0":

                    detected_disks.append(disk_number)

    textbox.insert(
        "end",
        "\n========== PROCESS STARTED ==========\n"
    )

    for d in detected_disks:

        textbox.insert(
            "end",
            f"\nProcessing Disk {d}...\n"
        )

        commands = f"""
select disk {d}
clean
convert mbr
exit
"""

        with open("convert.txt", "w") as f:
            f.write(commands)

        result = subprocess.run(
            ["diskpart", "/s", "convert.txt"],
            capture_output=True,
            text=True
        )

        if "successfully converted" in result.stdout.lower():

            textbox.insert(
                "end",
                f"Disk {d} : GPT -> MBR SUCCESS\n"
            )

        else:

            textbox.insert(
                "end",
                f"Disk {d} : FAILED\n"
            )

    textbox.insert(
        "end",
        "\n========== ALL PROCESS COMPLETED ==========\n"
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
        target=auto_convert
    ).start()
)

btn.pack(pady=20)


# =========================
# RUN APP
# =========================

app.mainloop()
