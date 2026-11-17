"""Native directory picker; stdout is a single JSON result for the local server."""
import json
import tkinter as tk
from tkinter import filedialog


def main():
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        folder = filedialog.askdirectory(parent=root, title="Snap Ninja — Choose output folder", mustexist=True)
        print(json.dumps({"path": folder or ""}, ensure_ascii=True), flush=True)
    finally:
        root.destroy()


if __name__ == "__main__":
    main()
