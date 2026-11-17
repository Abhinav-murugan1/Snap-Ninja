"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const {youtubeConnectionState} = require("../static/youtube-guide.js");

test("new user is asked for the JSON file before sign-in", () => {
  const view = youtubeConnectionState({has_client: false, connected: false, connecting: false});
  assert.equal(view.needsFile, true);
  assert.equal(view.canConnect, false);
  assert.equal(view.connecting, false);
});
test("imported client enables Google sign-in", () => {
  const view = youtubeConnectionState({has_client: true, connected: false, connecting: false});
  assert.equal(view.needsFile, false);
  assert.equal(view.canConnect, true);
});
test("pending consent hides duplicate sign-in and enables status polling", () => {
  const view = youtubeConnectionState({has_client: true, connected: false, connecting: true});
  assert.equal(view.canConnect, false);
  assert.equal(view.connecting, true);
});
test("connected channel is shown as text without sign-in or polling", () => {
  const view = youtubeConnectionState({has_client: true, connected: true, channel: "My channel"});
  assert.match(view.message, /My channel/);
  assert.equal(view.canConnect, false);
  assert.equal(view.needsFile, false);
  assert.equal(view.connecting, false);
});
test("expired sign-in retains the client and offers reconnection", () => {
  const view = youtubeConnectionState({has_client: true, connected: false, error: "Sign-in expired"});
  assert.equal(view.needsFile, false);
  assert.equal(view.canConnect, true);
});
