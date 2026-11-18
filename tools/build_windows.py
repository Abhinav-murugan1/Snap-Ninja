"""Build the Windows installer in a clean, internet-connected Windows environment."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "packaging/vendor"
CACHE = ROOT / "build/downloads"
MEDIA_BASE = "https://www.gyan.dev/ffmpeg/builds/"
NODE_BASE = "https://nodejs.org/dist/latest-v22.x/"
WEBVIEW_DOWNLOADS = (
    ("Evergreen bootstrapper", "https://go.microsoft.com/fwlink/p/?LinkId=2124703"),
    ("Evergreen standalone x64 installer", "https://go.microsoft.com/fwlink/?LinkId=2124701"),
)


def fetch(url: str) -> bytes:
    if not url.startswith("https://"):
        raise ValueError("Downloads require HTTPS")
    with urllib.request.urlopen(url, timeout=180) as response:
        return response.read()


def checksum(text: str) -> str:
    match = re.match(r"\s*([0-9a-fA-F]{64})(?:\s|$)", text)
    if not match:
        raise ValueError("Distributor did not provide a valid SHA-256")
    return match[1].lower()


def download(url: str, destination: Path, expected: str | None = None) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = fetch(url)
    digest = hashlib.sha256(payload).hexdigest()
    if expected and digest != expected:
        raise ValueError(f"SHA-256 mismatch for {url}")
    destination.write_bytes(payload)
    return digest


def extract(archive: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zipped:
        for item in zipped.infolist():
            # Do not allow an archive to write outside this build's staging folder.
            name = item.filename.replace("\\", "/")
            path = target / name
            if ":" in name or not path.resolve().is_relative_to(target.resolve()):
                raise ValueError("Unsafe archive member")
        zipped.extractall(target)


def prepare_webview() -> dict:
    destination = VENDOR / "MicrosoftEdgeWebview2Setup.exe"
    errors = []
    with tempfile.TemporaryDirectory(prefix="webview-", dir=VENDOR) as temp:
        candidate = Path(temp) / destination.name
        for kind, url in WEBVIEW_DOWNLOADS:
            print(f"Downloading Microsoft WebView2 {kind}", flush=True)
            try:
                digest = download(url, candidate)
                with candidate.open("rb") as payload:
                    if payload.read(2) != b"MZ":
                        raise ValueError("Microsoft download did not return a Windows executable")
                env = dict(os.environ, SNAP_NINJA_SIGNATURE_FILE=str(candidate))
                verification = subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                     "$ErrorActionPreference = 'Stop'; "
                     "$signature = Get-AuthenticodeSignature -LiteralPath $env:SNAP_NINJA_SIGNATURE_FILE; "
                     "$signer = $signature.SignerCertificate.Subject; "
                     "Write-Output ('Authenticode: {0}; {1}; signer: {2}' -f $signature.Status, $signature.StatusMessage, $signer); "
                     "if ($signature.Status -ne 'Valid' -or $signer -notmatch '(^|,\\s*)O=Microsoft Corporation(,|$)') { exit 1 }"],
                    env=env, capture_output=True, text=True, errors="replace", timeout=120,
                )
                if verification.returncode:
                    raise ValueError((verification.stdout + verification.stderr).strip()
                                     or f"Signature check exited with code {verification.returncode}")
                print(verification.stdout.strip(), flush=True)
                candidate.replace(destination)
                return {"url": url, "sha256": digest, "signature": "Microsoft Corporation", "package": kind}
            except (OSError, ValueError, subprocess.TimeoutExpired) as error:
                message = f"{kind}: {error}"
                errors.append(message)
                print(message, flush=True)
    raise RuntimeError("Could not verify a Microsoft WebView2 installer:\n" + "\n".join(errors))


def prepare_payloads() -> Path:
    VENDOR.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    sums = fetch(NODE_BASE + "SHASUMS256.txt").decode("utf-8")
    match = re.search(r"(?m)^([a-f0-9]{64})\s+(node-v22\.\d+\.\d+-win-x64\.zip)\s*$", sums)
    if not match:
        raise RuntimeError("Node.js x64 archive was not found in its official checksum list")
    digest, name = match.groups()
    node_archive = CACHE / name
    download(NODE_BASE + name, node_archive, digest)
    node_stage = CACHE / "node"
    extract(node_archive, node_stage)
    node_root = node_stage / name.removesuffix(".zip")
    node_dest = VENDOR / "node"
    node_dest.mkdir(exist_ok=True)
    for filename in ("node.exe", "LICENSE"):
        shutil.copy2(node_root / filename, node_dest / filename)

    version = fetch(MEDIA_BASE + "ffmpeg-release-essentials.zip.ver").decode().strip()
    if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", version):
        raise ValueError("Unexpected FFmpeg release version")
    media_url = MEDIA_BASE + f"packages/ffmpeg-{version}-essentials_build.zip"
    media_hash = checksum(fetch(media_url + ".sha256").decode())
    media_archive = CACHE / "ffmpeg.zip"
    download(media_url, media_archive, media_hash)
    media_stage = CACHE / "media"
    extract(media_archive, media_stage)
    ffmpeg = list(media_stage.glob("*/bin/ffmpeg.exe"))
    if len(ffmpeg) != 1:
        raise RuntimeError("Unexpected FFmpeg archive structure")

    webview = prepare_webview()
    (ROOT / "packaging/generated.iss").write_text(
        f'#define MediaURL "{media_url}"\n#define MediaSHA256 "{media_hash}"\n', encoding="utf-8")
    (VENDOR / "payload-manifest.json").write_text(json.dumps({
        "node": {"url": NODE_BASE + name, "sha256": digest},
        "ffmpeg": {"url": media_url, "sha256": media_hash, "version": version,
                   "license": "GPL-3.0; downloaded from distributor during installation"},
        "webview2": webview,
    }, indent=2), encoding="utf-8")
    return ffmpeg[0].parent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="0.1.0")
    args = parser.parse_args()
    if sys.platform != "win32" or sys.maxsize <= 2**32:
        parser.error("Build with 64-bit Python on Windows")
    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        parser.error("Version must have the form 1.2.3")
    compiler = shutil.which("ISCC.exe")
    if not compiler:
        candidates = [Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Inno Setup 6/ISCC.exe",
                      Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Inno Setup 7/ISCC.exe"]
        compiler = next((str(p) for p in candidates if p.is_file()), None)
    if not compiler:
        raise SystemExit("Inno Setup is required on the build computer (not on users' computers).")
    media_bin = prepare_payloads()
    with (VENDOR / "python-licenses.json").open("w", encoding="utf-8") as report:
        subprocess.run([sys.executable, "-m", "piplicenses", "--format=json", "--with-license-file", "--no-license-path"],
                       stdout=report, check=True, cwd=ROOT)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "packaging/snap_ninja.spec"], cwd=ROOT, check=True)
    subprocess.run([sys.executable, "tools/test_packaged.py", "dist/Snap Ninja/Snap Ninja.exe",
                    "--media-bin", str(media_bin)], cwd=ROOT, check=True)
    subprocess.run([compiler, "/DAppVersion=" + args.version, "packaging/windows.iss"], cwd=ROOT, check=True)
    installer = ROOT / "dist/installer" / f"SnapNinja-Setup-{args.version}-x64.exe"
    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    installer.with_suffix(".exe.sha256").write_text(f"{digest}  {installer.name}\n", encoding="utf-8")
    print(f"Built {installer}")


if __name__ == "__main__":
    main()
