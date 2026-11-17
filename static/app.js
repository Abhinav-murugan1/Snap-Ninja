"use strict";

const $ = (s, el = document) => el.querySelector(s);
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* storage blocked */ } },
};

const state = { file: null, jobId: null, timer: null, renderedClips: new Map() };

const modelSelect = document.querySelector('[name="brain_model"]');
const preferredModel = store.get("snap-ninja:model");
if (Array.from(modelSelect.options).some(o => o.value === preferredModel)) modelSelect.value = preferredModel;
modelSelect.addEventListener("change", () => store.set("snap-ninja:model", modelSelect.value));

// ------------------------------------------------------------------ helpers
function tc(sec) {
  sec = Math.max(0, sec || 0);
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = Math.floor(sec % 60);
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}
function sizeText(b) {
  if (b > 1e9) return (b / 1e9).toFixed(1) + " GB";
  return (b / 1e6).toFixed(0) + " MB";
}
async function api(path, opts = {}) {
  if (opts.method && !["GET", "HEAD"].includes(opts.method.toUpperCase())) {
    opts = { ...opts, headers: { ...opts.headers, "X-Snap-Ninja": "1" } };
  }
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(data.error || `Request failed (${res.status})`), { status: res.status });
  return data;
}

function uploadFile(file, kind, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/upload?kind=${kind}&name=${encodeURIComponent(file.name)}`);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.setRequestHeader("X-Snap-Ninja", "1");
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress && onProgress(e.loaded / e.total, e.loaded, e.total);
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch { /* not json */ }
      xhr.status < 300 ? resolve(data) : reject(new Error(data.error || `Upload failed (${xhr.status})`));
    };
    xhr.onerror = () => reject(new Error("Upload stopped. Check that Snap Ninja is still running, then try again."));
    xhr.send(file);
  });
}

// ------------------------------------------------------------------ new job form
const drop = $("#drop"), fileInput = $("#file"), pathInput = $("#path"), startBtn = $("#start");
state.files = [];

const MODES = {
  long: {
    lede: "Find the moments worth keeping.",
    title: "Choose a video or drop it here",
    hint: "MP4, MOV, MKV, WEBM. Hours-long files are fine.",
    path: "Or paste a video link (YouTube, Kick, Twitch, including live streams) or a file path on this computer",
    placeholder: "https://www.youtube.com/watch?v=…   or   D:\\Streams\\full stream.mp4",
    button: "Find the best moments",
  },
  ready: {
    lede: "Put the finishing touches on your clips.",
    title: "Choose clips or drop them here",
    hint: "Pick as many as you like. Each clip stays exactly as you cut it.",
    path: "Or paste a link to a clip, a clip's file path, or a folder of clips on this computer",
    placeholder: "D:\\Clips\\my clip.mp4   or   D:\\Clips",
    button: "Caption and title my clips",
  },
  dvr: {
    lede: "Your stream. Your cuts.",
    path: "Paste the link of a stream that is live right now (YouTube, Twitch, Kick and most other sites)",
    placeholder: "https://www.twitch.tv/…   or   https://www.youtube.com/@channel/live",
    button: "Start recording",
  },
  chat: {
    lede: "Capture the moments chat reacts to.",
    path: "Paste the link of a Twitch or YouTube stream that is live right now. It can run at the same time as a "
      + "stream you're cutting by hand.",
    placeholder: "https://www.twitch.tv/…   or   https://www.youtube.com/@channel/live",
    button: "Start the hype clipper",
  },
};
function mode() { return document.querySelector('input[name="mode"]:checked')?.value || "long"; }
function applyMode() {
  const m = MODES[mode()];
  $("#lede").textContent = m.lede;
  $("#path-label").textContent = m.path;
  pathInput.placeholder = m.placeholder;
  startBtn.textContent = m.button;
  fileInput.multiple = mode() === "ready";
  drop.hidden = mode() === "dvr" || mode() === "chat";  // a live stream comes from a link, not a file
  if ((mode() === "dvr" || mode() === "chat") && state.files.length) { state.files = []; drop.classList.remove("has-file"); refreshStart(); }
  $("#clips-fields").hidden = mode() !== "long";  // you pick the moments yourself
  const musicBox = document.querySelector('#settings [name="music"]');
  if (musicBox && mode() !== "long") musicBox.checked = false;  // your clip keeps its own sound
  if (!state.files.length && m.title) {
    $("#drop-title").textContent = m.title;
    $("#drop-hint").textContent = m.hint;
  }
}
// ------------------------------------------------------------------ tabs: each mode keeps its own place
// (its link, its chosen files, and the job it was showing), also after Snap Ninja is restarted
const TABS = ["long", "ready", "dvr", "chat"];
state.tabs = Object.fromEntries(TABS.map((t) => [t, { jobId: null, path: "", files: [] }]));
state.tab = "long";
try {
  const saved = JSON.parse(store.get("clipper:tabs") || "{}");
  for (const t of TABS) if (saved[t]) state.tabs[t].jobId = saved[t];
  if (TABS.includes(saved.current)) state.tab = saved.current;
} catch { /* nothing saved */ }
function saveTabs() {
  const out = Object.fromEntries(TABS.map((t) => [t, state.tabs[t].jobId]));
  store.set("clipper:tabs", JSON.stringify({ ...out, current: state.tab }));
}
function jobTab(job) {
  const o = job.options || {};
  return o.chat_only ? "chat" : o.live_manual ? "dvr" : o.ready_clip ? "ready" : "long";
}
function selectTab(t) {
  const r = document.querySelector(`input[name="mode"][value="${t}"]`);
  if (r) r.checked = true;
  state.tab = t;
  applyMode();
}
function stashForm() {
  Object.assign(state.tabs[state.tab], { path: pathInput.value, files: state.files });
}
function restoreForm() {
  const t = state.tabs[state.tab];
  state.files = [];
  drop.classList.remove("has-file");
  pathInput.value = t.path;
  applyMode();
  if (t.files.length) setFiles(t.files);
  refreshStart();
}
function switchTab(next) {
  stashForm();
  selectTab(next);
  restoreForm();
  const id = state.tabs[next].jobId;
  if (id) openJob(id); else showForm();
  saveTabs();
}
document.querySelectorAll('input[name="mode"]').forEach((r) => r.addEventListener("change", () => switchTab(r.value)));

function refreshStart() {
  startBtn.disabled = !(state.files.length || pathInput.value.trim());
}
function setFiles(list) {
  const files = Array.from(list || []).filter(Boolean);
  if (!files.length) return;
  state.files = mode() === "ready" ? files : files.slice(0, 1);
  pathInput.value = "";
  drop.classList.add("has-file");
  const total = state.files.reduce((n, f) => n + f.size, 0);
  $("#drop-title").textContent = state.files.length === 1 ? state.files[0].name : `${state.files.length} clips`;
  $("#drop-hint").textContent = `${sizeText(total)}. Choose different files any time.`;
  refreshStart();
}
drop.addEventListener("click", () => fileInput.click());
drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } });
fileInput.addEventListener("change", () => setFiles(fileInput.files));
["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => setFiles(e.dataTransfer.files));
pathInput.addEventListener("input", () => {
  if (pathInput.value.trim()) {
    state.files = [];
    drop.classList.remove("has-file");
    applyMode();
  }
  refreshStart();
});
applyMode();

function readOptions() {
  const form = $("#settings"), out = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    out[el.name] = el.type === "checkbox" ? el.checked : el.value;
  }
  if (mode() === "ready") {  // keep the clip as it was cut: no pause trimming, no zooms
    Object.assign(out, { ready_clip: true, tighten_pauses: false, punch_in: false });
  }
  if (mode() === "dvr") Object.assign(out, { live_manual: true, tighten_pauses: false, punch_in: false });
  if (mode() === "chat") {
    Object.assign(out, { live_manual: true, chat_only: true, tighten_pauses: false, punch_in: false });
  }
  return out;
}

startBtn.addEventListener("click", async () => {
  $("#form-error").textContent = "";
  const options = readOptions();
  if (options.live_manual && !/^https?:\/\//i.test(pathInput.value.trim())) {
    $("#form-error").textContent = "Paste the link of the live stream (it starts with https://).";
    return;
  }
  if (mode() === "long" && Number(options.min_len) >= Number(options.max_len)) {
    $("#form-error").textContent = "Make the longest clip length bigger than the shortest.";
    return;
  }
  startBtn.disabled = true;
  try {
    const musicFile = $("#music-file").files[0];
    const musicId = musicFile ? (await uploadFile(musicFile, "music")).upload_id : null;
    const makeJob = (body) => {
      if (musicId) body.music_upload_id = musicId;
      return api("/api/jobs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    };
    let first = null;
    if (state.files.length) {
      const files = state.files;
      for (const [i, file] of files.entries()) {
        const n = files.length > 1 ? ` (${i + 1} of ${files.length})` : "";
        showJobShell(file.name);
        const up = await uploadFile(file, "video", (f, done, total) => {
          setProgress("upload", f * 100, `Uploading${n} ${sizeText(done)} of ${sizeText(total)}`);
        });
        const job = await makeJob({ options, upload_id: up.upload_id, name: file.name });
        first = first || job;
      }
    } else {
      const value = pathInput.value.trim();
      first = await makeJob(/^https?:\/\//i.test(value) ? { options, url: value } : { options, path: value });
    }
    openJob(first.id);
    loadRecent();
    if (first.more) $("#job-message").textContent = `Started ${first.more + 1} clips. The others are under Previous jobs.`;
  } catch (err) {
    showForm();
    $("#form-error").textContent = err.message;
  } finally {
    refreshStart();
  }
});

// ------------------------------------------------------------------ views
function showForm() {
  clearTimeout(state.timer);
  dvrClose();
  state.jobId = null;
  state.tabs[state.tab].jobId = null;
  saveTabs();
  $("#job").hidden = true;
  $("#new-job").hidden = false;
  history.replaceState(null, "", "/");
}
function showJobShell(name, message = "Starting upload") {
  $("#new-job").hidden = true;
  $("#job").hidden = false;
  $("#job-name").textContent = name;
  $("#job-meta").textContent = "";
  $("#job-output").textContent = "";
  $("#job-error").textContent = "";
  $("#clips").innerHTML = "";
  $("#log").innerHTML = "";
  $("#map").hidden = true;
  $("#live").hidden = true;
  dvrClose();
  $("#stages").hidden = false;
  document.querySelector(".meter").hidden = false;
  state.renderedClips.clear();
  setProgress("upload", 0, message);
}
$("#another").addEventListener("click", showForm);
$("#open-folder").addEventListener("click", async () => {
  try {
    await api(`/api/jobs/${state.jobId}/open-folder`, { method: "POST" });
  } catch (err) {
    $("#job-error").textContent = err.message;
  }
});
$("#stop-live").addEventListener("click", async () => {
  const btn = $("#stop-live");
  btn.disabled = true;
  btn.textContent = "Stopping…";
  try {
    await api(`/api/jobs/${state.jobId}/stop`, { method: "POST" });
    $("#job-message").textContent = "Stopping. The last part is being clipped now.";
  } catch (err) {
    $("#job-error").textContent = err.message;
  } finally {
    setTimeout(() => { btn.disabled = false; btn.textContent = "Stop recording"; }, 4000);
  }
});

const STAGE_ORDER = ["upload", "transcribe", "analyze", "render"];
function stageKey(stage) {
  if (stage === "probe" || stage === "audio") return "transcribe";
  if (stage === "queued" || stage === "download") return "upload";
  return STAGE_ORDER.includes(stage) ? stage : "upload";
}
function setProgress(stage, pct, message, finished = false) {
  const key = stageKey(stage);
  const idx = STAGE_ORDER.indexOf(key);
  document.querySelectorAll("#stages li").forEach((li, i) => {
    li.classList.toggle("done", finished || i < idx || (key !== "upload" && i === 0));
    li.classList.toggle("active", !finished && i === idx);
  });
  $("#meter-fill").style.width = `${Math.max(0, Math.min(100, pct))}%`;
  if (message !== undefined) $("#job-message").textContent = message;
}

function openJob(id) {
  clearTimeout(state.timer);
  if (state.jobId !== id) {
    showJobShell("", "Loading job");
  }
  state.jobId = id;
  store.set("clipper:lastJob", id);
  history.replaceState(null, "", `/#job=${id}`);
  poll();
}

