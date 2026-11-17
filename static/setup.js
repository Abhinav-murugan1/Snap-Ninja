"use strict";

// Separate from editing state: the guide never starts jobs or installs software.
(() => {
  document.body.insertAdjacentHTML("beforeend", `
    <dialog id="setup" class="setup" aria-labelledby="setup-title">
      <div class="setup-header">
        <img src="/static/snap-ninja.png" width="38" height="38" alt="">
        <div><p class="setup-brand">Snap Ninja</p><h2 id="setup-title">Set up your workspace</h2></div>
        <button id="setup-close" class="ghost small" type="button" aria-label="Close setup guide">Close</button>
      </div>
      <nav class="setup-steps" aria-label="Setup progress">
        <button type="button" data-step="0">1. System</button>
        <button type="button" data-step="1">2. Ollama</button>
        <button type="button" data-step="2">3. Models</button>
        <button type="button" data-step="3">4. Start editing</button>
      </nav>
      <div class="setup-content">
        <section data-panel="0">
          <h3>A few checks before your first clip</h3>
          <p>Snap Ninja processes video on this computer. Start with the required tools, then add local AI if you want it.</p>
          <dl class="setup-checks">
            <div><dt>Python <span>Runs Snap Ninja</span></dt><dd data-check="python">Checking…</dd></div>
            <div><dt>FFmpeg &amp; FFprobe <span>Required for cutting, audio and captions</span></dt><dd data-check="media">Checking…</dd></div>
            <div><dt>Node.js or Deno <span>Needed for YouTube video downloads</span></dt><dd data-check="runtime">Checking…</dd></div>
          </dl>
          <details class="setup-help"><summary>Install a missing tool</summary>
            <p>Get <a href="https://ffmpeg.org/download.html" target="_blank" rel="noopener noreferrer">FFmpeg</a> and add its <code>bin</code> folder to PATH. Both <code>ffmpeg -version</code> and <code>ffprobe -version</code> should work in a new terminal.</p>
            <p>For YouTube downloads, install <a href="https://nodejs.org/" target="_blank" rel="noopener noreferrer">Node.js</a> or <a href="https://deno.com/" target="_blank" rel="noopener noreferrer">Deno</a>. Restart Snap Ninja after changing PATH.</p>
          </details>
        </section>
        <section data-panel="1" hidden>
          <h3>Run local AI with Ollama</h3>
          <p>Ollama runs the Qwen models that help Snap Ninja understand a video. It is optional: without it, Snap Ninja finds highlights using audio and transcript signals.</p>
          <ol class="setup-instructions">
            <li><a href="https://ollama.com/download" target="_blank" rel="noopener noreferrer">Download Ollama</a> for your computer and run its installer.</li>
            <li>Open Ollama and leave it running in the background.</li>
            <li>Choose <strong>Check again</strong> below to test the connection.</li>
          </ol>
          <div class="setup-connection"><span>Ollama connection</span><strong data-check="ollama">Checking…</strong></div>
          <p class="note">If you installed the command-line version, start it in a separate terminal with <code>ollama serve</code>. If you configured OLLAMA_HOST, this check uses that address.</p>
        </section>
        <section data-panel="2" hidden>
          <h3>Download the models you need</h3>
          <p>Open PowerShell or a terminal and run a command below. Wait for the download to finish, then check again. You only need one text model.</p>
          <article class="setup-model"><div><h4>Qwen 2.5 · Fast <span>Start here</span></h4><strong data-check="fast">Checking…</strong></div>
            <p>About 4.7 GB. A practical starting point for most computers.</p>
            <div class="setup-command"><code>ollama pull qwen2.5:7b</code><button class="ghost small" type="button" data-copy="ollama pull qwen2.5:7b">Copy</button></div>
          </article>
          <article class="setup-model"><div><h4>Qwen 2.5 · Smart</h4><strong data-check="smart">Checking…</strong></div>
            <p>About 9 GB. Optional. Automatic mode prefers it on GPUs with at least 12 GB of memory; other computers may run it slowly.</p>
            <div class="setup-command"><code>ollama pull qwen2.5:14b</code><button class="ghost small" type="button" data-copy="ollama pull qwen2.5:14b">Copy</button></div>
          </article>
          <article class="setup-model"><div><h4>Qwen 2.5 VL · Vision</h4><strong data-check="vision">Checking…</strong></div>
            <p>About 6 GB. Optional. Adds video-frame understanding alongside your text model.</p>
            <div class="setup-command"><code>ollama pull qwen2.5vl:7b</code><button class="ghost small" type="button" data-copy="ollama pull qwen2.5vl:7b">Copy</button></div>
          </article>
          <article class="setup-model"><div><h4>Qwen 3.8 · Advanced</h4><strong data-check="advanced">Checking…</strong></div>
            <p>Optional, for high-memory computers. The 27B model downloads about 18 GB. Plan for 24 GB or more VRAM as a starting point, with extra memory for context and video frames. This is guidance, not a fit guarantee; lower-memory systems may offload to RAM and run slowly.</p>
            <div class="setup-command"><code>ollama pull qwen3.8:27b</code><button class="ghost small" type="button" data-copy="ollama pull qwen3.8:27b">Copy</button></div>
            <p>Update Ollama first. This model can read both transcripts and frames; it does not need the separate Qwen 2.5 VL model. Installing it does not change Automatic mode.</p>
          </article>
          <label class="setup-model-choice" for="setup-model">Preferred local model
            <select id="setup-model">
              <option value="auto">Automatic (existing 7B / 14B selection)</option>
              <option value="qwen2.5:7b">Qwen 2.5 Fast (7B)</option>
              <option value="qwen2.5:14b">Qwen 2.5 Smart (14B)</option>
              <option value="qwen3.8:27b">Qwen 3.8 Advanced (27B)</option>
            </select>
          </label>
          <p class="note" id="setup-model-note">Download your chosen model before using it. This preference also appears under Advanced in the workspace.</p>
          <p class="note">Allow extra memory and disk space beyond the download sizes. Speech recognition downloads its own Whisper model on first use. Model information: <a href="https://ollama.com/library/qwen2.5" target="_blank" rel="noopener noreferrer">Qwen 2.5</a>, <a href="https://ollama.com/library/qwen2.5vl" target="_blank" rel="noopener noreferrer">Qwen 2.5 VL</a>, <a href="https://ollama.com/library/qwen3.8" target="_blank" rel="noopener noreferrer">Qwen 3.8</a>.</p>
        </section>
        <section data-panel="3" hidden>
          <h3 id="setup-summary-title">Your workspace</h3>
          <p id="setup-summary">Run the checks to see what is ready.</p>
          <ol class="setup-instructions">
            <li>Choose <strong>Find highlights</strong> and add a short test video.</li>
            <li>Set the clip length, formats and captions, then start the job.</li>
            <li>Preview the results. Use <strong>Adjust</strong> to refine a cut before downloading.</li>
          </ol>
          <p class="note">An NVIDIA GPU is optional. CPU processing takes longer. YouTube upload is optional; you can connect a channel below or from a finished clip later.</p>
          <details class="youtube-help">
            <summary>Set up YouTube uploads (optional)</summary>
            <div data-youtube-guide="connect"></div>
          </details>
          <p class="note">Local processing uses this computer. Video links contact their source sites. Enabling Claude sends transcript content to a paid external API. Keep keys, sign-ins and private media out of a public repository.</p>
        </section>
        <p id="setup-message" class="note" role="status" aria-live="polite"></p>
      </div>
      <div class="setup-footer">
        <button id="setup-check" class="ghost" type="button">Check again</button>
        <span id="setup-page" class="note">Step 1 of 4</span>
        <button id="setup-back" class="ghost" type="button">Back</button>
        <button id="setup-next" class="primary" type="button">Continue</button>
      </div>
    </dialog>`);

  const dialog = document.querySelector("#setup");
  const el = (s) => dialog.querySelector(s);
  let step = 0, checking = false, checks = null;
  function showStep(n) {
    step = Math.max(0, Math.min(3, n));
    dialog.querySelectorAll("[data-panel]").forEach(p => { p.hidden = Number(p.dataset.panel) !== step; });
    dialog.querySelectorAll("[data-step]").forEach(b => {
      if (Number(b.dataset.step) === step) b.setAttribute("aria-current", "step");
      else b.removeAttribute("aria-current");
    });
    el("#setup-page").textContent = `Step ${step + 1} of 4`;
    el("#setup-back").disabled = step === 0;
    el("#setup-next").textContent = step === 3 ? "Open workspace" : "Continue";
    dialog.scrollTop = 0;
  }
  function status(name, ok, yes, no) {
    const node = el(`[data-check="${name}"]`);
    node.textContent = ok ? yes : no;
    node.dataset.ready = String(Boolean(ok));
  }
  async function check() {
    if (checking) return;
    checking = true;
    el("#setup-check").disabled = true;
    el("#setup-message").textContent = "Checking this computer…";
    try {
      const res = await fetch("/api/setup", { signal: AbortSignal.timeout(10000) });
      if (!res.ok) throw new Error("Connection failed");
      checks = await res.json();
      status("python", true, checks.python, "Missing");
      status("media", checks.ffmpeg && checks.ffprobe, "Ready", "Install required");
      status("runtime", checks.runtime, "Ready", "Not detected");
      status("ollama", checks.ollama, "Connected", "Not connected");
      for (const name of ["fast", "smart", "vision", "advanced"]) status(name, checks[name], "Installed", "Not installed");
      const media = checks.ffmpeg && checks.ffprobe;
      el("#setup-summary-title").textContent = media ? "Ready for your first clip" : "Install the video tools first";
      el("#setup-summary").textContent = !media
        ? "FFmpeg and FFprobe are required. You can explore the workspace now, then return to System to finish setup."
        : checks.ollama && (checks.fast || checks.smart || (checks.advanced && el("#setup-model").value === "qwen3.8:27b"))
          ? "Video tools and local AI are ready. Start with a short video to check the result on your computer."
          : "Video tools are ready. You can start with basic highlight detection and add Ollama and a text model later.";
      el("#setup-message").textContent = "Checks complete. Nothing was installed or changed.";
    } catch {
      checks = null;
      dialog.querySelectorAll("[data-check]").forEach(n => { n.textContent = "Check failed"; delete n.dataset.ready; });
      el("#setup-message").textContent = "Could not check the setup. Make sure Snap Ninja is running, then try again.";
      el("#setup-summary-title").textContent = "Setup could not be verified";
      el("#setup-summary").textContent = "Retry the checks before processing a video. You can still explore the workspace.";
    } finally {
      checking = false;
      el("#setup-check").disabled = false;
    }
  }
  function open() {
    el("#setup-model").value = document.querySelector('[name="brain_model"]').value;
    showStep(0); dialog.showModal(); check();
  }
  el("#setup-model").addEventListener("change", () => {
    const select = document.querySelector('[name="brain_model"]');
    select.value = el("#setup-model").value;
    select.dispatchEvent(new Event("change"));
    el("#setup-model-note").textContent = select.value === "qwen3.8:27b"
      ? "Qwen 3.8 selected for new jobs. Download it first; if it is unavailable, Snap Ninja falls back to an installed 7B or 14B model."
      : "Preference saved for new jobs. Download your chosen model before using it.";
    if (checks) check();
  });
  document.querySelector("#setup-open").addEventListener("click", open);
  el("#setup-close").addEventListener("click", () => dialog.close());
  el("#setup-back").addEventListener("click", () => showStep(step - 1));
  el("#setup-next").addEventListener("click", () => {
    if (step < 3) showStep(step + 1);
    else {
      // Missing tools never count as completed setup; show the guide again next launch.
      if (checks?.ffmpeg && checks?.ffprobe) {
        try { localStorage.setItem("snap-ninja:setup:v1", "done"); } catch { /* storage unavailable */ }
      }
      dialog.close();
    }
  });
  el("#setup-check").addEventListener("click", check);
  dialog.querySelectorAll("[data-step]").forEach(b => b.addEventListener("click", () => showStep(Number(b.dataset.step))));
  dialog.querySelectorAll("[data-copy]").forEach(b => b.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(b.dataset.copy);
      el("#setup-message").textContent = "Command copied. Paste it into your terminal to download the model.";
    } catch { el("#setup-message").textContent = "Clipboard unavailable. Select and copy the command above."; }
  }));
  let done = false;
  try { done = localStorage.getItem("snap-ninja:setup:v1") === "done"; } catch { /* show guide */ }
  if (!done) open();
})();
