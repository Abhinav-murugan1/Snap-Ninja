"use strict";

(() => {
  const field = document.querySelector("#output-dir");
  const display = document.querySelector("#output-display");
  function refresh() { display.textContent = field.value || "App folder (jobs)"; }
  try { field.value = localStorage.getItem("snap-ninja:output") || ""; } catch { /* use app folder */ }
  refresh();
  document.body.insertAdjacentHTML("beforeend", `
    <dialog id="output-dialog" class="yt" aria-labelledby="output-heading">
      <form method="dialog">
        <h2 id="output-heading">Output folder</h2>
        <p class="note">Choose where new jobs save their clips. Each job gets its own subfolder. Existing jobs keep their current locations.</p>
        <label for="output-path">Folder path</label>
        <div class="output-path-row"><input id="output-path" type="text" placeholder="D:\\Snap Ninja exports" spellcheck="false" autocomplete="off"><button id="output-browse" type="button" class="ghost">Browse…</button></div>
        <p class="note">Leave blank to use the app folder. A folder you type here will be created when you save. Original videos and working files stay in the app folder. Clips saved outside the app are kept when you delete a job.</p>
        <p id="output-error" class="error" role="alert"></p>
        <p id="output-status" class="note" role="status"></p>
        <div class="yt-buttons"><button id="output-default" type="button" class="link">Use app folder</button><button id="output-cancel" type="button" class="ghost">Cancel</button><button id="output-save" type="submit" class="primary">Save folder</button></div>
      </form>
    </dialog>`);
  const dialog = document.querySelector("#output-dialog");
  const input = dialog.querySelector("#output-path");
  const error = dialog.querySelector("#output-error");
  const message = dialog.querySelector("#output-status");
  let busy = false;
  function setBusy(value) {
    busy = value;
    dialog.querySelectorAll("button, input").forEach(el => { el.disabled = value; });
  }
  async function post(url, body = {}) {
    const res = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json", "X-Snap-Ninja": "1"}, body: JSON.stringify(body)});
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Could not change the output folder.");
    return data;
  }
  document.querySelector("#output-change").addEventListener("click", () => {
    input.value = field.value;
    error.textContent = "";
    message.textContent = "";
    dialog.showModal();
    input.focus();
  });
  dialog.addEventListener("cancel", event => { if (busy) event.preventDefault(); });
  dialog.querySelector("#output-cancel").addEventListener("click", () => dialog.close());
  dialog.querySelector("#output-default").addEventListener("click", () => { input.value = ""; input.focus(); });
  dialog.querySelector("#output-browse").addEventListener("click", async () => {
    setBusy(true);
    error.textContent = "";
    message.textContent = "Choose a folder in the system dialog. If it is behind the browser, check your taskbar.";
    try {
      const result = await post("/api/output/browse");
      if (!result.cancelled) input.value = result.path;
      message.textContent = result.cancelled ? "Folder selection cancelled." : "Folder selected. Save to use it for new jobs.";
    } catch (err) { error.textContent = err.message; message.textContent = ""; }
    finally { setBusy(false); input.focus(); }
  });
  dialog.querySelector("form").addEventListener("submit", async event => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    error.textContent = "";
    message.textContent = "Checking folder access…";
    try {
      const result = await post("/api/output/validate", {path: input.value});
      field.value = result.path;
      try { localStorage.setItem("snap-ninja:output", result.path); } catch { /* current session still works */ }
      refresh();
      dialog.close();
    } catch (err) { error.textContent = err.message; message.textContent = ""; }
    finally { setBusy(false); }
  });
})();