async function poll() {
  const id = state.jobId;
  if (!id) return;
  try {
    const job = await api(`/api/jobs/${id}`);
    if (id !== state.jobId) return;
    renderJob(job);
    if (job.status === "queued" || job.status === "running" || anyUploading(job) || cutsBusy(job)
        || (job.clips || []).some((c) => c.editing)) {
      state.timer = setTimeout(poll, 1500);
    }
  } catch (err) {
    if (err.status === 404 && id === state.jobId) { showForm(); return; }
    $("#job-error").textContent = err.message;
    state.timer = setTimeout(poll, 4000);
  }
}

function renderJob(job) {
  state.lastJob = job;
  $("#job-output").textContent = job.output_path ? `Clips folder: ${job.output_path}` : "";
  const kind = jobTab(job);
  if (state.tabs[kind].jobId !== job.id || state.tab !== kind) {
    if (state.tab !== kind) { stashForm(); selectTab(kind); restoreForm(); }
    state.tabs[kind].jobId = job.id;
    saveTabs();
  }
  $("#new-job").hidden = true;
  $("#job").hidden = false;
  $("#job-name").textContent = job.name;
  const v = job.video;
  const from = job.source_info && job.source_info.uploader ? `${job.source_info.uploader}, ` : "";
  const live = job.live;
  if (live) {
    $("#job-meta").textContent = `${from}live stream`;
  } else {
    const sit = job.situation;
    const kind = sit && sit.video_type ? `${sit.video_type.replace("_", " ")}${sit.game_or_topic ? ` (${sit.game_or_topic})` : ""}, ` : "";
    $("#job-meta").textContent = v ? `${from}${kind}${tc(v.duration)} long, ${v.width}×${v.height}` : (job.url ? job.url : "");
  }
  const done = job.status === "done";
  const msg = job.status === "queued" ? "Waiting for the previous job to finish" : job.message;
  // live jobs: a status bar replaces the 4-step progress
  $("#live").hidden = !live;
  $("#stages").hidden = !!live;
  document.querySelector(".meter").hidden = !!live;
  if (live) {
    const recording = live.recording && job.status === "running";
    $("#live").classList.toggle("on", recording);
    if (live.manual) {
      $("#live-text").textContent = recording
        ? `Recording live: ${tc(live.recorded)} so far.`
        : `Recording ended: ${tc(live.recorded)} recorded. You can still cut clips from it.`;
    } else {
      const clipped = `${live.parts_done} of ${live.parts_recorded} parts clipped`;
      $("#live-text").textContent = recording
        ? `Recording live: ${tc(live.recorded)} so far, ${clipped}. A part is clipped every ${Math.round(live.part_minutes)} min.`
        : `Recording ended: ${tc(live.recorded)} recorded, ${clipped}.`;
    }
    const stopBtn = $("#stop-live");
    stopBtn.hidden = !recording;
    $("#job-message").textContent = msg;
  } else {
    setProgress(job.stage, job.progress, msg, done);
  }
  $("#job-error").textContent = job.status === "failed" ? (job.error || "This job stopped.") : "";

  const log = $("#log");
  if (log.childElementCount !== job.log.length) {
    log.innerHTML = "";
    for (const entry of job.log) {
      const li = document.createElement("li");
      li.textContent = `${new Date(entry.t * 1000).toLocaleTimeString()}  ${entry.msg}`;
      log.appendChild(li);
    }
    log.scrollTop = log.scrollHeight;
  }
  if (!live && job.energy && job.energy.length && v) drawMap(job.energy, v.duration, job.clips);
  dvrRender(job);
  $("#open-folder").hidden = !(job.clips || []).some((c) => c.files && (c.files.short || c.files.landscape));
  refreshSource(job);
  renderClips(job);
}

