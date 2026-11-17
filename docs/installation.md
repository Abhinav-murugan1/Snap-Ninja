# Installation

[Back to README](../README.md)

## Desktop installer (Windows x64)

A verified installer has not yet been published. Once one is available, download
`SnapNinja-Setup-VERSION-x64.exe` from the project's GitHub Releases page, open it,
and follow Setup. Leave the internet connected while it downloads the video tools
and installs WebView2 if needed. Then choose **Launch Snap Ninja**.

On later launches, open **Snap Ninja** from the Start menu or its desktop shortcut.
No terminal, batch file, separate Python installation or browser page is required.
The installer includes Python, the application dependencies and Node.js. It obtains
FFmpeg/FFprobe directly from their distributor and checks the archive's SHA-256.

Optional Ollama models and Google channel authorization remain separate choices
inside **Setup guide**. The first transcription downloads its Whisper model.
The standard installer uses CPU transcription without requiring CUDA installation;
the existing developer GPU installation remains available below.

The app installs for the current Windows account. Its jobs, uploads, music,
feedback, Google connection, settings, model cache and logs use
`%LOCALAPPDATA%\SnapNinja`. A custom output folder is still supported.
Updates replace program files; uninstalling leaves user data and finished clips
in place. Source installations keep their existing folders and are not moved or
imported automatically.

Maintainers: see [installer build and validation](windows-installer.md).

## Source installation requirements (developers)

- Windows 10/11 for the desktop window and Windows launcher.
- Python 3.10 or later; Python 3.13 was used for local validation.
- FFmpeg and FFprobe, both available on PATH.
- Enough disk space for source copies, working audio, models and finished clips.
- Internet access for installation, first-use model downloads, video links and Google sign-in.
- Optional: NVIDIA GPU and CUDA 12 runtime libraries for faster transcription.
- Optional: Ollama for local video understanding.
- Node.js or Deno for YouTube video downloads. Node is also used for frontend tests.

The browser/CLI portions may work on other systems, but full macOS/Linux workflows
have not been verified. `desktop.py` and the shortcut scripts target Windows.

## Windows source installation

1. Install Python from https://www.python.org/downloads/ and enable **Add Python to PATH**.
2. Download FFmpeg from https://ffmpeg.org/download.html. Extract a Windows build
   and add its `bin` directory to your user PATH. Open a new terminal after the change.
3. Verify the tools:

   ```powershell
   python --version
   ffmpeg -version
   ffprobe -version
   ```

4. Clone the public repository, or choose GitHub's **Code → Download ZIP** and extract it.
5. Double-click **`run.bat`** from the extracted project folder. Wait for dependency
   installation to finish and keep the console running while using the browser app.
6. The launcher opens http://127.0.0.1:7860. If the browser opens before the server
   is ready, refresh after setup finishes. Follow **Setup guide**.

For a source checkout, `run.bat` performs the initial Python dependency setup
and starts the app; Python and FFmpeg must
already be installed. Extract the ZIP before running it and keep the project folder
in a permanent location if you plan to create shortcuts.

## Which file should I open?

| Goal | What to open |
| --- | --- |
| First-time setup | `run.bat` |
| Start the browser version again | `run.bat` |
| Create Windows shortcuts after the initial setup | `make_shortcuts.bat` |
| Launch the desktop window | The **Snap Ninja** desktop or Start menu shortcut |

To switch from the browser launcher to the desktop app, finish active jobs, then
press **Ctrl+C** in the launcher's terminal. Run `make_shortcuts.bat` and open the
new shortcut. Later, you can open that shortcut directly without running `run.bat`.

The first run installs the base Python dependencies inside `.venv`. It does not
install Ollama or download Qwen models. The first speech-enabled job downloads a
Whisper model, which can take time. GPU libraries are an optional separate step.

## Manual installation

From the project directory in PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python app.py
```

If a first installation stops, rerun the pip installation command above. The
launcher only installs automatically when it creates the virtual environment.

## Desktop shortcuts

After installing dependencies, run `make_shortcuts.bat`. This creates **Snap Ninja**
shortcuts on your desktop and Start menu. The app opens in its own window through
Microsoft Edge WebView2. Install WebView2 from Microsoft's official site if Windows
reports that the runtime is missing.

Run `make_shortcuts.bat` again after upgrading from Clipper. It recreates the Snap Ninja
shortcuts with the green ninja icon and backs up only this project's matching old
Clipper shortcuts. Restart the app afterward. If you pinned an old Clipper shortcut
to the taskbar, unpin it and pin the new Snap Ninja shortcut. The existing project
folder and job paths stay in place.

Closing the desktop window stops unfinished work after its confirmation prompt.
Finished clips are retained. Desktop output downloads use a Save As dialog.

## Optional NVIDIA acceleration

Use a current NVIDIA driver, then install the optional CUDA 12 libraries:

```powershell
.venv\Scripts\python -m pip install -r requirements-gpu.txt
```

Restart Snap Ninja and inspect the setup/Advanced status. CPU mode remains available
and uses a smaller speech model automatically. Other applications using the GPU can
reduce available memory and slow processing.

## Optional Ollama and models

Install Ollama from https://ollama.com/download, open it, and leave it running.
In a new terminal, download the starting model:

```text
ollama pull qwen2.5:7b
```

The in-app Models step lists additional choices and copyable commands. Download
only the models you want. Qwen 3.8 is an explicit high-memory option; installing it
does not change Automatic mode. If Ollama is not running, the app can still use its
offline scoring method.

## Optional Claude API

Copy `.env.example` to `.env` and set your own `ANTHROPIC_API_KEY`. This enables paid
API processing and sends transcript content to the configured service. Keep `.env`
private. Do not commit an API key. Local Ollama use does not need an Anthropic key.

## Updating

Finish active jobs, close the app, and back up your media and important job folders.
Download the updated source or pull the repository, then update base dependencies:

```powershell
.venv\Scripts\python -m pip install -r requirements.txt
```

Keep `jobs/`, `uploads/`, `youtube/`, `.env` and custom output folders when updating.
Do not replace them with another user's data. Reopen the app and run a short test clip.

## Troubleshooting

| Problem | Next step |
| --- | --- |
| `python` is not found | Repair Python installation/PATH and open a new terminal |
| FFmpeg check fails | Ensure both executable files are in the PATH visible to Snap Ninja; restart it |
| Dependencies failed partway | Run the manual pip installation command again |
| Local AI not connected | Open Ollama, run `ollama list`, then use Check again in setup |
| GPU memory error | Select Fast or a smaller speech model; close other GPU workloads |
| YouTube video link cannot download | Install Node.js or Deno, update yt-dlp, and check that the video is accessible |
| Google sign-in fails | Follow the [YouTube guide](youtube.md), including test users and Desktop app client type |
| Output folder is rejected | Choose an absolute, writable path; reconnect external/network drives |
| Port 7860 is occupied | Close another app instance; advanced browser users can set `PORT` before starting `app.py` |

Do not expose the server to the internet to fix a connection problem. It has local
file access and is designed for use on the same computer.
