"use strict";

function youtubeConnectionState(status) {
  if (status.connected) return {
    message: `Connected to ${status.channel || "your YouTube channel"}. Uploads are ready.`,
    needsFile: false, canConnect: false, connecting: false,
  };
  if (status.connecting) return {
    message: "Finish Google sign-in in your browser, then return here. Waiting for permission…",
    needsFile: false, canConnect: false, connecting: true,
  };
  return {
    message: status.has_client ? "Client file added. Sign in with the account that owns your channel."
      : "Add the Desktop app JSON file from step 4, then sign in with Google.",
    needsFile: !status.has_client, canConnect: Boolean(status.has_client), connecting: false,
  };
}

// The same instructions appear in onboarding and next to the upload controls.
const youtubeGuideContent = `
  <p class="youtube-intro">Connect your own Google Cloud project once to upload from Snap Ninja. You need a Google account with a YouTube channel, a Desktop app OAuth file, and permission to upload. A plain API key cannot authorize uploads.</p>
  <ol class="youtube-guide-steps">
    <li><h4>Create a Google Cloud project</h4>
      <p>Open the <a href="https://console.cloud.google.com/" target="_blank" rel="noopener noreferrer">Google Cloud console</a>, sign in, and use the project selector at the top to create a project. For example, name it <strong>Snap Ninja uploads</strong>. Keep this project selected throughout the setup.</p>
    </li>
    <li><h4>Enable the YouTube API</h4>
      <p>Open <strong>APIs &amp; Services → Library</strong>, search for <strong>YouTube Data API v3</strong>, and select <strong>Enable</strong>. You can also <a href="https://console.cloud.google.com/apis/library/youtube.googleapis.com" target="_blank" rel="noopener noreferrer">open its API page</a> directly.</p>
    </li>
    <li><h4>Configure who can sign in</h4>
      <p>Open <strong>Google Auth platform → Branding</strong> (or <strong>OAuth consent screen</strong>). If prompted, choose <strong>Get started</strong>. Enter an app name, support email and contact email. Review Google's terms yourself before completing the form.</p>
      <p>For a personal Google account, choose <strong>External</strong>. In <strong>Audience</strong>, keep the project in <strong>Testing</strong> and add the Google account that owns your YouTube channel under <strong>Test users</strong>.</p>
      <p>In <strong>Data Access → Add or remove scopes</strong>, add and save the two permissions Snap Ninja requests:</p>
      <dl class="youtube-scopes">
        <div><dt><code>https://www.googleapis.com/auth/youtube.upload</code></dt><dd>Upload videos to your channel.</dd></div>
        <div><dt><code>https://www.googleapis.com/auth/youtube.readonly</code></dt><dd>Read channel details so you can confirm the destination.</dd></div>
      </dl>
    </li>
    <li><h4>Download a Desktop app client file</h4>
      <p>Open <strong>Google Auth platform → Clients → Create client</strong>. In the older console layout, use <strong>APIs &amp; Services → Credentials → Create credentials → OAuth client ID</strong>.</p>
      <p>Choose <strong>Desktop app</strong>, enter a name, and create the client. Download its <strong>JSON</strong> file. Do not choose Web application, API key, or service account. Desktop clients use the local callback automatically; there is no website redirect URL to enter.</p>
    </li>
    <li><h4>Add the file and connect your channel</h4>
      <p>Use the <strong>Google OAuth client file (.json)</strong> control in Snap Ninja to select the downloaded file. It can keep its original filename. Then choose <strong>Sign in with Google</strong>.</p>
      <p>In your browser, choose the account and channel you want to use and review the requested access. Snap Ninja never asks for your Google password. Return here after Google finishes; Snap Ninja shows the connected channel.</p>
    </li>
    <li><h4>Review your first upload</h4>
      <p>Open <strong>Upload to YouTube</strong> on a finished clip. Check the channel, clip format, title, description and visibility. Choose <strong>Private</strong> for a first test, then press <strong>Upload to YouTube</strong> when you are ready. Connecting your account does not upload a video.</p>
    </li>
  </ol>
  <details class="youtube-troubleshooting"><summary>Connection problems and account limits</summary>
    <dl>
      <div><dt>Access blocked or test user missing</dt><dd>Check that the account you selected is listed under Audience → Test users in the same Cloud project. Work or school accounts may require administrator approval.</dd></div>
      <div><dt>Google shows an unverified-app notice</dt><dd>Check the app name and project owner. For a project you created for personal testing, review Google's testing guidance before deciding whether to grant access. Never continue for an unfamiliar app.</dd></div>
      <div><dt>Wrong client type or redirect mismatch</dt><dd>Create a new Desktop app client in the same project and download its JSON. A Web application client does not work with this desktop sign-in flow.</dd></div>
      <div><dt>Sign-in expires after a week</dt><dd>External projects in Testing normally issue refresh tokens that expire after seven days for these permissions. Sign in again. Production use has separate Google verification requirements.</dd></div>
      <div><dt>API disabled or quota exceeded</dt><dd>Confirm YouTube Data API v3 is enabled in the client's project. Check its Quotas page for the current allowance and reset time; channel upload limits can also apply.</dd></div>
      <div><dt>Wrong channel or unexpected visibility</dt><dd>Use “Use a different channel” in the upload dialog to reconnect. Confirm the channel name before uploading and inspect the resulting video in YouTube Studio.</dd></div>
    </dl>
  </details>
  <p class="note">The client file and saved sign-in stay in this computer's <code>youtube/</code> folder. Keep them out of public repositories and screenshots. Google handles authorization; an upload sends the selected video and its details to YouTube.</p>
  <p class="youtube-sources">Official instructions: <a href="https://developers.google.com/youtube/data/guides/authentication" target="_blank" rel="noopener noreferrer">YouTube authorization</a>, <a href="https://developers.google.com/workspace/guides/configure-oauth-consent" target="_blank" rel="noopener noreferrer">consent configuration</a>, <a href="https://developers.google.com/youtube/data/guides/uploading_a_video" target="_blank" rel="noopener noreferrer">video uploads</a>. Google may rename console menus.</p>`;