// ------------------------------------------------------------------ timeline map
function drawMap(energy, duration, clips) {
  const svg = $("#map-svg"), labels = $("#map-labels");
  $("#map").hidden = false;
  $("#map-end").textContent = tc(duration);
  const W = 600, H = 100, bw = W / energy.length;
  let html = "";
  energy.forEach((e, i) => {
    const h = Math.max(1.5, e * (H - 30));
    html += `<rect class="bar" x="${(i * bw).toFixed(2)}" y="${(H - h).toFixed(2)}" width="${Math.max(bw - 0.3, 0.4).toFixed(2)}" height="${h.toFixed(2)}"/>`;
  });
  labels.innerHTML = "";
  clips.forEach((c, i) => {
    const x1 = (c.start / duration) * W, x2 = (c.end / duration) * W;
    html += `<rect class="range" data-i="${i}" x="${x1}" y="0" width="${Math.max(x2 - x1, 1)}" height="${H}"/>`;
    html += `<path class="bracket" d="M${x1 + 3} 1 H${x1} V${H - 1} H${x1 + 3} M${x2 - 3} 1 H${x2} V${H - 1} H${x2 - 3}"/>`;
    const b = document.createElement("button");
    b.className = "map-label";
    b.type = "button";
    b.textContent = i + 1;
    b.style.left = `${((c.start + c.end) / 2 / duration) * 100}%`;
    b.setAttribute("aria-label", `Go to clip ${i + 1}: ${c.title}`);
    b.addEventListener("click", () => focusClip(i));
    labels.appendChild(b);
  });
  svg.innerHTML = html;
  svg.querySelectorAll(".range").forEach((r) => r.addEventListener("click", () => focusClip(+r.dataset.i)));
}
function focusClip(i) {
  const li = $("#clips").children[i];
  if (!li) return;
  li.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
  li.classList.add("flash");
  setTimeout(() => li.classList.remove("flash"), 1200);
}

// ------------------------------------------------------------------ clips
function renderClips(job) {
  const list = $("#clips"), tpl = $("#clip-tpl");
  const base = `/files/${job.id}/`;
  // rebuild if the clip set changed (e.g. analysis just finished)
  const sig = job.clips.map((c) => `${c.start}-${c.end}`).join("|");
  if (list.dataset.sig !== sig) {
    list.innerHTML = "";
    list.dataset.sig = sig;
    state.renderedClips.clear();
    job.clips.forEach(() => list.appendChild(tpl.content.firstElementChild.cloneNode(true)));
  }
  job.clips.forEach((c, i) => {
    const li = list.children[i];
    const f = c.files || {};
    $(".rank", li).textContent = i + 1;
    $(".title", li).textContent = c.title || `Clip ${i + 1}`;
    const where = c.part
      ? `${tc(c.abs_start)} to ${tc(c.abs_end)} into the stream (part ${parseInt(c.part.slice(1), 10)})`
      : `${tc(c.start)} to ${tc(c.end)} in the original`;
    $(".tc", li).textContent = `${where}, ${Math.round(c.duration)} s` +
      (c.category ? `, ${c.category}` : "") + (c.edited ? ". Adjusted by you" : `. Score ${Math.round(c.score)}`);
    const es = $(".clip-edit-state", li);
    es.classList.toggle("bad", !!c.edit_error);
    es.textContent = c.editing ? "Re-editing with your changes…"
      : c.edit_error ? c.edit_error
      : c.youtube_outdated ? "You changed this clip after uploading it. Upload again to post the new version." : "";
    $(".hook", li).textContent = c.hook ? `Hook: “${c.hook}”` : "";
    $(".reason", li).textContent = c.reason || "";
    const pending = $(".pending", li);
    if (f.thumb) pending.style.backgroundImage = `linear-gradient(rgba(0,0,0,.55), rgba(0,0,0,.55)), url("${base}${f.thumb}")`;

    const primary = f.short ? "short" : f.landscape ? "landscape" : null;
    const key = `${f.short || ""}|${f.landscape || ""}`;
    if (primary && state.renderedClips.get(i) !== key) {
      state.renderedClips.set(i, key);
      const video = $("video", li);
      if (!video.src.includes(f[primary])) {
        video.src = base + f[primary] + "#t=0.6"; // show a real frame instead of black
        video.preload = "metadata";
      }
      $(".clip-media", li).classList.toggle("wide", primary === "landscape");
      li.classList.add("ready");
      const actions = $(".actions", li);
      actions.innerHTML = "";
      const up = document.createElement("button");
      up.type = "button";
      up.className = "main";
      up.textContent = "Upload to YouTube";
      up.addEventListener("click", () => openYouTube(job.id, i, f));
      actions.append(up);
      if (!c.part && !job.source_deleted && job.status !== "running") {
        const adj = document.createElement("button");
        adj.type = "button";
        adj.className = "adjust";
        adj.textContent = "Adjust";
        adj.title = "Change where the clip starts and ends, its title and hook";
        adj.addEventListener("click", () => openEditor(state.lastJob || job, i));
        actions.append(adj);
      }
      const files = document.createElement("span");
      files.className = "files";
      if (f.short) files.append(link(base + f.short, "Download short"));
      if (f.landscape) files.append(link(base + f.landscape, "Download original shape"));
      actions.append(files);
      if (f.short && f.landscape) {
        const t = document.createElement("button");
        t.type = "button";
        t.className = "textlink";
        t.textContent = "Preview original shape";
        t.addEventListener("click", () => {
          const wide = !$(".clip-media", li).classList.contains("wide");
          video.src = base + (wide ? f.landscape : f.short);
          $(".clip-media", li).classList.toggle("wide", wide);
          t.textContent = wide ? "Preview short" : "Preview original shape";
          video.play().catch(() => {});
        });
        files.append(t);
      }
    } else if (!primary) {
      $(".pending", li).textContent = job.status === "failed" ? "Not edited" : "Editing…";
    }
    renderYouTubeState($(".yt-state", li), c.youtube || {});
    const up = $(".vote-up", li), down = $(".vote-down", li);
    up.setAttribute("aria-pressed", String(c.vote === "up"));
    down.setAttribute("aria-pressed", String(c.vote === "down"));
    if (!li.dataset.voteWired) {
      li.dataset.voteWired = "1";
      up.addEventListener("click", () => voteClip(job.id, i, li, "up"));
      down.addEventListener("click", () => voteClip(job.id, i, li, "down"));
    }
  });
}

