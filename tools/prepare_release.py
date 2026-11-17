"""Build a source-only publication snapshot with an explicit allowlist.

Private application data is never copied. Run from any directory with Python.
The snapshot is under release/snap-ninja; inspect it before pushing.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "release" / "snap-ninja"
ROOT_FILES = (
    ".gitignore", ".gitattributes", ".env.example", "app.py", "cli.py", "desktop.py",
    "run.bat", "make_shortcuts.bat", "requirements.txt", "requirements-gpu.txt",
    "README.md", "LICENSE", "CONTRIBUTING.md", "SECURITY.md", "THIRD_PARTY_NOTICES.md",
)
STATIC_FILES = (
    "index.html", "app.css", "app.js", "setup.js", "output.js", "youtube-guide.js",
    "snap-ninja.png", "snap-ninja.ico", "snap-ninja-v1.ico", "snap-ninja-source.png",
)


def selected_files() -> list[Path]:
    items = [ROOT / name for name in ROOT_FILES]
    items += list((ROOT / "clipper").glob("*.py"))
    items += [ROOT / "clipper/models" / name for name in
              ("face_detection_yunet_2023mar.onnx", "LICENSE.YuNet")]
    items += [ROOT / "static" / name for name in STATIC_FILES]
    items += [p for p in (ROOT / "tests").iterdir() if p.is_file() and p.suffix in {".py", ".js"}]
    items += [ROOT / "tools" / name for name in
              ("make_shortcuts.ps1", "make_icon.py", "pick_output.py", "preview_ui.py", "prepare_release.py")]
    items += [p for p in (ROOT / "docs").rglob("*") if p.is_file() and p.suffix in {".md", ".png"}]
    items += [p for p in (ROOT / ".github").rglob("*") if p.is_file() and p.suffix in {".md", ".yml"}]
    return sorted(set(items))


def check_text_files(files: list[Path]) -> None:
    patterns = [re.compile(p) for p in (
        r"sk-ant-[A-Za-z0-9_-]{20,}", r"gh[pousr]_[A-Za-z0-9]{20,}",
        r"github_pat_[A-Za-z0-9_]{20,}", r"GOCSPX-[A-Za-z0-9_-]{12,}",
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    )]
    private_values = []
    env = ROOT / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            key, sep, value = line.partition("=")
            if sep and any(word in key.upper() for word in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
                value = value.strip().strip('"\'')
                if len(value) >= 16:
                    private_values.append(value)
    for path in files:
        if path.suffix.lower() in {".onnx", ".png", ".ico"}:
            continue
        text = path.read_text(encoding="utf-8-sig")
        if any(p.search(text) for p in patterns) or any(value in text for value in private_values):
            raise RuntimeError(f"Possible credential in publication file: {path.relative_to(ROOT)}")


def main() -> None:
    files = selected_files()
    for path in files:
        if not path.is_file():
            raise FileNotFoundError(path)
    check_text_files(files)
    DEST.mkdir(parents=True, exist_ok=True)
    expected = set()
    for source in files:
        relative = source.relative_to(ROOT)
        expected.add(relative.as_posix())
        target = DEST / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    # Empty music directory is intentional; no user's tracks are redistributed.
    (DEST / "music").mkdir(exist_ok=True)
    (DEST / "music/.gitkeep").touch()
    expected.add("music/.gitkeep")
    for path in DEST.rglob("*"):
        if not path.is_file() or ".git" in path.relative_to(DEST).parts:
            continue
        if path.relative_to(DEST).as_posix() not in expected:
            raise RuntimeError(f"Unexpected file in release snapshot: {path.relative_to(DEST)}")
    print(f"Prepared {len(expected)} approved source/assets files. Credential checks passed.")
    print(DEST)


if __name__ == "__main__":
    main()
