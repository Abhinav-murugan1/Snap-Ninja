"""Local API regression tests. Never instantiate the real job store or workers."""
from __future__ import annotations

import importlib
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clipper.options import Options

# Importing the production app normally loads and updates saved jobs. Isolate it.
with patch("clipper.jobs.JobStore") as factory:
    web = importlib.import_module("app")
    fake_store = factory.return_value


class LocalAppTests(unittest.TestCase):
    def setUp(self):
        self.client = web.app.test_client()
        self.headers = {"X-Snap-Ninja": "1"}
        fake_store.reset_mock()

    def test_workspace_and_assets(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Snap Ninja", response.data)
        for name in ("app.css", "app.js", "setup.js", "output.js", "youtube-guide.js", "snap-ninja.png", "snap-ninja.ico"):
            with self.subTest(name=name):
                with self.client.get("/static/" + name) as asset:
                    self.assertEqual(asset.status_code, 200)
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        response.close()

    def test_dns_rebinding_host_is_denied(self):
        response = self.client.get("/api/jobs", base_url="http://attacker.example:7860")
        self.assertEqual(response.status_code, 403)
        fake_store.list.assert_not_called()

    def test_cross_origin_read_and_write_are_denied(self):
        for method in ("GET", "POST"):
            with self.subTest(method=method):
                response = self.client.open("/api/jobs", method=method,
                    headers={**self.headers, "Origin": "https://attacker.example"}, json={})
                self.assertEqual(response.status_code, 403)
        fake_store.create.assert_not_called()
        fake_store.list.assert_not_called()

    def test_cross_site_browser_request_without_origin_is_denied(self):
        self.assertEqual(self.client.get("/api/jobs", headers={"Sec-Fetch-Site": "cross-site"}).status_code, 403)

    def test_mutations_need_app_header(self):
        for method, path in (("POST", "/api/jobs"), ("DELETE", "/api/jobs/test"),
                             ("POST", "/api/youtube/disconnect"), ("POST", "/api/upload")):
            with self.subTest(path=path):
                self.assertEqual(self.client.open(path, method=method, json={}).status_code, 403)

    def test_json_shape_validation(self):
        for body in ([1], "text", None, 123):
            with self.subTest(body=body):
                response = self.client.post("/api/jobs", data=__import__("json").dumps(body),
                    content_type="application/json", headers=self.headers)
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.post("/api/jobs", json={"options": [1]}, headers=self.headers).status_code, 400)

    def test_same_origin_request_and_existing_job_options(self):
        fake_store.create.return_value = {"id": "test-job", "status": "queued"}
        response = self.client.post("/api/jobs", base_url="http://127.0.0.1:7860",
            headers={**self.headers, "Origin": "http://127.0.0.1:7860"},
            json={"url": "https://www.youtube.com/watch?v=example", "options": {"num_clips": 3}})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(fake_store.create.call_args.args[2].num_clips, 3)

    def test_upload_is_streamed_to_isolated_folder(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(web, "UPLOADS", Path(folder)):
            response = self.client.post("/api/upload?name=sample.mp4", data=b"test-video-bytes", headers=self.headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual((Path(folder) / response.json["upload_id"]).read_bytes(), b"test-video-bytes")
            response = self.client.post("/api/upload?name=empty.mp4", data=b"", headers=self.headers)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(len(list(Path(folder).iterdir())), 1)
            self.assertEqual(self.client.post("/api/upload?name=bad.exe", data=b"x", headers=self.headers).status_code, 400)

    def test_setup_with_installed_models(self):
        with patch("clipper.brain._request", return_value={"models": [{"name": "qwen2.5:7b"}]}), \
             patch("app.shutil.which", return_value="tool"):
            response = self.client.get("/api/setup")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["fast"])
        self.assertFalse(response.json["smart"])
        self.assertTrue(response.json["ffprobe"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_setup_when_ollama_and_tools_are_missing(self):
        from clipper.brain import BrainError
        with patch("clipper.brain._request", side_effect=BrainError("offline")), \
             patch("app.shutil.which", return_value=None):
            response = self.client.get("/api/setup")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json["ollama"])
        self.assertFalse(response.json["ffmpeg"])

    def test_clip_edit_rejects_nonfinite_times(self):
        for value in ("nan", "inf", "-inf"):
            response = self.client.post("/api/jobs/test/clips/0/edit", headers=self.headers,
                                        json={"start": 0, "end": value})
            self.assertEqual(response.status_code, 400)
        fake_store.edit_clip.assert_not_called()

    def test_path_traversal_does_not_serve_private_files(self):
        self.assertEqual(self.client.get("/static/../.env").status_code, 404)
        self.assertEqual(self.client.get("/files/test/../../../.env").status_code, 404)


class OptionsTests(unittest.TestCase):
    def test_defaults_stay_compatible(self):
        self.assertEqual(Options.from_dict({}), Options())

    def test_finite_limits_and_order(self):
        opts = Options.from_dict({"min_len": 999999, "max_len": -5, "live_max_hours": 999,
                                  "live_part_minutes": -1, "live_clips_per_part": 100})
        self.assertEqual((opts.min_len, opts.max_len), (170, 175))
        self.assertEqual((opts.live_max_hours, opts.live_part_minutes, opts.live_clips_per_part), (24, 2, 10))

    def test_nonfinite_and_overflow_values_use_defaults(self):
        for value in ("nan", "inf", "-inf", "1e10000"):
            opts = Options.from_dict({"min_len": value, "num_clips": value, "music_volume": value})
            self.assertTrue(math.isfinite(opts.min_len))
            self.assertEqual(opts, Options())


if __name__ == "__main__":
    unittest.main()