async function voteClip(jobId, index, li, choice) {
  const btn = $(choice === "up" ? ".vote-up" : ".vote-down", li);
  const vote = btn.getAttribute("aria-pressed") === "true" ? null : choice;  // click again to clear
  try {
    await api(`/api/jobs/${jobId}/clips/${index}/vote`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ vote }),
    });
    $(".vote-up", li).setAttribute("aria-pressed", String(vote === "up"));
    $(".vote-down", li).setAttribute("aria-pressed", String(vote === "down"));
  } catch (err) {
    $("#job-error").textContent = err.message;
  }
}

function renderYouTubeState(el, yt) {
  el.replaceChildren();
  el.classList.remove("bad");
  for (const kind of ["short", "landscape"]) {
    const s = yt[kind];
    if (!s) continue;
    const line = document.createElement("span");
    line.style.display = "block";
    const name = kind === "short" ? "Short" : "Original shape";
    if (s.status === "uploading") {
      line.textContent = `${name}: uploading to YouTube, ${s.progress || 0}%`;
    } else if (s.status === "done") {
      line.append(`${name}: `);
      const a = document.createElement("a");
      a.href = s.url; a.target = "_blank"; a.rel = "noopener";
      a.textContent = "on YouTube";
      line.append(a);
      if (s.privacy && s.privacy !== "public") line.append(` (${s.privacy})`);
    } else if (s.status === "failed") {
      line.textContent = `${name}: upload failed. ${s.error || ""}`;
      el.classList.add("bad");
    }
    el.append(line);
  }
}

// ------------------------------------------------------------------ delete original video
const delDialog = $("#del");
const src = { jobId: null, info: null, job: null };

async function refreshSource(job) {
  const btn = $("#del-src"), note = $("#src-note");
  if (job.status !== "done" && job.status !== "failed") {
    btn.hidden = true;
    note.textContent = "";
    return;
  }
  if (src.jobId === job.id && src.info && src.info.deleted === !!job.source_deleted) return;
  src.jobId = job.id;
  src.job = job;
  try {
    src.info = await api(`/api/jobs/${job.id}/source`);
  } catch {
    return;
  }
  const info = src.info;
  if (info.deleted || (!info.available && !info.files)) {
    btn.hidden = true;
    note.textContent = info.deleted ? "Original video deleted. Clips are kept." : "";
    return;
  }
  note.textContent = "";
  btn.hidden = false;
  btn.textContent = `Delete original video (${sizeText(info.size)})`;
}

$("#del-src").addEventListener("click", () => {
  const info = src.info, job = src.job;
  if (!info) return;
  const size = sizeText(info.size);
  let text, action = "Delete";
  if (info.kind === "recording") {
    text = `This deletes the ${info.files} recorded parts of this live stream (${size}). Your clips, captions and ` +
      "YouTube details stay. This can't be undone.";
  } else if (info.kind === "yours") {
    text = `This moves your original file to the Recycle Bin: ${info.path} (${size}). Your clips stay, and you ` +
      "can restore the file from the Recycle Bin.";
    action = "Move to Recycle Bin";
  } else if (job.url) {
    text = `This deletes the downloaded copy of “${info.name}” (${size}). Your clips stay. You can download it ` +
      "again from the link if you need it.";
  } else {
    text = `This deletes the uploaded copy of “${info.name}” (${size}). Your clips stay, and the file you ` +
      "uploaded from isn't touched.";
  }
  $("#del-text").textContent = text;
  $("#del-confirm").textContent = action;
  $("#del-error").textContent = "";
  delDialog.showModal();
});
$("#del-cancel").addEventListener("click", () => delDialog.close());
$("#del-confirm").addEventListener("click", async () => {
  const btn = $("#del-confirm");
  btn.disabled = true;
  try {
    const res = await api(`/api/jobs/${src.jobId}/source/delete`, { method: "POST" });
    delDialog.close();
    src.info = null;
    $("#del-src").hidden = true;
    $("#src-note").textContent = `${res.kind === "yours" ? "Moved to the Recycle Bin" : "Deleted"}. ` +
      `Freed ${sizeText(res.freed)}. Clips are kept.`;
    poll();
  } catch (err) {
    $("#del-error").textContent = err.message;
  } finally {
    btn.disabled = false;
  }
});

function anyUploading(job) {
  return (job.clips || []).some((c) => Object.values(c.youtube || {}).some((s) => s.status === "uploading"));
}

// ------------------------------------------------------------------ YouTube upload dialog
const ytDialog = $("#yt");
const yt = { jobId: null, index: null, files: null, timer: null, kind: "short" };

function ytShow(section) {
  $("#yt-setup").hidden = section !== "setup";
  $("#yt-fields").hidden = section !== "fields";
  $("#yt-upload").hidden = section !== "fields";
}

