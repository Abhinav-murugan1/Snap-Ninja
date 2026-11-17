"""Destination and model regression tests, using disposable media only."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clipper import brain
from clipper.options import Options
from clipper.output import clips_directory, validate_destination
from test_app import web, fake_store


class OutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="snap-ninja-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.jobs = self.root / "jobs"
        self.export = self.root / "exports"
        self.client = web.app.test_client()
        self.headers = {"X-Snap-Ninja": "1"}
        fake_store.reset_mock()

    def test_legacy_and_custom_output_paths(self):
        job = self.jobs / "test-job"
        self.assertEqual(clips_directory(job), job / "clips")
        self.assertEqual(clips_directory(job, str(self.export)), self.export / "test-job" / "clips")

    def test_destination_validation(self):
        self.assertEqual(validate_destination("", self.jobs), "")
        self.assertEqual(validate_destination(str(self.jobs), self.jobs), "")
        path = validate_destination(str(self.export), self.jobs)
        self.assertTrue(Path(path).is_dir())
        self.assertEqual(list(self.export.iterdir()), [])
        for invalid in ("relative/path", str(self.jobs / "existing-job")):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_destination(invalid, self.jobs)

    def test_file_cannot_be_used_as_a_folder(self):
        file = self.root / "video.mp4"
        file.write_bytes(b"original")
        with self.assertRaises(OSError):
            validate_destination(str(file), self.jobs)
        self.assertEqual(file.read_bytes(), b"original")

    def test_unwritable_destination(self):
        with patch("clipper.output.tempfile.TemporaryFile", side_effect=PermissionError("denied")):
            response = self.client.post("/api/output/validate", headers=self.headers, json={"path": str(self.export)})
        self.assertEqual(response.status_code, 400)

    def test_output_api_and_new_job_snapshot(self):
        response = self.client.post("/api/output/validate", headers=self.headers, json={"path": str(self.export)})
        self.assertEqual(response.status_code, 200)
        fake_store.create.return_value = {"id": "new"}
        response = self.client.post("/api/jobs", headers=self.headers,
            json={"url": "https://www.youtube.com/watch?v=example", "options": {"output_dir": str(self.export)}})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(fake_store.create.call_args.args[2].output_dir, str(self.export.resolve()))

    def test_native_picker_cancellation_and_failure(self):
        for code, stdout, status in ((0, '{"path": ""}', 200), (1, "", 503)):
            with self.subTest(code=code), patch("subprocess.run", return_value=subprocess.CompletedProcess([], code, stdout)):
                response = self.client.post("/api/output/browse", headers=self.headers)
            self.assertEqual(response.status_code, status)
            if not code:
                self.assertTrue(response.json["cancelled"])
        self.assertEqual(self.client.post("/api/output/browse").status_code, 403)

    def test_preview_download_and_upload_lookup_in_custom_folder(self):
        job = {"id": "test", "options": {"output_dir": str(self.export)},
               "clips": [{"files": {"short": "clip01_short.mp4"}}]}
        output = self.export / "test" / "clips"
        output.mkdir(parents=True)
        video = output / "clip01_short.mp4"
        video.write_bytes(b"preview bytes")
        fake_store.get.return_value = job
        with self.client.get("/files/test/clip01_short.mp4") as response:
            self.assertEqual(response.data, b"preview bytes")
        self.assertEqual(web._clip_file(job, 0, "short"), video)
        response = self.client.get("/api/jobs/test")
        self.assertEqual(Path(response.json["output_path"]), output)
        with patch.object(web.os, "startfile", create=True) as open_folder:
            response = self.client.post("/api/jobs/test/open-folder", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        open_folder.assert_called_once_with(output)


class ModelTests(unittest.TestCase):
    def test_advanced_is_opt_in(self):
        installed = {brain.DEFAULT_MODEL, brain.SMART_MODEL, brain.ADVANCED_MODEL}
        with patch.object(brain, "installed", return_value=installed), patch.object(brain, "gpu_memory_mb", return_value=8000):
            self.assertEqual(brain.pick_model("auto"), brain.DEFAULT_MODEL)
            self.assertEqual(brain.pick_model(brain.ADVANCED_MODEL), brain.ADVANCED_MODEL)
        with patch.object(brain, "installed", return_value=installed), patch.object(brain, "gpu_memory_mb", return_value=48000):
            self.assertEqual(brain.pick_model("auto"), brain.SMART_MODEL)

    def test_advanced_alias_and_missing_model_fallback(self):
        with patch.object(brain, "installed", return_value={"qwen3.8:latest"}):
            self.assertEqual(brain.pick_model(brain.ADVANCED_MODEL), "qwen3.8:latest")
        with patch.object(brain, "installed", return_value={brain.DEFAULT_MODEL}):
            self.assertEqual(brain.pick_model(brain.ADVANCED_MODEL), brain.DEFAULT_MODEL)

    def test_advanced_vision_and_json_payload(self):
        self.assertEqual(brain.pick_vision_model(brain.ADVANCED_MODEL), brain.ADVANCED_MODEL)
        self.assertEqual(brain.pick_vision_model(brain.DEFAULT_MODEL), brain.VISION_MODEL)
        for model in (brain.ADVANCED_MODEL, brain.DEFAULT_MODEL):
            with patch.object(brain, "_request", return_value={"message": {"content": '{"ok": true}'}}) as request:
                self.assertEqual(brain._chat(model, "system", "user", {"type": "object"}), {"ok": True})
            payload = request.call_args.args[1]
            self.assertEqual("think" in payload, model == brain.ADVANCED_MODEL)
            if model == brain.ADVANCED_MODEL:
                self.assertFalse(payload["think"])

    def test_setup_recognizes_advanced_only_installation(self):
        for tag in (brain.ADVANCED_MODEL, "qwen3.8:latest"):
            with patch.object(brain, "_request", return_value={"models": [{"name": tag}]}):
                response = web.app.test_client().get("/api/setup")
            self.assertTrue(response.json["advanced"])
            self.assertFalse(response.json["fast"])


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class RenderDestinationTests(unittest.TestCase):
    def test_real_render_and_reedit_use_custom_folder(self):
        from clipper.pipeline import run_pipeline, rerender_clip
        with tempfile.TemporaryDirectory(prefix="snap-ninja-render-") as tmp:
            root = Path(tmp)
            source = root / "sample.mp4"
            subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-f", "lavfi", "-i",
                "color=c=green:s=160x90:r=10:d=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)], check=True,
                capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            job_dir = root / "jobs" / "test-job"
            export = root / "exports"
            opts = Options(ready_clip=True, output_dir=str(export), make_shorts=False, make_landscape=True,
                music=False, captions=False, title_overlay=False, use_brain=False, use_llm=False,
                tighten_pauses=False, punch_in=False)
            result = run_pipeline(source, job_dir, opts, lambda **kwargs: None)
            out = export / "test-job" / "clips"
            clip = result["clips"][0]
            self.assertTrue((out / clip["files"]["landscape"]).is_file())
            self.assertFalse((job_dir / "clips").exists())
            edited = rerender_clip(source, job_dir, opts, {**clip, "start": 0, "end": 3.5}, 0, 1, lambda message: None)
            self.assertTrue((out / edited["files"]["landscape"]).is_file())
            self.assertTrue((out / edited["files"]["srt"]).is_file())
            self.assertTrue(source.exists())
