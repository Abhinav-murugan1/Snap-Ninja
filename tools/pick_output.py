"""Native directory picker; stdout is a single JSON result for the local server."""
import json
import sys
from pathlib import Path
import tkinter as tk
from tkinter import filedialog


def main():
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        folder = filedialog.askdirectory(parent=root, title="Snap Ninja — Choose output folder", mustexist=True)
        value = json.dumps({"path": folder or ""}, ensure_ascii=True)
        if len(sys.argv) > 1:
            Path(sys.argv[1]).write_text(value, encoding="utf-8")
        else:
            print(value, flush=True)
    finally:
        root.destroy()


if __name__ == "__main__":
    main()