async function ytRefresh() {
  clearTimeout(yt.timer);
  $("#yt-error").textContent = "";
  let st;
  try {
    st = await api("/api/youtube/status");
  } catch (err) {
    $("#yt-error").textContent = err.message;
    return;
  }
  if (st.connected) {
    $("#yt-channel").textContent = st.channel || "your channel";
    ytShow("fields");
    await ytLoadMeta();
    return;
  }
  ytShow("setup");
  $("#yt-client-row").hidden = st.has_client;
  $("#yt-signin").hidden = !st.has_client || st.connecting;
  if (!st.has_client) {
    $("#yt-guide").open = true;
    $("#yt-setup-text").textContent = "Connect your YouTube channel once. Add the OAuth client file you downloaded " +
      "from Google Cloud (a “Desktop app” client).";
  } else if (st.connecting) {
    $("#yt-setup-text").textContent = "Finish signing in on the Google page that opened in your browser. " +
      "This box updates by itself when you're done.";
    yt.timer = setTimeout(ytRefresh, 2000);
  } else {
    $("#yt-setup-text").textContent = "Sign in with the Google account that owns the channel you want to post to.";
  }
  if (st.error) $("#yt-error").textContent = st.error;
}

async function ytLoadMeta() {
  const kinds = ["short", "landscape"].filter((k) => yt.files[k]);
  document.querySelectorAll('#yt-form input[name="kind"]').forEach((r) => {
    r.disabled = !yt.files[r.value];
    r.checked = r.value === yt.kind;
  });
  if (!yt.files[yt.kind]) yt.kind = kinds[0];
  try {
    const m = await api(`/api/jobs/${yt.jobId}/clips/${yt.index}/youtube?kind=${yt.kind}`);
    $("#yt-title").value = m.title;
    $("#yt-desc").value = m.description;
    $("#yt-tags").value = m.tags.join(", ");
    $("#yt-privacy").value = m.privacy;
    yt.category = m.category_id;
    ytCount();
  } catch (err) {
    $("#yt-error").textContent = err.message;
  }
}

function ytCount() {
  $("#yt-title-count").textContent = `${$("#yt-title").value.length}/100`;
}

function openYouTube(jobId, index, files) {
  Object.assign(yt, { jobId, index, files, kind: files.short ? "short" : "landscape" });
  $("#yt-error").textContent = "";
  ytShow("");
  ytDialog.showModal();
  ytRefresh();
}

$("#yt-title").addEventListener("input", ytCount);
document.querySelectorAll('#yt-form input[name="kind"]').forEach((r) =>
  r.addEventListener("change", () => { yt.kind = r.value; ytLoadMeta(); }));
$("#yt-cancel").addEventListener("click", () => { clearTimeout(yt.timer); ytDialog.close(); });
ytDialog.addEventListener("close", () => clearTimeout(yt.timer));

$("#yt-client").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  $("#yt-error").textContent = "";
  try {
    await api("/api/youtube/client", { method: "POST", body: file });
    await ytRefresh();
  } catch (err) {
    $("#yt-error").textContent = err.message;
  }
  e.target.value = "";
});

$("#yt-signin").addEventListener("click", async () => {
  try {
    await api("/api/youtube/connect", { method: "POST" });
  } catch (err) {
    $("#yt-error").textContent = err.message;
    return;
  }
  ytRefresh();
});

$("#yt-switch").addEventListener("click", async () => {
  await api("/api/youtube/disconnect", { method: "POST" }).catch(() => {});
  ytRefresh();
});

$("#yt-upload").addEventListener("click", async () => {
  const btn = $("#yt-upload");
  if (!$("#yt-title").value.trim()) {
    $("#yt-error").textContent = "Add a title.";
    return;
  }
  btn.disabled = true;
  try {
    await api(`/api/jobs/${yt.jobId}/clips/${yt.index}/youtube`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        kind: yt.kind, title: $("#yt-title").value, description: $("#yt-desc").value,
        tags: $("#yt-tags").value, privacy: $("#yt-privacy").value, category_id: yt.category,
      }),
    });
    ytDialog.close();
    clearTimeout(state.timer);
    poll();
  } catch (err) {
    $("#yt-error").textContent = err.message;
  } finally {
    btn.disabled = false;
  }
});
function link(href, text, main) {
  const a = document.createElement("a");
  a.href = href;
  a.download = "";
  a.textContent = text;
  if (main) a.className = "main";
  return a;
}

// ------------------------------------------------------------------ boot
// ------------------------------------------------------------------ previous jobs (open, delete, undo)
const TRASH_ICON = '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M7.5 3.5h5M3.5 5.5h13M5.5 5.5l.8 10.2c.1.7.6 1.3 1.4 1.3h4.6c.8 0 1.3-.6 1.4-1.3l.8-10.2M8.3 8.5v5.5M11.7 8.5v5.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';

function diskText(b) {  // 6.8 GB, 412 MB, 96 KB
  if (b >= 1e9) return `${(b / 1e9).toFixed(1)} GB`;
  if (b >= 1e6) return `${Math.round(b / 1e6)} MB`;
  return `${Math.max(1, Math.round(b / 1e3))} KB`;
}
function showTotal() {
  const rows = [...$("#recent-list").children];
  const total = rows.reduce((n, li) => n + Number(li.dataset.size || 0), 0);
  $("#recent-total").innerHTML = rows.length
    ? `App-managed data uses <strong>${diskText(total)}</strong>; external clips are separate` : "";
}

function jobWhen(j) {
  return new Date(j.created * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
function jobState(j) {
  if (j.status === "done") return `${j.clip_count} clip${j.clip_count === 1 ? "" : "s"}`;
  if (j.status === "failed") return "stopped";
  if (j.status === "queued") return "waiting";
  return `${Math.round(j.progress)}%`;
}

async function loadRecent() {
  let jobs = [];
  try { jobs = await api("/api/jobs?sizes=1"); } catch { return; /* server not ready */ }
  const list = $("#recent-list");
  list.innerHTML = "";
  $("#recent-count").textContent = jobs.length ? String(jobs.length) : "";
  $("#recent-empty").hidden = jobs.length > 0;
  for (const j of jobs) {
    const li = document.createElement("li");
    li.dataset.id = j.id;
    li.dataset.size = j.size || 0;
    if (j.id === state.jobId) li.classList.add("current");
    const open = document.createElement("button");
    open.type = "button";
    open.className = "recent-open";
    const name = document.createElement("span");
    name.className = "recent-name";
    name.textContent = j.name;
    const meta = document.createElement("span");
    meta.className = "recent-meta";
    meta.textContent = `${jobWhen(j)}, ${jobState(j)}, `;
    const size = document.createElement("span");
    size.className = j.size >= 1e9 ? "recent-size big" : "recent-size";  // big ones stand out
    size.textContent = diskText(j.size || 0);
    meta.appendChild(size);
    open.append(name, meta);
    open.addEventListener("click", () => { closeRecent(); openJob(j.id); });
    const del = document.createElement("button");
    del.type = "button";
    del.className = "recent-del";
    del.innerHTML = TRASH_ICON;
    const busy = j.status === "running" || j.status === "queued";
    del.disabled = busy;
    del.title = busy ? "Still working: stop it or let it finish first" : "Delete this job";
    del.setAttribute("aria-label", `Delete ${j.name} (${diskText(j.size || 0)})`);
    del.addEventListener("click", () => deleteJob(j, li));
    li.append(open, del);
    list.appendChild(li);
  }
  showTotal();
}

function closeRecent() {
  $("#recent-menu").hidden = true;
  $("#recent-btn").setAttribute("aria-expanded", "false");
}
$("#recent-btn").addEventListener("click", (e) => {
  e.stopPropagation();
  const opening = $("#recent-menu").hidden;
  if (opening) loadRecent();
  $("#recent-menu").hidden = !opening;
  $("#recent-btn").setAttribute("aria-expanded", String(opening));
});
document.addEventListener("click", (e) => { if (!e.target.closest(".recent")) closeRecent(); });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("#recent-menu").hidden) { closeRecent(); $("#recent-btn").focus(); }
});

