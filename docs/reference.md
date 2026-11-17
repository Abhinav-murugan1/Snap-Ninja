# Snap Ninja

Upload a long video (stream VOD, podcast, vlog, lecture). Snap Ninja finds the most interesting moments,
cuts them out, and edits each one into:

- a **9:16 short** (1080×1920) that keeps the person who is talking in the centre (details under **Framing shorts** below). When nobody is on screen, as in gameplay, it zooms into the centre of the frame. You can also fit the whole frame over a blurred fill.
- a **landscape clip** in the original shape

Both versions get word-by-word animated captions, background music that ducks under speech, and loudness
normalized to -14 LUFS (the level Shorts, TikTok and Reels expect).

## Run it

Needs Python 3.10+ and ffmpeg on PATH.

```bat
run.bat
```

The first run creates `.venv` and installs dependencies. Then open http://127.0.0.1:7860.

### As a desktop app

Run `make_shortcuts.bat` once (after `run.bat` has installed everything). It puts **Snap Ninja** in the Start menu and
on the desktop. Snap Ninja then opens as its own program, the same as any other app:

- its own window and taskbar icon, with no browser and no console window
- every feature works the same as in the browser, because the same server runs inside the app
- **Download short** opens a normal *Save as* dialog; YouTube links and Google sign-in open in your browser
- **Open clips folder** on a job shows its finished clips in File Explorer
- opening Snap Ninja again while it's open brings the open window to the front instead of starting a second copy
- closing the window while something is still working (a job, a live recording, a clip being re-edited) asks
  first. Finished clips are always kept.
- when Snap Ninja closes, its helper programs (ffmpeg) stop with it, so a live recording never keeps running
  invisibly. `run.bat` does this too.

What would normally be printed goes to `logs/clipper.log`. The window uses Microsoft Edge WebView2, which comes with
Windows 11. To remove the app, delete the two Snap Ninja shortcuts.

