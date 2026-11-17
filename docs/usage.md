# Using Snap Ninja

[Back to README](../README.md) · [Detailed reference](reference.md)

## Find highlights

Add a local video by dropping it onto the workspace, choosing a file, or entering a
local path. A supported video link can be used instead. Choose clip lengths, formats,
caption settings and music, then start the job. Automatic clip count returns the
moments that meet the scorer's threshold; a cap limits the maximum, not a guaranteed count.

The app transcribes speech, scores moments, optionally asks local AI to understand
events, and renders clips. Watch the progress and activity log for the actual model
and any fallback. Long videos can take substantial time and temporary disk space.

## Caption finished clips

Choose **Caption clips** and select one or more videos, or enter a folder path. Each
video becomes a job. This mode preserves the cut and disables pause trimming and
punch-in zooms. Music starts disabled. Landscape clips can also get a vertical version.

## Live editor

Choose **Live editor**, paste a currently live stream link, and start recording.
Use the player to move through the available recording, then:

- **Mark start** / `I`: choose the beginning.
- **Mark end** / `O`: choose the end.
- **Cut clip** / `C`: create a clip job for that range.
- The last 30/60/120-second buttons cut a recent range.
- **Jump to live** returns to the live edge; **Stop recording** ends recording.

Keyboard shortcuts do not operate while typing in a field or while a dialog is open.
The browser player loads hls.js from a CDN, so first use requires internet access.

## Chat highlights

Paste a live Twitch or YouTube link. The app records the stream, learns normal chat
activity and cuts around spikes. Chat availability depends on the source stream;
Kick chat is not supported. A chat job can run alongside a manually clipped stream.

## Review and adjust

Preview a rendered clip before publishing. **Adjust** opens the source player,
transcript and cut controls. Change the start/end and title/hook, check the range,
then save to render that clip again. The original video must still be available.
Automatic live-part clips cannot be adjusted this way; use Live editor for manual cuts.

## Output folders

**Change output folder** opens a dialog with a native Browse button and path entry.
Save checks write access and remembers the preference in the current browser.

| Item | Location |
| --- | --- |
| Default finished clips | `jobs/<job id>/clips/` |
| Custom finished clips | `<chosen folder>/<job id>/clips/` |
| Job metadata, working files and recordings | App's `jobs/` folder |
| Uploaded source copies | App's `uploads/` folder |
| Google OAuth client and sign-in | App's private `youtube/` folder |

A job keeps its destination when settings change. Downloads, re-edits, YouTube uploads
and Open clips folder use that same destination. Keep external drives connected while
processing or reviewing a job. Deleting a job retains clips in a custom output folder.

## Local model preferences

Select a model in **Setup guide → Models** or **Advanced → Local AI**. The choice is
remembered for new jobs in the current browser. Automatic uses the existing 7B/14B
selection. Qwen 3.8 is opt-in and can handle transcripts and frames with the same model.
If a selected model is missing, the app can fall back to an installed 7B/14B model and
records that in the activity log. With no usable local model, offline scoring remains.

## YouTube

Follow [YouTube setup](youtube.md). On a finished clip, review the channel, format,
title, description and visibility, then explicitly start the upload. Account connection
alone never uploads media. Google may require you to reconnect when tokens expire.

## Disk space and deletion

**Previous jobs** shows app-managed disk usage and allows deleting a completed job.
Undo is briefly available before app-managed files are removed. Exported clips are
separate and remain in a custom folder. **Delete original video** confirms separately;
user-owned local files go to the Recycle Bin, while app-owned copies are deleted.

## Command line

Run `.venv\Scripts\python cli.py --help` for all options. Examples on Windows:

```powershell
.venv\Scripts\python cli.py "D:\Videos\stream.mp4" --clips 5 --min 20 --max 55
.venv\Scripts\python cli.py "D:\Clips\clip.mp4" --ready
.venv\Scripts\python cli.py "D:\Videos\stream.mp4" --fast --no-vision
.venv\Scripts\python cli.py "D:\Videos\stream.mp4" --out "D:\SnapNinja\my-job"
```

CLI `--out` selects the complete job directory, unlike the web app's clip-only custom
destination. CLI results are written under that job directory's `clips/` folder.