// delete at once; the toast offers Undo for a few seconds (the server keeps the job for a minute)
let toastTimer = null;
async function deleteJob(j, li) {
  const next = li.nextElementSibling || li.previousElementSibling;
  li.remove();
  try {
    await api(`/api/jobs/${j.id}`, { method: "DELETE" });
  } catch (err) {
    loadRecent();
    showToast(err.message);
    return;
  }
  for (const t of TABS) if (state.tabs[t].jobId === j.id && t !== state.tab) state.tabs[t].jobId = null;
  if (state.jobId === j.id) showForm();
  saveTabs();
  const n = $("#recent-list").children.length;
  showTotal();
  $("#recent-count").textContent = n ? String(n) : "";
  $("#recent-empty").hidden = n > 0;
  if (next) $(".recent-del", next)?.focus();
  showToast(`Deleted “${j.name}”, freeing ${diskText(j.size || 0)}`, async () => {
    try {
      await api(`/api/jobs/${j.id}/restore`, { method: "POST" });
      showToast(`Restored “${j.name}”`);
      loadRecent();
    } catch (err) {
      showToast(err.message);
    }
  });
}

function showToast(text, undo) {
  clearTimeout(toastTimer);
  $("#toast-text").textContent = text;
  const btn = $("#toast-undo");
  btn.hidden = !undo;
  btn.onclick = undo ? () => { $("#toast").hidden = true; undo(); } : null;
  $("#toast").hidden = false;
  toastTimer = setTimeout(() => { $("#toast").hidden = true; }, undo ? 8000 : 4000);
}

async function boot() {
  const m = location.hash.match(/job=([\w-]+)/);
  selectTab(state.tab);  // where you left off
  if (m) openJob(m[1]);
  else if (state.tabs[state.tab].jobId) openJob(state.tabs[state.tab].jobId);
  try {
    const [st, tracks] = await Promise.all([api(`/api/status?model=${encodeURIComponent(modelSelect.value)}`), api("/api/music")]);
    const notes = [];
    notes.push(st.cuda ? "Speech recognition runs on your NVIDIA GPU." : "No CUDA GPU found, so speech recognition runs on the CPU. Pick a smaller speech model for long videos.");
    if (st.claude) {
      notes.push("Claude is connected and will pick moments from the transcript (paid API usage).");
    } else if (st.brain) {
      const parts = [st.model || "local model", st.vision ? "with frame understanding" : "without frame understanding"];
      notes.push(`Local AI is ready (${parts.join(", ")}): it works out what kind of video this is, follows ` +
        "stories, jokes and plays, picks clips with their set-up and payoff, and reviews each one. Free, on this PC.");
      const n = (st.votes && st.votes.up + st.votes.down) || 0;
      notes.push(n ? `It has learned from ${n} of your clip ratings.` :
        "Rate clips with 👍 / 👎 and it learns what you like.");
    } else {
      notes.push(`${st.brain_message} Until then, moments are picked from loudness, hype and excitement words.`);
    }
    if (!st.claude) {
      const llm = document.querySelector('[name="use_llm"]');
      llm.checked = false;
      llm.disabled = true;
    }
    $("#env-note").textContent = notes.join(" ");
    const sel = $("#music-track");
    for (const t of tracks) {
      const o = document.createElement("option");
      o.value = t; o.textContent = t;
      sel.appendChild(o);
    }
    if (!tracks.length) sel.options[0].textContent = "Built-in lo-fi loop (add tracks to the music folder)";
  } catch { /* ignore */ }
  loadRecent();
}


// ------------------------------------------------------------------ live: cut clips by hand
const dvr = { jobId: null, hls: null, inT: null, outT: null, statuses: {}, lastStatusFetch: 0, saved: {} };
const dvrVideo = $("#dvr-video");

function loadHls() {
  if (window.Hls) return Promise.resolve(window.Hls);
  return new Promise((resolve, reject) => {
    const sc = document.createElement("script");
    sc.src = "https://cdn.jsdelivr.net/npm/hls.js@1.5.17/dist/hls.min.js";
    sc.onload = () => resolve(window.Hls);
    sc.onerror = () => reject(new Error("Couldn't load the video player. Check your internet connection."));
    document.head.appendChild(sc);
  });
}

function dvrClose() {
  // leaving this recording (another tab or job): remember where you were and your marks
  if (dvr.jobId) dvr.saved[dvr.jobId] = { t: dvrVideo.currentTime, inT: dvr.inT, outT: dvr.outT };
  if (dvr.hls) { dvr.hls.destroy(); dvr.hls = null; }
  dvrVideo.removeAttribute("src");
  dvr.jobId = null;
  dvr.inT = dvr.outT = null;
  $("#dvr").hidden = true;
  $("#chatbox").hidden = true;
}

async function dvrOpen(job) {
  dvr.jobId = job.id;
  const back = dvr.saved[job.id];
  dvr.inT = back ? back.inT : null;
  dvr.outT = back ? back.outT : null;
  dvrMarks();
  if (back && back.t) {  // pick up where you left off
    dvrVideo.addEventListener("loadedmetadata", () => { dvrVideo.currentTime = back.t; }, { once: true });
  }
  $("#dvr-msg").textContent = "";
  const src = `/live/${job.id}/index.m3u8`;
  try {
    const Hls = await loadHls();
    if (dvr.jobId !== job.id) return;
    if (Hls.isSupported()) {
      // an EVENT playlist keeps every segment, so the whole recording stays seekable
      dvr.hls = new Hls({ liveDurationInfinity: true, liveSyncDurationCount: 2, manifestLoadingMaxRetry: 30,
                          manifestLoadingRetryDelay: 2000, levelLoadingMaxRetry: 30 });
      dvr.hls.loadSource(src);
      dvr.hls.attachMedia(dvrVideo);
    } else if (dvrVideo.canPlayType("application/vnd.apple.mpegurl")) {
      dvrVideo.src = src;
    } else {
      $("#dvr-msg").textContent = "This browser can't play the recording. Try Chrome or Edge.";
    }
  } catch (err) {
    $("#dvr-msg").textContent = err.message;
  }
}

