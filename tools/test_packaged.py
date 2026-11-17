"""Exercise the frozen executable in an isolated data directory."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument("--media-bin", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="snap-ninja-packaged-") as directory:
        root = Path(directory)
        report = root / "report.json"
        env = dict(os.environ, SNAP_NINJA_DATA_DIR=str(root / "data"))
        if args.media_bin:
            env["PATH"] = str(args.media_bin.resolve()) + os.pathsep + env.get("PATH", "")
        process = subprocess.run([str(args.executable.resolve()), "--self-test", str(report)],
                                 env=env, timeout=150)
        result = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {}
        if process.returncode or not result.get("ok"):
            raise SystemExit(json.dumps(result, indent=2) or "Packaged app did not finish its checks")
        print("Packaged imports, API, media tools and caption render passed.")


if __name__ == "__main__":
    main()
