# Snap Ninja v0.1.0

Initial public source release, created by Abhinav Murugan.

- Local browser workspace and Windows desktop window with the green ninja icon.
- Highlight selection, finished-clip captioning, manual live editor and chat highlights.
- Vertical/landscape exports, animated captions, music and clip adjustment.
- Custom output destinations with per-job folders.
- In-app FFmpeg/Ollama setup, opt-in Qwen 3.8 selection and Google Cloud/YouTube guide.
- Installation, usage and troubleshooting documentation with real UI screenshots.
- MIT source license, bundled YuNet notice, isolated tests and GitHub Actions configuration.

This release contains source code, not a standalone Windows installer. Install Python
and FFmpeg, then follow `docs/installation.md`. GPU libraries and Ollama are optional.

Local checks passed: 27 Python tests (including real FFmpeg render/re-edit) and 5
JavaScript connection-state tests. Google sign-in/uploads and Qwen 3.8 inference were
not exercised during validation. Other operating systems and hosted CI remain to be
verified. Keep the service local; it is not designed for public hosting.