You can drop a file, or paste a **video link** (YouTube, Kick, Twitch VODs and the other sites
[yt-dlp](https://github.com/yt-dlp/yt-dlp) supports) or a file path. Links download in up to 1080p. YouTube needs
Node.js or Deno installed. Full Kick or Twitch VODs can be very large (about 45 GB for 10 hours at 1080p), so check
your free disk space. Only clip videos you have the right to reuse.

**Live streams:** paste the link of a stream that is live right now (for example `https://www.youtube.com/@SkyNews/live`,
`https://kick.com/<channel>`, `https://www.twitch.tv/<channel>`). Snap Ninja records it in parts (every 10 minutes by
default). When a part finishes, Snap Ninja clips it while the next part records, so new clips keep appearing on the page.
Each part is analyzed with the last 90 seconds of the part before it, so a moment that crosses a part boundary is not
cut in half, and no moment is clipped twice. Recording stops when you press **Stop recording**, when the stream ends, or
after 4 hours. Change the part length, clips per part and time limit under **Advanced**. Recording uses about 2.5 GB
per hour at 1080p.

**Cut clips from a live stream by hand:** choose **Live stream: cut clips by hand** at the top of the page, paste the
link of a stream that is live right now (YouTube, Twitch, Kick, or most other sites yt-dlp knows), and press
**Start recording**. The stream plays right on the page while Snap Ninja records it, and you can scrub back through
everything recorded so far:

- **Mark start** (key `I`) and **Mark end** (key `O`) at the player's position, then **Cut clip** (key `C`).
- Just saw something? **Clip the last 30 s**, **Last 1 min** or **Last 2 min** cut the stretch that ends where the
  player is now.
- **Jump to live** goes back to the live edge.

**Chat hype clipper** (its own tab, **Chat hype clipper: clips itself**): paste a live Twitch or YouTube link and it
works on its own. It records the stream, reads its chat live, and clips every moment chat explodes. It runs at the
same time as anything else, so you can cut one stream by hand while the hype clipper watches another. Its page
shows a graph of chat messages per 5 seconds along the recording, the explosions it found, and its clips. How it
decides:

- It learns how busy this chat normally is (the median over the last 3 minutes, after a first minute of chat).
- A spike is when messages per 5 s jump to at least 2.5× normal and well above the usual ups and downs (and at
  least 8, so a tiny chat's two "lol"s don't count). It follows the spike until chat calms down.
- The clip runs from **20 s before chat started rising** (the moment chat is reacting to) to just after the spike,
  90 s at most. It's cut as soon as the recording covers it, then gets captions, a title and the upload button like
  any clip. The note on each chat clip says how hard chat went and the words it spammed ("KEKW", "W"…).

Works with Twitch and YouTube chat, read without an account. Kick isn't supported (its chat is only open to
registered developer apps), so the hype clipper refuses a Kick link; use cutting by hand for Kick. If a YouTube
stream has its chat switched off, the hype clipper says so and stops. Chat times are put on this computer's own clock, so a wrong system clock doesn't
shift the clips.

Cuts are frame-exact. Each one becomes its own job under **Your clips**: captions, an AI title and hook, and the
**Upload to YouTube** button, as for finished clips. You can keep cutting while it records, and after the recording
has stopped (with **Stop recording**, when the stream ends, or at the time limit under **Advanced**). The recording
stays on disk until you use **Delete original video**. The player needs an internet connection the first time it
loads (it uses hls.js).

Command line instead of the web page:

```bat
.venv\Scripts\python cli.py "D:\videos\long stream.mp4" --min 20 --max 55
.venv\Scripts\python cli.py "https://www.youtube.com/watch?v=..." --clips 5
```

Run `cli.py --help` to see every option.

**Adjust a clip:** not happy with where Snap Ninja cut? Press **Adjust** on the clip. The editor plays the original
video with the clip marked on a timeline. Move the start or end with **−5 s / −1 s / +1 s / +5 s**, set it to the
player's position, or click the transcript line it should start or end on. Change the title and the hook if you
like, press **Play the clip** to check, then **Save and re-edit**. Only that clip is rendered again, with the same
captions, framing, music and pacing as before. The original video must still be there (not deleted). Clips from an
automatic live recording can't be adjusted; use **Live stream: cut clips by hand** for those streams instead.

**Finished clips:** already cut your clips yourself? Choose **Finished clips: add captions and titles** at the top
of the page, then drop one or more clips (or paste a folder path: every video in it becomes its own job). Each clip
is kept exactly as you cut it, with no trimming, zooms or music unless you switch music on. Snap Ninja transcribes it,
burns in the word-by-word captions, and the local AI watches what happens to write a title, an on-screen hook and
a summary. A landscape clip also gets a 9:16 short; a vertical clip stays as it is. Then use **Upload to YouTube**
as usual: the title, description and hashtags are filled in for you. Command line:
`.venv\Scripts\python cli.py "D:\Clips\my clip.mp4" --ready`

**Tabs keep your place:** the four modes at the top (long video, finished clips, live by hand, chat hype clipper)
are tabs. Each one remembers what it was showing, the job you had open or the link and files you'd started to
enter, so you can jump to another tab and come back to the same spot. A live recording also remembers where you were
in the player and your start/end marks. Opening a job switches to the tab it belongs to, and Snap Ninja reopens where
you left off after a restart.

**How many clips:** you don't pick a number. Snap Ninja makes a clip of every moment that is good enough to post, so
a long video full of action gives many clips and a slow one gives a few. To cap it, choose **At most …** under
**How many**, or use `--clips 5`. Snap Ninja never pads the list with moments it doesn't understand: a padded clip
doesn't know where its moment ends, so it gets cut off.

## How it picks moments

1. **Transcribe**: faster-whisper with word timestamps. All audio tracks are mixed, so a separate mic track (ShadowPlay, OBS) is
   heard. Lines Whisper invents over game sound or silence are filtered out. It runs on an NVIDIA GPU when the CUDA libraries in
   `requirements-gpu.txt` are installed (large-v3-turbo), or on the CPU otherwise (small).
2. **Measure**: a hype detector listens to the audio every half second. It looks for:
   - sudden loudness jumps compared with the last 30 seconds
   - shouting and screaming (energy in the 1–4 kHz band)
   - crowd reactions such as laughter, cheering and applause
   - action bursts such as shots, hits and explosions
   - hype words and repeated shouts in the transcript ("let's go let's go")

   Peaks become hype moments. Each gets a clip with set-up before the peak and the reaction after it. Under
   **Clips → Look for**, choose **Hype and loud moments** to put these first, or use `cli.py --hype`. Excitement
   words, speech density and visual change between keyframes count too.
3. **Understand** (free, local): when the [Ollama](https://ollama.com) app is running, a local AI on your GPU
   works through the video the way an editor would. Models:
   - `qwen2.5:7b` is **Fast** (4.7 GB). `qwen2.5:14b` is **Smart** (9 GB): smarter, but on GPUs under 12 GB it
     doesn't fit and runs 3–4× slower. **Automatic** (the default) picks Smart on 12 GB+ GPUs and Fast otherwise.
   - `qwen2.5vl:7b` gives it **eyes** (6 GB).
   - Get them with `ollama pull <name>`. Snap Ninja uses whichever are installed.

   The steps:
   1. **Situation:** the type of video (gaming, podcast, IRL stream, talk, sports watch-along, news, vlog), what's
      going on, who is in it, and the game or topic.
   2. **Event map:** it works out what each commotion in the video is about, and where it starts and ends. An
      event is one commotion: a challenge with all its attempts and the result, a round, a story, an argument, a
      play and its reaction. Small steps keep even the 7B model accurate:
      - **Sections:** it reads the transcript in overlapping parts, twice with the parts shifted, and splits it
        wherever something new starts (a new challenge, topic or opponent, a sponsor read). It refers to
        numbered lines, not guessed seconds.
      - **Exact boundaries:** each boundary is checked again on the lines around it. The result of an event, the
        reactions and the thanks stay with that event.
      - **Each event on its own:** it reads every event alone and says what happens and how it ends, which line
        is the climax, and where the decisive attempt is set up. If two things got mixed into one event, it
        splits them and reads both again. If an event continues the one before, they become one event.
      - **Sponsor reads:** ads and plugs are found line by line and never end up in a clip.

      - **Moments inside long events:** a long event (a whole challenge with many attempts, a long story) is
        read once more for every moment in it that works on its own: each attempt with its result, each
        elimination, each funny exchange. Each one ends after its payoff and the reaction to it, never before.

      **An event ends where the next one begins**, so a clip can't stop in the middle of a commotion. Events
      that fit in a short become clips whole, from set-up to outcome and reaction.
   3. **Eyes:** a vision model looks at 4 frames of each candidate. It reports what's on screen (kills, kill feed,
      ACE/CLUTCH/VICTORY banners, scores, faces and reactions) and how visually exciting it is.
   4. **Editor review:** a second, stricter read of each clip on its own. It moves the start or end if the set-up or
      payoff is cut, rewrites the title and hook, and drops clips a stranger wouldn't get.
   5. **Your taste:** rate clips with 👍 **Good clip** / 👎 **Not for me**. The ratings are saved in
      `feedback/votes.json`. Later jobs show the AI examples of what you liked and disliked, and nudge scores toward
      the kinds of clips you like.

   **Every clip contains its climax.** Each moment the AI picks must name its climax: the result of the attempt, the punchline, the answer or the end of the topic. The end has to come after that and after the reaction to it. The editor review sees 45 s after each clip and moves the end if the outcome is there. Then a final **ending check** reads the clip's last lines and what comes after the cut, and asks: does a viewer already know how it turns out? If the reveal, the elimination or the result only comes after the cut, the clip is extended to it and the reaction (up to 45 s), but never into the next challenge or an ad. The AI is asked twice (from the lines alone, and knowing what the clip is about), and a model-free rule backs it up: if a line right after the cut gives a result ("you've been eliminated", "your team moves on", "the winner is…", "I pick you") and nothing new has started yet, the clip runs to it. A clip also never ends mid-sentence. A clip also extends when a cheer, laugh or burst of excitement follows right after its end. A clip may run up to 1.5× your maximum length (180 s at most) when the climax needs it.

   Clip length follows the type (gameplay about 18–40 s, challenge videos 30–75 s, podcasts and talks 30–60 s).

   **Fallback:** without Ollama, the offline scorer below is used.

   **Settings:** under **Advanced**: Automatic, Smart or Fast; frames on or off; review on or off. On the command
   line: `--smart`, `--fast`, `--no-vision`, `--no-review`, `--no-brain`. If the vision model gets stuck (for example
   when another program is using the GPU heavily), Snap Ninja stops looking at frames for that job and carries on.
4. **Choose**:
   - By default this is free and offline (see below). Optional and paid: put an API key in `.env` and
     Claude Opus 5.5 reads the timestamped
     transcript and the loudness spikes. It picks self-contained moments with a hook and a payoff, then
     writes a title and an on-screen hook for each. The offline score is blended in.
   - Without Claude, the offline scorer ranks every sentence-aligned window and picks the best
     non-overlapping ones.
5. **Edit**: ffmpeg cuts each clip on word boundaries, frames it, burns in the captions, mixes in the music and
   encodes it with NVENC when available (libx264 otherwise). The edit follows short-form retention practice:
   - **The first seconds:** the clip opens on something that grabs attention. The hook text is on screen from the
     first frame, with no fade-in. Hooks open a question the clip answers, for example "1 HP, 3 enemies left".
   - **No dead air:** pauses with no words and nothing audible shrink to about 0.25 s. Laughs, cheers and game
     action are never cut. The log says how much was removed from each clip.
   - **Pattern interrupts:** shorts get a hard punch-in zoom about every 4 s at sentence starts, plus a stronger
     punch on hype peaks.
   - **Captions:**
     - Words appear one by one as they're spoken, with a pop and the spoken word highlighted.
     - They sit in the 9:16 safe band, about 66% down and clear of the platform buttons.
     - Each clip also gets a plain `.srt` subtitle file.
   - **Loudness:** -14 LUFS, with peaks at -2 dBTP before encoding, so they stay under -1 after AAC.
   - **YouTube:** titles stay under 60 characters with the game or topic first. The description opens with a
     one-line summary of the clip, and hashtags match the kind of clip (#clutch, #storytime, #funny…).

   Turn the pacing features off on the command line with `--no-tighten` and `--no-punch`, or in `clipper/options.py`.

## Framing shorts

For each short, Snap Ninja looks at 4 frames a second and works out who to keep in the 9:16 frame:

- **People:** faces are found with OpenCV's YuNet detector (`clipper/models/`), which catches small, turned and
  side-on faces. Each person is tracked across frames. Faces that never move, like avatars in a score overlay, are
  ignored.
- **Who's talking:** while someone is speaking, the face whose mouth moves most is the speaker. The camera doesn't
  flick between people on tiny differences. Only main people count: tiny faces in a background crowd are never
  treated as the speaker.
- **Composition:** if the speaker and the people next to them fit in the frame, the whole group is framed.
  Otherwise the speaker is centred with their whole face inside.
- **Scene cuts:** at a cut the camera re-frames immediately. Inside a shot it only moves when the target really
  moves, so it doesn't jitter.
- **No face:** it follows the person's body (for example someone bent over or facing away), then the movement in
  the picture (a skater, a runner), then the centre.

## Upload to YouTube

Each finished clip has an **Upload to YouTube** button. It opens a review box with the title, description,
hashtags, tags and visibility already filled in from what is said in the clip. If the clip came from a link, the
description also credits the original video. Change anything you like, then press **Upload to YouTube**. Progress
and the link appear on the clip.

### One-time setup

The app includes the full walkthrough in **Setup guide → Start editing → Set up
YouTube uploads (optional)**. You can import your client file and sign in there
before creating a clip. The same instructions appear under **First time? Set up
YouTube uploads** in the upload dialog; they expand automatically when no client
file has been added.

1. Create a project in https://console.cloud.google.com and keep it selected.
2. In APIs & Services → Library, enable YouTube Data API v3.
3. Configure Google Auth platform → Branding, Audience and Data Access. For a
   personal account, use External / Testing and add the channel owner's Google
   account as a test user. Declare the `youtube.upload` and `youtube.readonly`
   scopes shown in the in-app guide.
4. Under Google Auth platform → Clients, create a **Desktop app** OAuth client
   and download its JSON file. An API key cannot authorize uploads.
5. Add the JSON file in Snap Ninja and choose **Sign in with Google**. Review
   Google's permissions in the browser and select the channel you own.
6. On a finished clip, confirm the channel, title and visibility before uploading.
   Connecting an account never uploads a video on its own.

The app saves the client file as `youtube/client_secret.json` and the sign-in as
`youtube/token.json`. Keep these files private. The `youtube/` folder is ignored
by git, but do not include it in a manually created source archive either.

### Limits worth knowing

- External projects in Testing normally require signing in again after seven
  days for these scopes. Production use has separate Google verification rules.
- Project quotas and channel upload limits can differ. Check the API's Quotas
  page for your current allowance and reset time.
- Review the returned video's visibility in YouTube Studio. Account settings can
  affect visibility; the app displays the actual status returned by YouTube.

Official references:
https://developers.google.com/youtube/data/guides/authentication
https://developers.google.com/workspace/guides/configure-oauth-consent
https://developers.google.com/youtube/data/reference/rest/v3/videos/insert

## Freeing disk space

A finished job has a **Delete original video** link under its title, showing how much space it frees. It asks
before doing anything:

| Original video | What happens |
| --- | --- |
| Uploaded through the page, or downloaded from a link | Snap Ninja's copy is deleted |
| Recorded from a live stream | The recorded parts are deleted |
| A file on your computer that you pointed Snap Ninja at | The file goes to the Recycle Bin, so you can restore it |

The job's working audio copy is removed too. Clips, captions, the transcript and the YouTube details stay.

**Delete a whole job:** open **Previous jobs** (top right) and press the trash icon next to it. It's gone from the
list at once, and **Undo** at the bottom of the page brings it back for a few seconds. After a minute the job's
folder (clips, recording, Snap Ninja's own copy of the video) is removed for good. A video file on your computer that
you pointed Snap Ninja at is never touched. Jobs that are still working can't be deleted until they finish or you stop
them.

## Music

Put your own tracks (mp3, wav, m4a, ogg, flac) in `music/`. Each clip gets a random track, or you can pick one or
upload one in the web page. With an empty folder, Snap Ninja generates a built-in lo-fi loop. Use only music you
have the rights to publish.

## Output

Use **Change output folder** above Output settings to choose where new jobs save
their clips. **Browse** opens the system folder picker; you can also type an
absolute path. **Save folder** checks write access and remembers the selection in
this browser. **Use app folder** restores the default.

Each job uses its destination from the time it was created. Changing the setting
does not move existing jobs or interrupt a recording. Original videos, recordings,
transcripts and working files stay in the app's `jobs/` and `uploads/` folders.

By default, clips are saved in `jobs/<job id>/clips/`. A custom destination uses
`<chosen folder>/<job id>/clips/`. Previews, downloads, re-edits, YouTube uploads
and **Open clips folder** use that job's destination. Clips in a custom destination
are **kept** when a job is deleted; remove those files yourself when no longer
needed. The Previous jobs disk-space total measures app-managed data and excludes
those external clips.

Output files:

- `clipNN_short.mp4` and `clipNN_landscape.mp4`: the edited clips
- `clipNN_*.ass`: the caption files, editable in Aegisub if you want to tweak wording
- `clipNN.json`: the clip's time range, title, hook, reason and score

### Qwen 3.8 for high-memory computers

Qwen 3.8 is optional. Update Ollama, then run:

```text
ollama pull qwen3.8:27b
```

Choose **Qwen 3.8 Advanced (27B)** in the setup guide, or select it under
**Advanced → Local AI**. The preference is remembered for new jobs in this browser.
It supports transcript analysis and video-frame understanding using the same
model, without a separate Qwen 2.5 VL download. Enable frame understanding in
Advanced to use it. Finished-clip titles continue to use transcript analysis.

The default quantized download is approximately 18 GB. Plan for at least 24 GB
VRAM as a starting point, plus enough RAM and disk space; context size, frame
inputs, quantization and other applications affect the actual fit. This is sizing
guidance, not a guarantee. Ollama may offload to RAM on smaller GPUs and run slowly.
Snap Ninja keeps its requests at their existing context sizes and disables thinking
for schema-constrained editing responses.

**Automatic** retains the existing Qwen 2.5 selection; it never opts into the 27B
model. If an explicitly selected model is missing, Snap Ninja uses an installed
7B or 14B model when possible and records the fallback in the activity log.
No setup action downloads or runs a model automatically.

Official model details: https://ollama.com/library/qwen3.8

## Layout

| File | Job |
| --- | --- |
| `clipper/transcribe.py` | Whisper transcription, GPU with CPU fallback, chunked for very long audio |
| `clipper/analyze.py` | Highlight detection (offline scorer and Claude) and boundary snapping |
| `clipper/framing.py` | 9:16 framing: face tracking with smoothed camera cuts, or blur fill |
| `clipper/captions.py` | Word-by-word highlighted ASS captions and the hook title |
| `clipper/music.py` | Track selection and the generated lo-fi loop |
| `clipper/render.py` | ffmpeg graph: cut, frame, caption, duck music, loudnorm, encode |
| `clipper/pipeline.py` | Runs the stages and reports progress |
| `app.py`, `static/` | Web app: streaming upload, job queue, timeline view, previews |
