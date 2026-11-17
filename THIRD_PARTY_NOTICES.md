# Third-party notices

Snap Ninja source code is licensed under MIT; third-party software and models
retain their own licenses. Installing Snap Ninja does not relicense dependencies.

## Bundled face detector

- File: `clipper/models/face_detection_yunet_2023mar.onnx`
- Project: [OpenCV Zoo / YuNet](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet)
- Copyright: 2020 Shiqi Yu
- License: MIT, included in [LICENSE.YuNet](clipper/models/LICENSE.YuNet)
- SHA-256: `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`

## Installed or downloaded separately

- Python dependencies are listed in `requirements.txt` and `requirements-gpu.txt`.
  Their licenses are supplied by their respective packages.
- Source installations use a separately installed FFmpeg. The Windows installer
  downloads Gyan's essentials build directly from its distributor; that build is
  GPLv3. Its archive's license and documentation are retained alongside the tools.
  The media executables are not embedded in the Snap Ninja installer. See
  [the distributor and sources](https://www.gyan.dev/ffmpeg/builds/) and
  [FFmpeg legal information](https://ffmpeg.org/legal.html).
- Desktop bundles include Python and Python packages with their original licenses.
  Build-generated `licenses/python-licenses.json` contains dependency notices and
  license text, and `licenses/payload-manifest.json` records downloaded payloads.
  Node.js is bundled with its upstream `LICENSE` file under `vendor/node`.
- Microsoft WebView2 uses Microsoft's distribution terms. Setup includes Microsoft's
  signed Evergreen bootstrapper and invokes it only if the runtime is missing.
- Whisper, Qwen and Ollama model weights are downloaded separately and are not
  bundled in this repository. Review the specific model's license before use or
  redistribution.
- The live-stream browser player loads the pinned hls.js build from jsDelivr.
  [hls.js](https://github.com/video-dev/hls.js) uses the Apache 2.0 license.
- The Snap Ninja logo is an AI-assisted project asset. No third-party brand
  endorsement is implied. YouTube, Google, Qwen and other names belong to their
  respective owners.

Screenshots use an empty demonstration workspace, without private recordings,
credentials, connected channels or user job history.
