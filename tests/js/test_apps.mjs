// App picker helpers (viewer/apps.js). Run: node --test tests/js/test_apps.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const apps = createRequire(import.meta.url)("../../viewer/apps.js");

test("ago words", () => {
  const now = Date.parse("2026-10-29T12:00:00Z");
  assert.equal(apps.ago("2026-10-29T11:48:00Z", now), "12 min ago");
  assert.equal(apps.ago("2026-10-29T11:59:50Z", now), "just now");
  assert.equal(apps.ago("2026-10-29T09:00:00Z", now), "3 h ago");
  assert.equal(apps.ago("2026-10-27T12:00:00Z", now), "2 d ago");
  assert.equal(apps.ago(null, now), "never scanned");
});

test("option text shows name, last scan and size", () => {
  const now = Date.parse("2026-10-29T12:00:00Z");
  assert.equal(apps.optionText({ name: "mygame", scannedAt: "2026-10-29T11:48:00Z", nodes: 340 }, now), "mygame · 12 min ago · 340 classes");
  assert.equal(apps.optionText({ name: "x", scanning: true, nodes: null }, now), "x · refreshing…");
});

test("chooseApp: hash, then serve's app, then last used, then first", () => {
  const names = ["a", "b", "c"];
  assert.equal(apps.chooseApp(names, { hash: "c", initial: "b", last: "a" }), "c");
  assert.equal(apps.chooseApp(names, { hash: "", initial: "b", last: "a" }), "b");
  assert.equal(apps.chooseApp(names, { hash: "", initial: null, last: "c" }), "c");
  assert.equal(apps.chooseApp(names, { hash: "zz", initial: null, last: "gone" }), "a");
  assert.equal(apps.chooseApp([], {}), null);
});

test("stepApp wraps", () => {
  assert.equal(apps.stepApp(["a", "b", "c"], "c", 1), "a");
  assert.equal(apps.stepApp(["a", "b", "c"], "a", -1), "c");
  assert.equal(apps.stepApp(["a"], "a", 1), "a");
});

test("apiBase: everything goes to the local helper on the hosted page, else to this server", () => {
  assert.equal(apps.apiBase(false, null), "");                       // local serve: same server
  assert.equal(apps.apiBase(true, "http://127.0.0.1:8517"), "http://127.0.0.1:8517");
  assert.equal(apps.apiBase(true, null), null);                      // hosted, no helper: nothing loads
});

test("url adds the app and the base to every API path, reads and writes alike", () => {
  Object.assign(apps, { current: "mygame", hosted: true, helper: "http://127.0.0.1:8517" });
  assert.equal(apps.url("/api/notes"), "http://127.0.0.1:8517/api/notes?app=mygame");
  assert.equal(apps.url("/api/view?level=2"), "http://127.0.0.1:8517/api/view?level=2&app=mygame");
  assert.equal(apps.url("/api/apps"), "http://127.0.0.1:8517/api/apps");
  assert.equal(apps.url("/api/tours/t/audio/k?app=other"), "http://127.0.0.1:8517/api/tours/t/audio/k?app=other");
  Object.assign(apps, { hosted: false, helper: null });
  assert.equal(apps.url("/api/notes"), "/api/notes?app=mygame");
  Object.assign(apps, { current: null });
});

test("empty page: start-the-helper how-to on the hosted page, else how to add an app", () => {
  Object.assign(apps, { hosted: true, helper: null });
  const html = apps.emptyHtml();
  assert.match(html, /Open this page on your PC with Coderstanding running/);
  assert.match(html, /<a class="btn" href="coderstanding:\/\/start" data-act="start-helper">Start helper<\/a>/);
  assert.match(html, /coderstanding helper install/);
  assert.match(html, /scripts\/install-helper\.sh/);
  Object.assign(apps, { hosted: false });
  assert.match(apps.emptyHtml(), /No projects registered.*&lt;name&gt;/);
  Object.assign(apps, { lost: true });  // a local page whose helper went away
  assert.match(apps.emptyHtml(), /Start helper/);
  Object.assign(apps, { lost: false });
});

test("Start helper: polls about every second, loads when the helper answers, gives up after ~15 s", async () => {
  const status = { textContent: "" };
  const reloads = [];
  globalThis.location = { reload: () => reloads.push(1) };
  let probes = 0, slept = 0;
  const up = await apps.waitForStart(status, async (ms) => { slept += ms; }, async () => ++probes === 3);
  assert.equal(up, true);
  assert.deepEqual([probes, slept, reloads.length], [3, 2000, 1]);
  probes = 0; slept = 0;
  assert.equal(await apps.waitForStart(status, async (ms) => { slept += ms; }, async () => { probes++; return false; }), false);
  assert.equal(probes, 15);
  assert.match(status.textContent, /Nothing answered/);
  delete globalThis.location;
});

test("app manager: sizes and the facts line", () => {
  assert.equal(apps.bytesText(999), "999 B");
  assert.equal(apps.bytesText(3_200_000), "3.2 MB");
  assert.equal(apps.bytesText(null), "");
  const now = Date.parse("2026-10-29T12:00:00Z");
  assert.equal(apps.factsText({ scannedAt: "2026-10-29T11:48:00Z", scannerVersion: 7, staleScanner: true, nodes: 3, edges: 4,
    graphBytes: 12_000, notes: 2, tours: 1 }, now), "12 min ago · scanner 7 (old, refresh) · 3 classes, 4 connections · graph.json 12 kB · 2 notes · 1 tours");
  assert.equal(apps.factsText({ scannedAt: null, nodes: null, notes: 0, tours: 0 }, now), "never scanned · 0 notes · 0 tours");
});

test("editor and web links", () => {
  assert.equal(apps.editorUrl("/home/me/My Repo", "src/a b.py", 12), "vscode://file/home/me/My%20Repo/src/a%20b.py:12");
  assert.equal(apps.editorUrl("/r", "a.py", 0), "vscode://file/r/a.py:1");
  assert.equal(apps.webUrl("https://f.org/o/r/src/commit/abc", "src/a b.py", 12), "https://f.org/o/r/src/commit/abc/src/a%20b.py#L12");
  assert.equal(apps.webUrl(null, "a.py", 3), null);
});