function dvrRender(job) {
  const live = job.live;
  if (!live || !live.manual) { if (dvr.jobId) dvrClose(); return; }
  if (live.chat_only) {  // the hype clipper's own job: no player or manual cutting, only its section
    $("#dvr").hidden = true;
    dvrCuts(live.cuts || []);
    chatRender(live);
    return;
  }
  $("#dvr").hidden = false;
  if (dvr.jobId !== job.id) {
    if (live.recorded > 5) dvrOpen(job);
    else $("#dvr-msg").textContent = "Starting the recording. The player appears in a few seconds.";
  }
  $("#dvr-total").textContent = tc(live.recorded);
  $("#go-live").hidden = !(live.recording && job.status === "running");
  dvrCuts(live.cuts || []);
  chatRender(live);
}

async function dvrCuts(cuts) {
  const mine = cuts.filter((c) => c.by !== "chat"), chats = cuts.filter((c) => c.by === "chat");
  $("#cuts-box").hidden = !mine.length;
  if (!cuts.length) return;
  // how far each clip has got: from the job list, at most every 4 s
  if (Date.now() - dvr.lastStatusFetch > 4000) {
    dvr.lastStatusFetch = Date.now();
    try {
      for (const j of await api("/api/jobs")) dvr.statuses[j.id] = j;
    } catch { /* keep the old statuses */ }
  }
  cutRows($("#cuts"), mine, "Clip");
  cutRows($("#chat-clips-list"), chats, "Chat clip");
}

function cutRows(ol, cuts, label) {
  ol.innerHTML = "";
  for (const c of cuts.slice().reverse()) {
    const j = dvr.statuses[c.job] || {};
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = `/#job=${c.job}`;
    a.textContent = `${label} ${c.n}: ${tc(c.start)} to ${tc(c.end)}`;
    a.addEventListener("click", (e) => { e.preventDefault(); openJob(c.job); });
    const st = document.createElement("span");
    const status = j.status || "queued";
    st.className = `st ${status}`;
    st.textContent = status === "done" ? "ready: captions and title added, open it to upload"
      : status === "failed" ? "failed" : status === "running" ? (j.message || "adding captions and title")
      : "waiting its turn";
    li.append(a, st);
    if (c.note) {
      const note = document.createElement("span");
      note.className = "note";
      note.textContent = c.note.charAt(0).toUpperCase() + c.note.slice(1);
      li.appendChild(note);
    }
    ol.appendChild(li);
  }
}

