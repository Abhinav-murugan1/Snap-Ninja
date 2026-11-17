"""Windows x64 desktop bundle. Run through tools/build_windows.py."""
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
datas = [(str(root / "static"), "static"),
         (str(root / "clipper/models"), "clipper/models"),
         (str(root / "packaging/vendor/node"), "vendor/node"),
         (str(root / "LICENSE"), "."),
         (str(root / "THIRD_PARTY_NOTICES.md"), "."),
         (str(Path(sys.base_prefix) / "LICENSE.txt"), "licenses/python"),
         (str(root / "packaging/vendor/python-licenses.json"), "licenses"),
         (str(root / "packaging/vendor/payload-manifest.json"), "licenses")]
binaries, hiddenimports = [], ["app", "tools.pick_output", "tkinter.filedialog",
                              "webview.platforms.winforms", "webview.platforms.edgechromium"]
for package in ("webview", "pythonnet", "clr_loader", "ctranslate2", "faster_whisper",
                "onnxruntime", "tokenizers", "googleapiclient", "yt_dlp", "yt_dlp_ejs"):
    data, libs, imports = collect_all(package)
    datas += data
    binaries += libs
    hiddenimports += imports
hiddenimports += collect_submodules("google.auth") + collect_submodules("google.oauth2")
for name in ("faster-whisper", "ctranslate2", "yt-dlp", "yt-dlp-ejs",
             "google-api-python-client", "google-auth", "pywebview", "pythonnet"):
    datas += copy_metadata(name)

a = Analysis([str(root / "desktop.py")], pathex=[str(root)], binaries=binaries,
             datas=datas, hiddenimports=hiddenimports,
             excludes=["PyQt5", "PyQt6", "PySide2", "PySide6", "torch", "nvidia",
                       "webview.platforms.gtk", "webview.platforms.qt", "webview.platforms.cocoa"],
             noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Snap Ninja",
          console=False, debug=False, strip=False, upx=False,
          icon=str(root / "static/snap-ninja-v1.ico"))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Snap Ninja")
