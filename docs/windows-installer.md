# Building the Windows installer

This guide is for maintainers. End users install the resulting Setup executable;
they do not perform these build steps. No installer binary has yet been validated
or published from the current development environment.

## Build on GitHub

1. Push the installer sources to the repository.
2. Open **Actions → Windows installer → Run workflow**.
3. Enter a three-part version such as `0.1.0` and start the workflow.
4. When every step succeeds, download **SnapNinja-Windows-Installer** from that run.

The workflow builds in a clean Windows x64 environment, runs regression tests,
freezes the desktop entry point with PyInstaller, verifies native imports and an
actual captioned video render, compiles Inno Setup, installs into a temporary folder,
and repeats the checks against the installed executable. Failed checks block the
artifact upload. Build artifacts expire according to GitHub's artifact retention.

The workflow does not publish a GitHub Release automatically. Before attaching the
Setup executable and checksum to a release, test the installer interactively on a
clean Windows computer without Python, FFmpeg or Node.js: launch the app, choose an
output folder, transcribe a short video, render and re-edit a captioned clip, then
update and uninstall. Verify that saved jobs and custom output files survive.
Google sign-in and Ollama require their own optional checks.

## Local maintainer build

Use Windows x64, Python 3.13 x64 and Inno Setup 6. Install build dependencies in a
fresh virtual environment, outside the populated app's environment:

```powershell
python -m venv .build-venv
.build-venv\Scripts\python -m pip install -r requirements.txt -r packaging/requirements-build.txt
.build-venv\Scripts\python tools/build_windows.py --version 0.1.0
```

The result is `dist/installer/SnapNinja-Setup-0.1.0-x64.exe`, with a SHA-256 file.
The helper requires internet access, obtains Node.js from nodejs.org and verifies
its official checksum, obtains FFmpeg metadata and the verification build from
gyan.dev, and verifies Microsoft's signature on the WebView2 bootstrapper. The
selected URLs, versions and hashes are recorded in `packaging/vendor/payload-manifest.json`.
The installer uses a versioned FFmpeg URL and the recorded hash instead of following
a moving latest-release download on users' computers. If the distributor removes
that version, installation fails with a retry message; maintainers must rebuild
against an available release. No unverified replacement is executed.

Builds currently resolve compatible Python dependency versions at build time;
they are not bit-for-bit reproducible. Build-generated notices record installed
dependencies. CUDA wheels are excluded from the standard CPU-compatible bundle;
Ollama can still use a compatible GPU independently. The setup executable is
currently unsigned. Code-sign a verified release with the publisher's own
certificate before broad distribution if available; do not tell users to disable
Windows security controls.

## Installation behavior

- Installs for the current account under `%LOCALAPPDATA%\Programs\Snap Ninja`.
- Creates Start menu and optional desktop shortcuts with the Snap Ninja icon.
- Includes Python, native Python dependencies and a private Node.js executable.
- Downloads FFmpeg/FFprobe during Setup and retains the distributor's notices.
- Installs Microsoft's WebView2 runtime when missing.
- Opens the existing workspace in its desktop window, without an external browser.
- Keeps writable app data in `%LOCALAPPDATA%\SnapNinja`; does not migrate source jobs.
- Does not delete saved jobs, credentials, models or custom exports on uninstall.
- Leaves the optional Ollama/model and YouTube account choices to the in-app guide.

Python, FFmpeg and Node.js are private to the app; Setup does not change the user's
global PATH. Installation requires an internet connection. The runtime downloads
can be blocked by corporate policies; Setup reports failure rather than claiming
the app is ready. Existing source checkouts continue using their original data.

Implementation references:
[PyInstaller runtime paths](https://pyinstaller.org/en/stable/runtime-information.html),
[pywebview freezing](https://pywebview.flowrl.com/guide/freezing),
[Microsoft WebView2 distribution](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution),
and [Inno Setup per-user installation](https://jrsoftware.org/inno-setup/documentation/topic_setup_privilegesrequired.htm).