// the chat hype clipper's panel: how busy chat is along the recording, and what it clipped
function chatRender(live) {
  const ch = live && live.chat;
  $("#chatbox").hidden = !ch;
  if (!ch) return;
  const total = Math.max(live.recorded || 0, 1);
  const n = (ch.spikes || []).filter((x) => x.job).length;
  const st = $("#chat-state");
  if (!ch.site) st.textContent = ch.note;
  else if (ch.on) st.innerHTML = `<span class="on">Watching ${ch.site === "twitch" ? "Twitch" : "YouTube"} chat</span>: `
    + `${ch.messages.toLocaleString()} messages, usually ${ch.normal} per 5 s. ${n} chat clip${n === 1 ? "" : "s"} so far.`;
  else st.textContent = `Chat clipper finished: ${ch.messages.toLocaleString()} messages read, ${n} chat clip${n === 1 ? "" : "s"}.`;
  if (ch.messages && ch.messages < 50 && ch.on) st.textContent += " Waiting for a minute of chat to learn what normal is.";
  const svg = $("#chat-svg");
  const pts = ch.series || [];
  const top = Math.max(5, ...pts.map((p) => p[1]));
  const x = (t) => (1000 * t) / total, y = (r) => 100 - (92 * r) / top;
  let area = "";
  if (pts.length) area = `M${x(pts[0][0])},100 ` + pts.map((p) => `L${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(" ")
    + ` L${x(pts[pts.length - 1][0])},100 Z`;
  const spikes = (ch.spikes || []).map((sp) => `<rect class="spike" x="${x(sp.start)}" y="0" width="${Math.max(2, x(sp.end) - x(sp.start))}" height="100"><title>${tc(sp.start)} to ${tc(sp.end)}: ${sp.peak_rate} messages in 5 s</title></rect>`).join("");
  svg.innerHTML = `<path class="area" d="${area}"/>${spikes}`;
  $("#chat-end").textContent = tc(total);
}

// clips cut from a live recording that are still getting their captions: keep the page updating
function cutsBusy(job) {
  const cuts = (job.live && job.live.manual && job.live.cuts) || [];
  return cuts.some((c) => !["done", "failed"].includes((dvr.statuses[c.job] || {}).status));
}

function dvrMarks() {
  $("#in-time").textContent = dvr.inT == null ? "not set" : tc(dvr.inT);
  $("#out-time").textContent = dvr.outT == null ? "not set" : tc(dvr.outT);
  $("#cut").disabled = !(dvr.inT != null && dvr.outT != null && dvr.outT - dvr.inT >= 3);
}

async function dvrCut(body) {
  $("#dvr-msg").textContent = "Cutting the clip…";
  try {
    const child = await api(`/api/jobs/${dvr.jobId}/cut`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    $("#dvr-msg").textContent = `Cut “${child.name}”. Captions and a title are being added; it appears under Your clips.`;
    dvr.inT = dvr.outT = null;
    dvrMarks();
    dvr.lastStatusFetch = 0;
    clearTimeout(state.timer);
    poll();  // show it under Your clips now, and keep the list updating
  } catch (err) {
    $("#dvr-msg").textContent = err.message;
  }
}

dvrVideo.addEventListener("timeupdate", () => { $("#dvr-time").textContent = tc(dvrVideo.currentTime); });
$("#mark-in").addEventListener("click", () => {
  dvr.inT = dvrVideo.currentTime;
  if (dvr.outT != null && dvr.outT <= dvr.inT) dvr.outT = null;
  dvrMarks();
});
$("#mark-out").addEventListener("click", () => {
  dvr.outT = dvrVideo.currentTime;
  if (dvr.inT != null && dvr.inT >= dvr.outT) dvr.inT = null;
  dvrMarks();
});
$("#cut").addEventListener("click", () => dvrCut({ start: dvr.inT, end: dvr.outT }));
document.querySelectorAll("#dvr [data-last]").forEach((b) => b.addEventListener("click", () => {
  // the stretch you just watched, ending where the player is now
  const end = dvrVideo.currentTime, len = Number(b.dataset.last);
  if (!end) { $("#dvr-msg").textContent = "Play the stream first."; return; }
  dvrCut({ start: Math.max(0, end - len), end: end + 2 });
}));
$("#go-live").addEventListener("click", () => {
  const r = dvrVideo.seekable;
  if (r.length) dvrVideo.currentTime = Math.max(0, r.end(r.length - 1) - 3);
  dvrVideo.play().catch(() => {});
});
document.addEventListener("keydown", (e) => {
  if ($("#dvr").hidden || document.querySelector("dialog[open]") || e.ctrlKey || e.metaKey || e.altKey) return;
  if (/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "")) return;
  const k = e.key.toLowerCase();
  if (k === "i") $("#mark-in").click();
  else if (k === "o") $("#mark-out").click();
  else if (k === "c" && !$("#cut").disabled) $("#cut").click();
  else return;
  e.preventDefault();
});

window.addEventListener("hashchange", () => {  // a pasted or edited #job= link opens that job
  const m = location.hash.match(/job=([\w-]+)/);
  if (m && m[1] !== state.jobId) openJob(m[1]);
});


// ------------------------------------------------------------------ adjust a clip by hand
const ed = { jobId: null, index: -1, start: 0, end: 0, dur: 0, lines: [], linesFrom: 0, linesTo: 0 };
const edVideo = $("#ed-video");

function tcf(sec) {  // 1:05.4 (tenths, for cut points)
  sec = Math.max(0, sec || 0);
  const whole = Math.floor(sec);
  return `${tc(whole)}.${Math.floor((sec - whole) * 10)}`;
}

async function openEditor(job, index) {
  const c = job.clips[index];
  Object.assign(ed, { jobId: job.id, index, start: c.start, end: c.end,
                      dur: (job.video && job.video.duration) || c.end + 120, lines: [] });
  $("#ed-heading").textContent = `Adjust clip ${index + 1}`;
  $("#ed-title").value = c.title || "";
  $("#ed-hook").value = c.hook || "";
  $("#ed-error").textContent = "";
  $("#ed-save").disabled = false;
  edVideo.src = `/api/jobs/${job.id}/original#t=${c.start.toFixed(2)}`;
  $("#ed").showModal();
  await edLoadLines();
  edDraw();
}

async function edLoadLines() {
  const from = Math.max(0, ed.start - 90), to = ed.end + 90;
  if (ed.lines.length && from >= ed.linesFrom && to <= ed.linesTo) return;
  try {
    const res = await api(`/api/jobs/${ed.jobId}/lines?from=${from - 60}&to=${to + 60}`);
    Object.assign(ed, { lines: res.lines, linesFrom: from - 60, linesTo: to + 60 });
  } catch { ed.lines = []; }
}

function edWindow() {
  const pad = Math.max(30, (ed.end - ed.start) * 0.5);
  return [Math.max(0, ed.start - pad), Math.min(ed.dur, ed.end + pad)];
}

function edDraw() {
  $("#ed-start").textContent = tcf(ed.start);
  $("#ed-end").textContent = tcf(ed.end);
  const len = ed.end - ed.start;
  $("#ed-len").textContent = `${len.toFixed(1)} s long` + (len < 3 ? ": too short" : len > 600 ? ": too long (10 min at most)" : "");
  $("#ed-save").disabled = len < 3 || len > 600;
  const [a, b] = edWindow();
  const pct = (t) => `${(100 * (t - a)) / (b - a)}%`;
  $("#ed-range").style.left = pct(ed.start);
  $("#ed-range").style.width = `${(100 * len) / (b - a)}%`;
  $("#ed-win-a").textContent = tc(a);
  $("#ed-win-b").textContent = tc(b);
  edHead();
  edList($("#ed-lines-start"), ed.start - 40, ed.start + 15, "start");
  edList($("#ed-lines-end"), ed.end - 20, ed.end + 40, "end");
}

function edHead() {
  const [a, b] = edWindow();
  const t = edVideo.currentTime || 0;
  $("#ed-head").style.left = `${Math.max(0, Math.min(100, (100 * (t - a)) / (b - a)))}%`;
  $("#ed-now").textContent = `player at ${tcf(t)}`;
}

function edList(ol, from, to, which) {
  ol.innerHTML = "";
  const rows = ed.lines.filter((l) => l.end >= from && l.start <= to);
  if (!rows.length) {
    const li = document.createElement("li");
    li.className = "ed-hint";
    li.textContent = "No speech here. Use the buttons above.";
    ol.appendChild(li);
    return;
  }
  let edge = null;
  for (const l of rows) {
    const li = document.createElement("li");
    const inside = l.start >= ed.start - 0.3 && l.end <= ed.end + 0.3;
    li.className = inside ? "in" : "";
    const isEdge = which === "start" ? Math.abs(l.start - ed.start) < 0.6 : Math.abs(l.end - ed.end) < 0.8;
    if (isEdge) { li.classList.add("edge"); edge = li; }
    const b = document.createElement("button");
    b.type = "button";
    const time = document.createElement("time");
    time.textContent = tc(which === "start" ? l.start : l.end);
    const span = document.createElement("span");
    span.textContent = l.text;
    b.append(time, span);
    b.addEventListener("click", () => {
      if (which === "start") ed.start = Math.max(0, l.start - 0.15);
      else ed.end = Math.min(ed.dur, l.end + 0.35);  // keep the last word's tail
      edSeek(which === "start" ? ed.start : Math.max(ed.start, ed.end - 4));
      edDraw();
    });
    li.appendChild(b);
    ol.appendChild(li);
  }
  if (edge) edge.scrollIntoView({ block: "nearest" });
}

function edSeek(t) {
  edVideo.currentTime = Math.max(0, t);
}

$("#ed").addEventListener("click", (e) => {
  const step = e.target.closest("[data-step]");
  if (!step) return;
  const which = step.parentElement.dataset.which;
  ed[which] = Math.max(0, Math.min(ed.dur, ed[which] + Number(step.dataset.step)));
  edSeek(which === "start" ? ed.start : Math.max(ed.start, ed.end - 3));
  edLoadLines().then(edDraw);
});
$("#ed-set-start").addEventListener("click", () => { ed.start = edVideo.currentTime; edLoadLines().then(edDraw); });
$("#ed-set-end").addEventListener("click", () => { ed.end = edVideo.currentTime; edLoadLines().then(edDraw); });
$("#ed-track").addEventListener("click", (e) => {
  const r = e.currentTarget.getBoundingClientRect(), [a, b] = edWindow();
  edSeek(a + ((e.clientX - r.left) / r.width) * (b - a));
});
let edStopAt = null;
$("#ed-play").addEventListener("click", () => {
  edStopAt = ed.end;
  edSeek(ed.start);
  edVideo.play().catch(() => {});
});
edVideo.addEventListener("timeupdate", () => {
  edHead();
  if (edStopAt != null && edVideo.currentTime >= edStopAt) { edVideo.pause(); edStopAt = null; }
});
edVideo.addEventListener("seeking", () => { if (edStopAt != null && edVideo.currentTime < ed.start - 1) edStopAt = null; });
$("#ed-cancel").addEventListener("click", () => $("#ed").close());
$("#ed").addEventListener("close", () => { edVideo.pause(); edVideo.removeAttribute("src"); edVideo.load(); });
$("#ed-save").addEventListener("click", async () => {
  $("#ed-error").textContent = "";
  $("#ed-save").disabled = true;
  try {
    await api(`/api/jobs/${ed.jobId}/clips/${ed.index}/edit`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ start: ed.start, end: ed.end, title: $("#ed-title").value, hook: $("#ed-hook").value }),
    });
    $("#ed").close();
    clearTimeout(state.timer);
    poll();
  } catch (err) {
    $("#ed-error").textContent = err.message;
    $("#ed-save").disabled = false;
  }
});

boot();  // last: everything above must exist before the first job opens
