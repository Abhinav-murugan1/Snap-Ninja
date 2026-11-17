"""Check installed/source storage boundaries and archive validation without user data."""
import hashlib
import os
from pathlib import Path
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
            base = Path(folder)
            env = {"PATH": "existing-tools", "LOCALAPPDATA": str(base)}
            with patch.dict(os.environ, env, clear=True), \
                 patch.object(paths, "frozen", return_value=True), \
                 patch.object(paths, "resource_root", return_value=base / "assets"), \
                 patch.object(sys, "executable", str(base / "program/Snap Ninja.exe")):
                paths.configure_runtime()
                self.assertTrue(paths.data_root().is_dir())
                self.assertEqual(os.environ["HF_HOME"], str(base / "SnapNinja/models/huggingface"))
                self.assertEqual([Path(p) for p in os.environ["PATH"].split(os.pathsep)[:2]],
                                 [base / "program/dependencies/ffmpeg", base / "assets/vendor/node"])
                self.assertTrue(os.environ["PATH"].endswith("existing-tools"))

    def test_frozen_picker_uses_helper_mode_instead_of_running_python_script(self):
        result = Path("result.json")
        with patch.object(paths, "frozen", return_value=True):
            self.assertEqual(paths.picker_command(result), [sys.executable, "--pick-output", str(result)])


class BuildDownloadTests(unittest.TestCase):
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
