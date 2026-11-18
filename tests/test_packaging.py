"""Check installed/source storage boundaries and archive validation without user data."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from clipper import paths
from tools import build_windows


class InstallationPathsTests(unittest.TestCase):
    def test_source_keeps_existing_jobs_location(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(paths, "frozen", return_value=False):
            self.assertEqual(paths.data_root(), Path(paths.__file__).resolve().parent.parent)

    def test_packaged_assets_and_user_data_are_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            with patch.dict(os.environ, {"LOCALAPPDATA": str(base)}, clear=True), \
                 patch.object(paths, "frozen", return_value=True), \
                 patch.object(sys, "_MEIPASS", str(base / "read-only-assets"), create=True):
                self.assertEqual(paths.data_root(), base / "SnapNinja")
                self.assertEqual(paths.resource_root(), base / "read-only-assets")
                self.assertFalse(paths.data_root().exists())

    def test_relative_data_override_is_rejected(self):
        with patch.dict(os.environ, {"SNAP_NINJA_DATA_DIR": "relative/user-data"}):
            with self.assertRaises(ValueError):
                paths.data_root()

    def test_private_tools_do_not_change_source_environment(self):
        with patch.dict(os.environ, {"PATH": "original"}, clear=True), \
             patch.object(paths, "frozen", return_value=False):
            paths.configure_runtime()
            self.assertEqual(os.environ["PATH"], "original")
            self.assertNotIn("HF_HOME", os.environ)

    def test_installed_tools_and_model_cache_are_private(self):
        with tempfile.TemporaryDirectory() as folder:
            # Windows can give tempfile a short or redirected path. Start from
            # the actual on-disk path and use an existing executable fixture.
            base = Path(folder).resolve()
            executable = base / "program" / "Snap Ninja.exe"
            executable.parent.mkdir()
            executable.touch()
            ffmpeg = executable.parent / "dependencies" / "ffmpeg"
            node = base / "assets" / "vendor" / "node"
            ffmpeg.mkdir(parents=True)
            node.mkdir(parents=True)
            env = {"PATH": "existing-tools", "LOCALAPPDATA": str(base)}
            with patch.dict(os.environ, env, clear=True), \
                 patch.object(paths, "frozen", return_value=True), \
                 patch.object(paths, "resource_root", return_value=base / "assets"), \
                 patch.object(sys, "executable", str(executable)):
                paths.configure_runtime()
                self.assertTrue(paths.data_root().is_dir())
                self.assertEqual(Path(os.environ["HF_HOME"]).resolve(),
                                 (base / "SnapNinja/models/huggingface").resolve())
                path_entries = os.environ["PATH"].split(os.pathsep)
                self.assertEqual(path_entries[2:], ["existing-tools"])
                for actual, expected in zip(path_entries[:2], (ffmpeg, node), strict=True):
                    self.assertTrue(Path(actual).samefile(expected), f"Expected {expected}, got {actual}")

    def test_frozen_picker_uses_helper_mode_instead_of_running_python_script(self):
        result = Path("result.json")
        with patch.object(paths, "frozen", return_value=True):
            self.assertEqual(paths.picker_command(result), [sys.executable, "--pick-output", str(result)])


class BuildDownloadTests(unittest.TestCase):
    def test_webview_falls_back_to_a_verified_microsoft_installer(self):
        def stage_download(url, destination):
            destination.write_bytes(b"MZ" + url.encode())
            return hashlib.sha256(destination.read_bytes()).hexdigest()

        with tempfile.TemporaryDirectory() as folder:
            vendor = Path(folder)
            with patch.object(build_windows, "VENDOR", vendor), \
                 patch.object(build_windows, "download", side_effect=stage_download), \
                 patch.object(build_windows.subprocess, "run", side_effect=[
                     subprocess.CompletedProcess([], 1, "Authenticode: NotTrusted", ""),
                     subprocess.CompletedProcess([], 0, "Authenticode: Valid", ""),
                 ]):
                manifest = build_windows.prepare_webview()
            self.assertEqual(manifest["url"], build_windows.WEBVIEW_DOWNLOADS[1][1])
            installed = vendor / "MicrosoftEdgeWebview2Setup.exe"
            self.assertEqual(installed.read_bytes(), b"MZ" + manifest["url"].encode())
            self.assertEqual(manifest["sha256"], hashlib.sha256(installed.read_bytes()).hexdigest())

    def test_payload_mismatch_never_creates_executable(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(build_windows, "fetch", return_value=b"wrong"):
            destination = Path(folder) / "tool.exe"
            with self.assertRaises(ValueError):
                build_windows.download("https://example.invalid/tool.exe", destination, "0" * 64)
            self.assertFalse(destination.exists())

    def test_verified_payload_is_written(self):
        payload = b"test payload"
        with tempfile.TemporaryDirectory() as folder, patch.object(build_windows, "fetch", return_value=payload):
            destination = Path(folder) / "tool"
            build_windows.download("https://example.invalid/tool", destination, hashlib.sha256(payload).hexdigest())
            self.assertEqual(destination.read_bytes(), payload)

    def test_archive_cannot_write_outside_staging(self):
        for member in ("../outside.txt", "nested/../../outside.txt", "..\\outside.txt", "C:/outside.txt"):
            with self.subTest(member=member), tempfile.TemporaryDirectory() as folder:
                base = Path(folder)
                archive = base / "payload.zip"
                with zipfile.ZipFile(archive, "w") as zipped:
                    zipped.writestr(member, b"payload")
                with self.assertRaises(ValueError):
                    build_windows.extract(archive, base / "stage")
                self.assertFalse((base / "outside.txt").exists())

    def test_archive_extracts_valid_members(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            archive = base / "payload.zip"
            with zipfile.ZipFile(archive, "w") as zipped:
                zipped.writestr("tool/LICENSE", "License text")
            build_windows.extract(archive, base / "stage")
            self.assertEqual((base / "stage/tool/LICENSE").read_text(), "License text")


if __name__ == "__main__":
    unittest.main()
