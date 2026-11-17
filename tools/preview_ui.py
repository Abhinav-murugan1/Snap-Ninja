"""Preview the real UI against a disposable store, without loading existing jobs.

Run from the project root: python tools/preview_ui.py
"""
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

with tempfile.TemporaryDirectory(prefix="snap-ninja-preview-") as folder:
    with patch("clipper.jobs.JobStore") as factory, patch("clipper.env.load_env"):
        import app
    factory.return_value.list.return_value = []
    factory.return_value.get.return_value = None
    app.UPLOADS = Path(folder)
    from flask import jsonify, request, Response
    from clipper import brain

    # Documentation captures use actual UI components and no user jobs or sign-ins.
    app.app.view_functions["status"] = lambda: jsonify(cuda=False, claude=False, brain=False,
        brain_message="Local AI is optional. Open Setup guide to configure Ollama.",
        smart=False, fast=False, vision=False, votes={"up": 0, "down": 0})
    app.app.view_functions["youtube_status"] = lambda: jsonify(connected=False, has_client=False,
        connecting=False, channel=None, error="")
    app.app.view_functions["setup_status"] = lambda: jsonify(python="3.13", ffmpeg=True, ffprobe=True,
        runtime=True, ollama=False, fast=False, smart=False, vision=False, advanced=False)

    def preview_index():
        screen = request.args.get("screen", "workspace")
        actions = {
            "workspace": "",
            "models": "document.querySelector('#setup-open').click(); document.querySelector('[data-step=2]').click();",
            "youtube": "document.querySelector('#setup-open').click(); document.querySelector('[data-step=3]').click(); document.querySelector('#setup [data-panel=3] details').open = true;",
        }
        script = ("<script>localStorage.setItem('snap-ninja:setup:v1','done');"
                  "document.addEventListener('DOMContentLoaded',()=>{" + actions.get(screen, "") + "});</script>")
        html = (app.STATIC / "index.html").read_text(encoding="utf-8")
        return Response(html.replace("<body>", "<body>" + script), mimetype="text/html")

    app.app.view_functions["index"] = preview_index
    print("Isolated Snap Ninja UI preview: http://127.0.0.1:7861", flush=True)
    app.app.run(host="127.0.0.1", port=7861, threaded=True, debug=False)