if (typeof document !== "undefined") {
  document.querySelectorAll("[data-youtube-guide]").forEach(container => {
    container.innerHTML = youtubeGuideContent;
    if (container.dataset.youtubeGuide !== "connect") return;
    container.insertAdjacentHTML("beforeend", `
      <section class="youtube-connect" aria-label="Connect a YouTube channel">
        <h4>Connect here</h4>
        <p class="youtube-connect-state" role="status" aria-live="polite">Open this section to check your connection.</p>
        <label class="file-pick youtube-client-label" hidden>Google OAuth client file (.json)<input class="youtube-client-file" type="file" accept=".json,application/json"></label>
        <div class="youtube-connect-buttons"><button type="button" class="primary youtube-connect-button" hidden>Sign in with Google</button><button type="button" class="ghost small youtube-check-button">Check connection</button></div>
        <p class="error youtube-connect-error" role="alert"></p>
      </section>`);
    const find = s => container.querySelector(s);
    const details = container.closest("details");
    const dialog = container.closest("dialog");
    let timer = null, busy = false;
    const isVisible = () => details.open && (!dialog || dialog.open);
    async function refresh() {
      clearTimeout(timer);
      if (busy) return;
      busy = true;
      find(".youtube-check-button").disabled = true;
      try {
        const status = await api("/api/youtube/status");
        const view = youtubeConnectionState(status);
        find(".youtube-connect-state").textContent = view.message;
        find(".youtube-client-label").hidden = !view.needsFile;
        find(".youtube-connect-button").hidden = !view.canConnect;
        find(".youtube-connect-error").textContent = status.error || "";
        if (view.connecting && isVisible()) timer = setTimeout(refresh, 2000);
      } catch (err) { find(".youtube-connect-error").textContent = err.message; }
      finally { busy = false; find(".youtube-check-button").disabled = false; }
    }
    details.addEventListener("toggle", () => { if (isVisible()) refresh(); else clearTimeout(timer); });
    dialog?.addEventListener("close", () => clearTimeout(timer));
    find(".youtube-check-button").addEventListener("click", refresh);
    find(".youtube-client-file").addEventListener("change", async event => {
      const file = event.target.files[0];
      if (!file) return;
      event.target.disabled = true;
      find(".youtube-connect-error").textContent = "";
      try {
        await api("/api/youtube/client", {method: "POST", body: file});
        await refresh();
      } catch (err) { find(".youtube-connect-error").textContent = err.message; }
      finally { event.target.value = ""; event.target.disabled = false; }
    });
    find(".youtube-connect-button").addEventListener("click", async event => {
      event.target.disabled = true;
      find(".youtube-connect-error").textContent = "";
      try { await api("/api/youtube/connect", {method: "POST"}); await refresh(); }
      catch (err) { find(".youtube-connect-error").textContent = err.message; }
      finally { event.target.disabled = false; }
    });
  });
}

if (typeof module !== "undefined" && module.exports) module.exports = {youtubeConnectionState};
