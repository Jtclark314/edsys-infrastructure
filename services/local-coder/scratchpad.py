"""Small native GUI for desktop qualification and disposable working notes."""
from pathlib import Path
import os
import tkinter as tk

root = tk.Tk()
root.title("EdSys Local Coder Scratchpad")
root.geometry("1100x650+80+60")
root.configure(bg="#172333")
tk.Label(root, text="LOCAL CODER SCRATCHPAD", fg="white", bg="#172333",
         font=("DejaVu Sans", 22, "bold")).pack(pady=18)
tk.Label(root, text="Type your note below, then click Save note.", fg="white",
         bg="#172333", font=("DejaVu Sans", 16)).pack(pady=8)
editor = tk.Text(root, font=("DejaVu Sans", 20), height=10, wrap="word")
editor.pack(fill="both", expand=True, padx=30, pady=12)
status = tk.StringVar(value="No note saved")


def save() -> None:
    directory = Path(os.environ.get("EDSYS_DESKTOP_NOTES", "/mnt/ai-store/local-coder/desktop-notes"))
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = directory / "note.txt"
    target.write_text(editor.get("1.0", "end-1c"))
    target.chmod(0o600)
    status.set("Note saved successfully")


tk.Button(root, text="Save note", command=save, font=("DejaVu Sans", 18),
          width=18, bg="#69d7ac").pack(pady=12)
tk.Label(root, textvariable=status, fg="white", bg="#172333",
         font=("DejaVu Sans", 16)).pack(pady=12)
root.mainloop()
