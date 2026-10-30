// End-to-end viewer checks in real headless Chromium (DevTools protocol, real mouse and keys).
// Run: node tests/e2e/run.mjs [--keep] [--shots DIR] [--app REPO --app-data DIR]
// Starts its own server on a spare port against tests/e2e/fixture with a throw-away data folder.
// --app also checks the layout of a real app (read-only; --app-data must be a scratch folder, it
// gets graph.json/cache): readable first zoom, no overlapping boxes, short bends.
// Needs Node 22 and `chromium-browser` (or CHROME=path). Failure screenshots go to --shots
// (default ~/Downloads/coderstanding-wip, as qa-fail-<step>.png).
import { spawn, execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, existsSync, mkdirSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { createServer as createHttpServer } from "node:http";
import { homedir, tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { launch, sleep } from "./cdp.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, "../..");
const cli = join(repo, "bin", "coderstanding");
const fixture = join(here, "fixture");
const args = process.argv.slice(2);
const shots = args.includes("--shots") ? args[args.indexOf("--shots") + 1] : join(homedir(), "Downloads", "coderstanding-wip");
const data = mkdtempSync(join(tmpdir(), "coderstanding-e2e-data-"));
const results = [];
// never the real registry or data home
const isolated = { CODERSTANDING_REGISTRY: join(data, "apps.json"), CODERSTANDING_DATA_HOME: join(data, "share") };

function freePort() {
  return new Promise((ok) => {
    const server = createServer();
    server.listen(0, "127.0.0.1", () => { const { port } = server.address(); server.close(() => ok(port)); });
  });
}

/** The hosted shell as the lab serves it: viewer/ as static files, hosted.json naming `helper`. */
function startShell(port, helper) {
  const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".svg": "image/svg+xml" };
  const server = createHttpServer((request, response) => {
    const path = new URL(request.url, "http://x").pathname.replace(/^\/$/, "/index.html");
    if (path === "/hosted.json") { response.writeHead(200, { "Content-Type": types[".json"] }); return response.end(JSON.stringify({ helper })); }
    const file = join(repo, "viewer", path);
    if (!file.startsWith(join(repo, "viewer")) || !existsSync(file)) { response.writeHead(404); return response.end(); }
    response.writeHead(200, { "Content-Type": types[path.slice(path.lastIndexOf("."))] || "application/octet-stream" });
    response.end(readFileSync(file));
  });
  return new Promise((ok) => server.listen(port, "127.0.0.1", () => ok(server)));
}

const option = (name) => (args.includes(name) ? args[args.indexOf(name) + 1] : null);

async function startServer(port, app = fixture, dataDir = data, serveArgs = ["serve", app, "--data", dataDir], env = isolated) {
  env = { ...process.env, ...env };  // never the real registry or data home
  const proc = spawn(cli, [...serveArgs, "--port", String(port)], { stdio: ["ignore", "ignore", "pipe"], env });
  let log = "";
  proc.stderr.on("data", (c) => { log += c; });
  for (let i = 0; i < 1200; i++) {  // a big app may rescan first
    try { if ((await fetch(`http://127.0.0.1:${port}/api/meta`)).ok) return proc; } catch { /* not up yet */ }
    await sleep(100);
  }
  proc.kill();
  throw new Error(`server did not start: ${log.slice(-500)}`);
}

/** Notes written so far: one JSON object per line. */
function notes() {
  const path = join(data, "notes.jsonl");
  return existsSync(path) ? readFileSync(path, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l)) : [];
}
const lastNote = (key, field) => notes().filter((n) => n.key === key && field in n).at(-1);

function assert(ok, message) {
  if (!ok) throw new Error(message);
}

// ---------- in-page helpers (client coordinates of graph things) ----------

const HELPERS = `
window.__qa = {
  node(name) {
    const n = cy.nodes().filter((m) => m.data("raw").name === name || m.data("name") === name)[0];
    if (!n) return null;
    const box = cy.container().getBoundingClientRect();
    const p = n.renderedPosition();
    return { id: n.id(), x: box.x + p.x, y: box.y + p.y, w: n.renderedWidth(), h: n.renderedHeight(),
      top: box.y + p.y - n.renderedHeight() / 2, open: n.hasClass("open") };
  },
  positions() {
    return Object.fromEntries(cy.nodes().map((n) => [n.id(), [Math.round(n.position().x), Math.round(n.position().y)]]));
  },
  edge(fromName, toName) {
    const e = cy.edges().filter((x) => x.source().data("raw").name === fromName && x.target().data("raw").name === toName && x.visible())[0];
    if (!e) return null;
    const box = cy.container().getBoundingClientRect();
    const m = e.renderedMidpoint();
    return { id: e.id(), x: box.x + m.x, y: box.y + m.y };
  },
  empty() {  // a spot on the graph with no class or line near it
    const box = cy.container().getBoundingClientRect();
    for (let y = box.y + 12; y < box.bottom - 12; y += 24) {
      for (let x = box.x + 12; x < box.right - 12; x += 24) {
        const near = cy.elements().some((el) => {
          const b = el.renderedBoundingBox();
          return x - box.x > b.x1 - 30 && x - box.x < b.x2 + 30 && y - box.y > b.y1 - 30 && y - box.y < b.y2 + 30;
        });
        if (!near) return { x, y };
      }
    }
    return { x: box.x + 5, y: box.y + 5 };
  },
  showing() { return document.getElementById("details").dataset.showing || ""; },
  settled() { return document.getElementById("overlay").hidden && cy.nodes().length > 0 && !cy.nodes().some((n) => n.animated()); },
};
true`;

// ---------- Tours: the fixture's tour, silent speech ----------

const fixtureTour = join(fixture, ".coderstanding", "tours", "first-steps.md");
/** The tour file as the server keeps it (in the data folder: serve runs with --data). */
const tourFile = () => readFileSync(join(data, "tours", "first-steps.md"), "utf8");

/** Every utterance speaks at volume 0 (no sound from the checks) and logs its events. */
const SILENT_SPEECH = `(() => {
  if (!window.speechSynthesis) return;
  window.__speech = [];
  const speak = speechSynthesis.speak.bind(speechSynthesis);
  speechSynthesis.speak = (u) => {
    u.volume = 0;
    for (const name of ["start", "boundary", "end", "error"]) u.addEventListener(name, (e) => __speech.push(name + (e.error ? ":" + e.error : "")));
    speak(u);
  };
})();`;

async function main() {
  mkdirSync(shots, { recursive: true });
  execFileSync(cli, ["set", fixture, "Player", "hint=AI wrote this", "--data", data]);  // an AI draft to accept
  mkdirSync(join(data, "tours"), { recursive: true });
  writeFileSync(join(data, "tours", "first-steps.md"), readFileSync(fixtureTour));
  const port = await freePort();
  const roPort = await freePort();  // the hosted shell step's page uses this server as its helper
  isolated.CODERSTANDING_ALLOW_ORIGIN = `http://127.0.0.1:${roPort}`;
  const server = await startServer(port);
  let base = `http://127.0.0.1:${port}/`;
  // --enable-speech-dispatcher: Chromium on Linux lists speech-dispatcher voices only with it;
  // fake microphone (a tone, never played: --mute-audio) for the tour recording check
  const { page, close } = await launch({ width: 1780, height: 1000,
    args: ["--enable-speech-dispatcher", "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"] });  // graph area ~1400x800
  await page.send("Page.addScriptToEvaluateOnNewDocument", { source: SILENT_SPEECH });
  const expectedErrors = [/favicon\.ico/];  // a step may add one it causes on purpose
  const errorsBefore = () => page.errors.filter((e) => !expectedErrors.some((pattern) => pattern.test(e)));

  async function open(query = "") {
    await page.goto(`${base}?${query.includes("theme=") ? "" : "theme=dark&"}${query}`);
    await page.eval(HELPERS);
    await page.waitFor("window.cy && document.getElementById('overlay').hidden && cy.nodes().length > 0", { what: "first view" });
    await settle();
  }
  async function settle(ms = 350) {
    await sleep(ms);
    await page.waitFor("window.__qa && __qa.settled()", { what: "view settled" });
  }
  const qa = (expr) => page.eval(`__qa.${expr}`);
  async function setLevel(key) {
    await page.clickOn("#level");
    await page.key(key);
    await settle();
  }
  async function step(name, fn) {
    const before = errorsBefore().length;
    try {
      await fn();
      const errs = errorsBefore().slice(before);
      assert(!errs.length, `console errors: ${errs.join(" | ")}`);
      results.push({ name, ok: true });
      console.log(`ok   ${name}`);
    } catch (error) {
      results.push({ name, ok: false, error: error.message });
      console.log(`FAIL ${name}: ${error.message}`);
      try { await page.screenshot(join(shots, `qa-fail-${name.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}.png`)); } catch { /* page gone */ }
      try { await open(); } catch { /* next step reports it */ }
    }
  }

  try {
    await open();

    await step("level slider", async () => {
      const count = () => page.eval("cy.nodes().length");
      await setLevel("Home");
      const low = await count();
      const label = await page.eval("document.getElementById('levelOut').textContent");
      await setLevel("End");
      assert(await count() > low, "more classes at the highest level");
      assert(await page.eval("document.getElementById('levelOut').textContent") !== label, "level label changes");
    });

    await step("All types / Concrete", async () => {
      await page.clickOn("#viewConcrete");
      await settle();
      assert(await page.eval("document.getElementById('viewConcrete').getAttribute('aria-pressed')") === "true", "Concrete pressed");
      await page.clickOn("#viewAll");
      await settle();
      assert(await page.eval("document.getElementById('viewAll').getAttribute('aria-pressed')") === "true", "All types pressed");
    });

    await step("Show toggles: Scenes, Outside code, Tests", async () => {
      const has = (name) => qa(`node(${JSON.stringify(name)})`).then(Boolean);
      assert(await has("main.tscn") && await has("requests"), "scene and outside code shown at first");
      assert(!(await has("test_player.gd")), "tests hidden at first");
      for (const [box, name, shown] of [["#showScene", "main.tscn", false], ["#showExternal", "requests", false], ["#showTest", "test_player.gd", true]]) {
        await page.clickOn(box);
        await settle();
        assert(await has(name) === shown, `${box} ${shown ? "shows" : "hides"} ${name}`);
        await page.clickOn(box);
        await settle();
        assert(await has(name) === !shown, `${box} back`);
      }
    });

    await step("Highlight menu", async () => {
      const options = await page.eval("[...document.getElementById('filter').options].map((o) => o.value)");
      assert(options.length > 1, `options: ${options}`);
      await page.clickOn("#filter");
      await page.key("Escape");
      await page.eval("document.getElementById('filter').focus()");
      await page.key("ArrowDown");
      await settle();
      assert(await page.eval("document.getElementById('filter').value") === options[1], "picked the second option");
      await page.eval("document.getElementById('filter').focus()");
      await page.key("ArrowUp");
      await settle();
      assert(await page.eval("cy.nodes('.dim').length") === 0, "All classes: nothing dimmed");
    });

    await step("hover shows, click keeps the details", async () => {
      const player = await qa('node("Player")');
      await page.move(player.x + 20, player.y + 4);
      await sleep(150);
      assert((await qa("showing()")).includes("Player"), "hover shows Player");
      const away = await qa("empty()");
      await page.move(away.x, away.y);
      await sleep(150);
      assert(!(await qa("showing()")), "moving away clears a hover");
      await page.click(player.x + 20, player.y + 4);
      await page.move(away.x, away.y);
      await sleep(200);
      assert((await qa("showing()")).includes("Player"), "click keeps Player");
    });

    await step("AI draft Accept", async () => {
      await page.clickOn('#details [data-act="accept"][data-name="hint"]');
      await settle();
      const note = lastNote("node:Player", "hint");
      assert(note && !note.ai && note.hint === "AI wrote this", `accepted: ${JSON.stringify(note)}`);
    });

    await step("notes: Enter saves, Esc reverts, Ctrl+Enter and blur save, status", async () => {
      const big = await qa('node("Big")');
      await page.click(big.x + 20, big.y + 4);
      await sleep(250);
      const fieldSel = (name) => `#details .note-input[data-note="${name}"]`;
      const zoomBefore = await page.eval("cy.zoom()");
      await page.clickOn(fieldSel("hint"));
      await page.type("counts steps");
      await page.key("Enter");
      await settle();
      assert(lastNote("node:Big", "hint")?.hint === "counts steps", "Enter saved the hint");
      assert(Math.abs(await page.eval("cy.zoom()") - zoomBefore) < 1e-6, "saving a note keeps the camera");
      await page.clickOn(fieldSel("hint"));
      await page.type(" and more");
      await page.key("Escape");
      await sleep(200);
      assert(await page.eval(`document.querySelector('${fieldSel("hint")}').value`) === "counts steps", "Esc reverted");
      assert(lastNote("node:Big", "hint")?.hint === "counts steps", "Esc saved nothing");
      await page.clickOn(fieldSel("description"));
      await page.type("A long list of steps.");
      await page.key("Enter", { ctrl: true });
      await settle();
      assert(lastNote("node:Big", "description")?.description === "A long list of steps.", "Ctrl+Enter saved");
      await page.clickOn(fieldSel("tags"));
      await page.type("core, e2e");
      const away = await qa("empty()");
      await page.click(away.x, away.y);  // blur
      await settle();
      assert(JSON.stringify(lastNote("node:Big", "tags")?.tags) === JSON.stringify(["core", "e2e"]), `blur saved tags: ${JSON.stringify(lastNote("node:Big", "tags"))}`);
      await page.click(big.x + 20, big.y + 4);
      await sleep(250);
      await page.clickOn(fieldSel("rules"));
      await page.type("Never block here");
      await page.key("Enter", { ctrl: true });
      await settle();
      assert(/Never block here/.test(JSON.stringify(lastNote("node:Big", "rules"))), "rules saved");
      assert(await page.eval(`(() => { const b = document.querySelector('#details dd[data-field="rules"]').previousElementSibling.querySelector('.help-btn'); b.click(); const t = document.getElementById(b.getAttribute('aria-describedby')); return getComputedStyle(t).display !== 'none' && /never/i.test(t.textContent); })()`), "? helper toggles its tip");
      assert(!(await page.eval(`!!document.querySelector('${fieldSel("review")}')`)), "empty Review hidden outside edit mode");
      await page.clickOn("#modeEdit");
      await sleep(250);
      await page.clickOn(fieldSel("review"));
      await page.type("Split into Net and Lobby");
      await page.key("Enter", { ctrl: true });
      await settle();
      assert(/Split into Net and Lobby/.test(JSON.stringify(lastNote("node:Big", "review"))), "review saved");
      await page.clickOn("#modeView");
      await sleep(250);
      assert(await page.eval(`!!document.querySelector('${fieldSel("review")}')`), "filled Review shows in View mode");
      await page.eval(`document.querySelector('${fieldSel("status")}').focus()`);
      await page.key("ArrowDown");
      await settle();
      assert(lastNote("node:Big", "status")?.status, "status saved");
    });

    await step("double-click a line zooms; breadcrumb back", async () => {
      await setLevel("End");
      const line = await qa('edge("Player", "Big")');
      assert(line, "a Player > Big line");
      await page.dblclick(line.x, line.y);
      await settle();
      assert(await page.eval("state.crumbs.length") === 1, "one breadcrumb");
      await page.clickOn('#crumbs [data-crumb="-1"]');
      await settle();
      assert(await page.eval("state.crumbs.length") === 0, "back to the whole app");
    });

    await step("double-click a class zooms; breadcrumb back", async () => {
      const player = await qa('node("Player")');
      await page.dblclick(player.x + 20, player.y + 4);
      await settle();
      assert(await page.eval("state.crumbs.length") === 1, "one breadcrumb");
      await page.clickOn('#crumbs [data-crumb="-1"]');
      await settle();
      assert(await page.eval("state.crumbs.length") === 0, "back");
    });

    await step("triangle opens and closes without moving any other box", async () => {
      const before = await qa("positions()");
      const bigId = (await qa('node("Big")')).id;
      await page.clickOn('#fanFront [aria-label="Expand Big"]');
      await settle();
      assert((await qa('node("Big")')).open, "Big is open");
      const opened = await qa("positions()");
      const moved = Object.keys(before).filter((id) => id !== bigId && String(before[id]) !== String(opened[id]));
      assert(!moved.length, `others moved on open: ${moved}`);
      await page.clickOn('#fanFront [aria-label="Collapse Big"]');
      await settle();
      assert(JSON.stringify(await qa("positions()")) === JSON.stringify(before), "collapse restores the picture");
    });

    await step("opening keeps View-mode moves and the Auto preview in place", async () => {
      const bigId = (await qa('node("Big")')).id;
      await page.eval("autoLayout()");  // a preview in View mode: nothing saved
      await sleep(600);
      await settle();
      const player = await qa('node("Player")');
      await page.drag({ x: player.x + 20, y: player.y + 4 }, { x: player.x + 140, y: player.y + 90 });
      await settle();
      const before = await qa("positions()");
      await page.clickOn('#fanFront [aria-label="Expand Big"]');
      await settle();
      let after = await qa("positions()");
      let moved = Object.keys(before).filter((id) => id !== bigId && String(before[id]) !== String(after[id]));
      assert(!moved.length, `others moved on open: ${moved}`);
      await page.clickOn('#fanFront [aria-label="Collapse Big"]');
      await settle();
      after = await qa("positions()");
      moved = Object.keys(before).filter((id) => String(before[id]) !== String(after[id]));
      assert(!moved.length, `moved on collapse: ${moved}`);
      await page.clickOn("#viewConcrete");  // a view change drops the unsaved moves again
      await settle();
      await page.clickOn("#viewAll");
      await settle();
    });

    await step("Expand all / Collapse all keep every spot", async () => {
      const before = await qa("positions()");
      const closedTops = await page.eval("Object.fromEntries(cy.nodes().map((n) => [n.id(), [Math.round(n.boundingBox().x1), Math.round(n.boundingBox().y1)]]))");
      await page.clickOn("#expandAllBtn");
      await settle();
      const openTops = await page.eval("Object.fromEntries(cy.nodes().map((n) => [n.id(), [Math.round(n.position().x - n.data('w') / 2), Math.round(n.position().y - n.data('h') / 2)]]))");
      const shifted = Object.keys(closedTops).filter((id) => Math.abs(closedTops[id][0] - openTops[id][0]) > 2 || Math.abs(closedTops[id][1] - openTops[id][1]) > 2);
      assert(!shifted.length, `top-left corners moved: ${shifted}`);
      await page.clickOn("#collapseAllBtn");
      await settle();
      assert(JSON.stringify(await qa("positions()")) === JSON.stringify(before), "collapse all restores");
    });

    await step("middle-drag pans over empty space and an open box, moving no box", async () => {
      await page.clickOn('#fanFront [aria-label="Expand Big"]');
      await settle();
      const row = await page.centreOf('#fanFront rect.fn-row[aria-label="Big.step_01"]');
      const empty = await page.eval("(() => { const r = document.getElementById('cy').getBoundingClientRect(); return { x: r.x + 6, y: r.y + r.height - 6 }; })()");
      const pan0 = await page.eval("({ ...cy.pan() })");
      const state = () => page.eval("JSON.stringify({ pan: cy.pan(), nodes: cy.nodes().map((n) => n.position()) })");
      for (const [where, from] of [["empty space", empty], ["an open box", row]]) {
        const before = JSON.parse(await state());
        await page.middleDrag(from, { x: from.x + 30, y: from.y + 20 });
        await settle(100);
        const after = JSON.parse(await state());
        assert(Math.abs(after.pan.x - before.pan.x - 30) < 1 && Math.abs(after.pan.y - before.pan.y - 20) < 1, `middle-drag over ${where} pans by the drag (${JSON.stringify(before.pan)} to ${JSON.stringify(after.pan)})`);
        assert(JSON.stringify(after.nodes) === JSON.stringify(before.nodes), `middle-drag over ${where} moves no box`);
      }
      await page.eval(`void cy.pan(${JSON.stringify(pan0)})`);
      await page.clickOn("#collapseAllBtn");
      await settle();
    });

        await step("function hover, list scroll, filter box", async () => {
      await page.clickOn('#fanFront [aria-label="Expand Big"]');
      await settle();
      const row = await page.centreOf('#fanFront rect.fn-row[aria-label="Big.step_01"]');
      assert(row, "row step_01 drawn");
      await page.move(row.x, row.y);
      await sleep(200);
      assert((await qa("showing()")).includes("step_01"), "hovering a function shows it");
      assert(await page.eval("document.querySelectorAll('#fanBack .hot, #fanFront .hot, #fanBack path.dim, #fanFront .dim').length") > 0, "its lines light up, others dim");
      const big = await qa('node("Big")');
      const rowsBefore = await page.eval("[...document.querySelectorAll('#fanFront rect.fn-row')].map((r) => r.getAttribute('aria-label')).join()");
      await page.wheel(row.x, row.y + 60, 120);
      await settle(200);
      const rowsAfter = await page.eval("[...document.querySelectorAll('#fanFront rect.fn-row')].map((r) => r.getAttribute('aria-label')).join()");
      assert(rowsBefore !== rowsAfter, "the wheel scrolled the list");
      await page.clickOn('#fanFront input.fn-filter');
      await page.type("step_1");
      await settle(300);
      const shown = await page.eval("document.querySelectorAll('#fanFront rect.fn-row').length");
      assert(shown === 10, `filter shows step_10..19 (got ${shown})`);
      assert(big, "Big still drawn");
      await page.clickOn("#collapseAllBtn");
      await settle();
    });

    await step("View mode drag snaps back on view change", async () => {
      const player = await qa('node("Player")');
      await page.drag({ x: player.x + 20, y: player.y + 4 }, { x: player.x + 140, y: player.y + 90 });
      await settle();
      const dragged = await qa('node("Player")');
      assert(Math.abs(dragged.x - player.x - 120) < 8, "dragged");
      await page.clickOn("#viewConcrete");
      await settle();
      await page.clickOn("#viewAll");
      await settle();
      const back = await qa('node("Player")');
      assert(Math.abs(back.x - player.x) < 2 && Math.abs(back.y - player.y) < 2, "snapped back");
      const saved = await (await fetch(`${base}api/layout`)).json();
      assert(!saved.items.some((i) => i.key === player.id), "nothing saved in View mode");
    });

    const layout = async () => (await (await fetch(`${base}api/layout`)).json()).items;

    await step("Edit layout: stripes, drag saves, pin, Undo/Redo", async () => {
      await page.clickOn("#modeEdit");
      await sleep(200);
      assert(await page.eval("document.getElementById('app').classList.contains('editing') && !document.getElementById('editbar').hidden"), "editing look");
      const player = await qa('node("Player")');
      await page.drag({ x: player.x + 20, y: player.y + 4 }, { x: player.x + 100, y: player.y + 60 });
      await settle();
      assert((await layout()).some((i) => i.key === player.id), "drag saved");
      await page.click(player.x + 100 + 20, player.y + 64);
      await sleep(200);
      await page.clickOn('#details [data-act="pin"]');
      await settle();
      assert((await layout()).find((i) => i.key === player.id)?.pinned, "pinned");
      await page.clickOn("#undoBtn");
      await settle();
      assert(!(await layout()).find((i) => i.key === player.id)?.pinned, "undo unpins");
      await page.clickOn("#undoBtn");
      await settle();
      assert(!(await layout()).some((i) => i.key === player.id), "undo drops the drag");
      await page.clickOn("#redoBtn");
      await settle();
      assert((await layout()).some((i) => i.key === player.id), "redo brings it back");
      await page.clickOn("#undoBtn");
      await settle();
    });

    async function pickMethod(value) {
      await page.eval("document.getElementById('autoMethod').focus()");
      for (let i = 0; i < 4 && await page.eval("document.getElementById('autoMethod').value") !== value; i++) await page.key("ArrowDown");
      for (let i = 0; i < 4 && await page.eval("document.getElementById('autoMethod').value") !== value; i++) await page.key("ArrowUp");
      assert(await page.eval("document.getElementById('autoMethod').value") === value, `picked ${value}`);
      await settle(500);
    }

    await step("Auto layout: preview in View, saved in Edit, Hub centre", async () => {
      await page.clickOn("#modeView");
      await settle();
      const before = await qa("positions()");
      await pickMethod("grid");
      assert(JSON.stringify(await qa("positions()")) !== JSON.stringify(before), "grid preview moved classes");
      assert(!(await layout()).length, "preview saves nothing");
      const player = await qa('node("Player")');
      await page.click(player.x + 20, player.y + 4);
      await pickMethod("hub");
      assert(await page.eval("document.getElementById('autoBtn').title") === "Hub around Player", "hub names the selected class");
      const big = await qa('node("Big")');
      await page.click(big.x + 20, big.y + 4);
      await sleep(150);
      assert(await page.eval("document.getElementById('autoBtn').title") === "Hub around Big", "selecting another class re-centres");
      await page.clickOn("#autoBtn");
      await settle(500);
      const line = await qa('edge("Player", "Big")');
      if (line) {
        await page.click(line.x, line.y);
        await sleep(150);
        assert(await page.eval("document.getElementById('autoBtn').title") === "Hub around Player", "a line centres on its from class");
      }
      const away = await qa("empty()");
      await page.click(away.x, away.y);
      await sleep(150);
      assert(/Hub around main\.gd/.test(await page.eval("document.getElementById('autoBtn').title")), "nothing selected: the app's start");
      await page.clickOn("#modeEdit");
      await pickMethod("layered");
      assert((await layout()).length >= (await page.eval("cy.nodes().length")) - 1, "Edit mode saves every spot");
      for (let i = 0; i < 6 && !(await page.eval("document.getElementById('undoBtn').disabled")); i++) {
        await page.clickOn("#undoBtn");
        await settle();
      }
      await page.clickOn("#modeView");
      await settle();
    });

    await step("ignore a warning, then undo", async () => {
      await setLevel("End");
      const unused = await qa('node("Unused")');
      assert(unused, "Unused shown");
      await page.click(unused.x + 20, unused.y + 4);
      await sleep(250);
      await page.clickOn('#details [data-act="ignore-warning"]');
      await settle();
      assert(JSON.stringify(lastNote("node:Unused", "ignore_warnings")?.ignore_warnings) === '["unused"]', "ignored");
      await page.eval("document.querySelector('#details details')?.setAttribute('open', '')");
      await page.clickOn('#details [data-act="unignore-warning"]');
      await settle();
      const undone = lastNote("node:Unused", "ignore_warnings");
      assert(!undone?.ignore_warnings?.length, `undone: ${JSON.stringify(undone)}`);
      assert(await page.eval("Boolean(document.querySelector('#details [data-act=\"ignore-warning\"]'))"), "warning back");
    });

    await step("side panels: drag, keyboard, double-click reset", async () => {
      const width = (id) => page.eval(`document.getElementById('${id}').getBoundingClientRect().width`);
      const start = await width("details");
      const handle = await page.centreOf("#detailsResizer");
      await page.drag(handle, { x: handle.x - 100, y: handle.y });
      await sleep(150);
      assert(Math.abs(await width("details") - start - 100) < 4, "dragged 100 wider");
      await page.eval("document.getElementById('detailsResizer').focus()");
      const mid = await width("details");
      await page.key("ArrowLeft");
      await sleep(100);
      assert(Math.abs(await width("details") - mid - 16) < 2, "arrow key widens by 16");
      const h = await page.centreOf("#detailsResizer");
      await page.dblclick(h.x, h.y);
      await sleep(150);
      assert(Math.abs(await width("details") - start) < 2, "double-click resets");
      await page.clickOn("#sideToggle");
      await sleep(200);
      const side = await width("side");
      const sh = await page.centreOf("#sideResizer");
      await page.drag(sh, { x: sh.x + 60, y: sh.y });
      await sleep(150);
      assert(Math.abs(await width("side") - side - 60) < 4, "side panel dragged wider");
      await page.dblclick(sh.x + 60, sh.y);
      await sleep(150);
      await page.clickOn("#sideToggle");
      await sleep(200);
    });

    await step("Copy for AI / Copy command only", async () => {
      await page.grantClipboard(base.slice(0, -1));
      await page.clickOn("#copyBtn");
      await page.waitFor("/Copied/.test(document.getElementById('copyOut').textContent)", { what: "copied note" });
      const text = await page.eval("navigator.clipboard.readText()");
      assert(text.startsWith("$ coderstanding view") && text.split("\n").length > 2, `copied view text: ${text.slice(0, 80)}`);
      await page.clickOn("#copyCmdBtn");
      await page.waitFor("/Copied, 1 line/.test(document.getElementById('copyOut').textContent)", { what: "copied one line" });
      const cmd = await page.eval("navigator.clipboard.readText()");
      assert(cmd.startsWith("$ coderstanding view") && !cmd.includes("\n"), `command only: ${cmd}`);
    });

    // ---------- Tours tab ----------
    const tourPanel = (sel) => page.eval(`document.querySelector('#tourPanel ${sel}')?.textContent || ""`);
    async function openTour(query) {
      await open(query);
      await page.waitFor("!document.getElementById('tabTours').hidden && (/Step \\d/.test(document.querySelector('#tourPanel .stepno')?.textContent || '') || document.querySelector('#tourPanel .tour-editbar'))", { what: "tour step" });
      await settle();
    }

    await step("Tours: URL options, list, step panel, code pane, focus view", async () => {
      await openTour("tab=tours&tour=first-steps&step=2");
      assert(await tourPanel(".stepno") === "Step 2/3", `step label: ${await tourPanel(".stepno")}`);
      const list = await page.eval("[...document.querySelectorAll('#tourList .pick')].map((b) => b.textContent.replace(/\\s+/g, ' ').trim())");
      assert(list.length === 1 && /How the game starts 3 steps · sam ?1 AI ?1 changed/.test(list[0]), `list: ${JSON.stringify(list)}`);
      assert(await page.eval("document.querySelector('#tourDots [aria-current=step]').textContent") === "2", "dot 2 current");
      assert(await page.eval("document.querySelector('#tourDots .stale')?.textContent") === "2", "stale dot");
      assert(await page.eval("document.getElementById('codePath').textContent") === "src/player.gd:8-10", "code path");
      assert(await page.eval("document.querySelectorAll('#codeSrc .ln.hl').length") === 2, "two marked lines");
      assert(/^vscode:\/\/file\/.+src\/player\.gd:9$/.test(await page.eval("document.getElementById('openEditor').href")), "Open in editor link");
      const bar = await page.eval("document.getElementById('staleBar').textContent");
      assert(/code changed since written/.test(bar) && /moved from lines 6-8 to 8-10/.test(bar) && /Still true/.test(bar), `stale bar: ${bar}`);
      assert(await page.eval("document.getElementById('canvas').parentElement.id") === "tourGraph", "graph canvas moved into the Tours tab");
      assert(await page.eval("state.focus.join(',')") === "Player#spawn", "view asked with the step's focus");
      assert(await page.eval("cy.nodes('.focus').map((n) => n.id()).join()") === "Player", "the server's focus class highlighted");
      assert(await page.eval("state.selected?.raw.id") === "Player#spawn", "focused function selected (its class opened)");
      const words = await page.eval("[...document.querySelectorAll('#stepText .w')].map((w) => [w.textContent, +w.dataset.n])");
      assert(words[0][0] === "Player#spawn" && words[0][1] === 2, `a link speaks as several words: ${JSON.stringify(words[0])}`);
      assert(/Read aloud · seek by words/.test(await page.eval("document.getElementById('seekChip').textContent")), "voice source chip");
      assert(await page.eval("new URLSearchParams(location.search).get('step')") === "2", "step in the URL");
    });

    await step("Tours keyboard: arrows, Home, Space", async () => {
      await openTour("tab=tours&tour=first-steps&step=1");
      await page.key("ArrowRight");
      await page.waitFor("document.querySelector('#tourPanel .stepno').textContent === 'Step 2/3'", { what: "Right: step 2" });
      await page.key("ArrowRight");
      await page.waitFor("document.querySelector('#tourPanel .stepno').textContent === 'Step 3/3'", { what: "Right: step 3" });
      await page.key("ArrowLeft");
      await page.waitFor("document.querySelector('#tourPanel .stepno').textContent === 'Step 2/3'", { what: "Left: step 2" });
      await page.key("Home");
      await page.waitFor("document.querySelector('#tourPanel .stepno').textContent === 'Step 1/3'", { what: "Home: step 1" });
      await page.key(" ");
      await page.waitFor("document.getElementById('tourPlay').getAttribute('aria-pressed') === 'true'", { what: "Space plays" });
      await page.key(" ");
      await page.waitFor("document.getElementById('tourPlay').getAttribute('aria-pressed') === 'false'", { what: "Space pauses" });
      await settle();
    });

    await step("Tours player: slider and highlight move on the clock, seek by words", async () => {
      await openTour("tab=tours&tour=first-steps&step=1");
      assert((await page.eval("document.getElementById('seekDur').textContent")).startsWith("~"), "voice length is an estimate");
      await page.clickOn("#tourPlay");
      await page.waitFor("+document.getElementById('seekBar').value > 30 && document.querySelector('#tourPanel .w.now')", { what: "slider and highlight move" });
      await page.clickOn("#tourPlay");  // pause
      const total = await page.eval("TourView.player.track.total");
      await page.eval("(() => { const bar = document.getElementById('seekBar'); bar.value = 500; bar.dispatchEvent(new Event('input')); })()");
      const word = await page.eval("TourView.player.word");
      assert(word === Math.floor(total / 2), `seek to the middle word: ${word} of ${total}`);
      const now = await page.eval("+document.querySelector('#tourPanel .w.now').dataset.s");
      assert(now <= word && await page.eval("document.querySelectorAll('#tourPanel .w.said').length") > 3, "highlight at the seeked word, earlier words dim");
    });

    await step("Tours speech (speech-dispatcher voices in Chromium)", async () => {
      await openTour("tab=tours&tour=first-steps&step=1");
      const voices = await page.eval("speechSynthesis.getVoices().length");
      if (!voices) { console.log("     skip: no speech voices (speech-dispatcher + a voice, Chromium with --enable-speech-dispatcher)"); return; }
      await page.eval("window.__speech.length = 0");
      const voice = await page.eval("document.getElementById('tourVoice').value");
      await page.clickOn("#tourPlay");  // a real click: Chromium speaks only after a user gesture
      await page.waitFor("__speech.some((e) => e === 'start' || e.startsWith('error'))", { what: "speech start", timeout: 8000 });
      const events = await page.eval("__speech.slice()");
      assert(!events.some((e) => e.startsWith("error")), `speech error: ${events}`);
      await page.waitFor("+document.getElementById('seekBar').value > 30 && document.querySelector('#tourPanel .w.now')", { what: "slider moves while speaking" });
      await sleep(1500);
      const boundaries = (await page.eval("__speech.filter((e) => e === 'boundary').length"));
      console.log(`     ${voices} voices, picked "${voice}", ${boundaries} boundary events in ~2 s${boundaries ? "" : " (none: the slider runs on the clock)"}`);
      await page.clickOn("#tourPlay");
      assert(voice && !/afrikaans/i.test(voice), `a voice for the page language, not Chromium's first ("default") one: ${voice}`);
    });

    await step("Tours writes: Still true, Accept, Save as Markdown, AI text", async () => {
      await openTour("tab=tours&tour=first-steps&step=2");
      await page.clickOn("#staleBar [data-act=still-true]");
      await page.waitFor("document.getElementById('staleBar').hidden", { what: "stale bar gone" });
      assert(/key: s2[^]*?was: 8-10/.test(tourFile()), "still true re-stamped step 2 only");
      await page.clickOn("#tourPanel [data-act=accept]");
      await page.waitFor("!document.querySelector('#tourPanel [data-act=accept]')", { what: "accepted" });
      assert(!/ai: true/.test(tourFile()), "step 2 accepted");
      const md = await page.eval("document.querySelector('#tourPanel a[data-act=md]').getAttribute('href')");
      assert(md === "/api/tours/first-steps.md?app=fixture" && await page.eval("document.querySelector('#tourPanel a[data-act=md]').download") === "first-steps.md", `Save as Markdown: ${md}`);
      assert((await page.eval(`fetch(${JSON.stringify(md)}).then((r) => r.text())`)).startsWith("# How the game starts"), "Markdown export served");
      await page.clickOn("#aiText summary");
      await page.waitFor("document.getElementById('aiTextBody').dataset.loaded", { what: "AI text loaded" });
      const aiText = await page.eval("document.getElementById('aiTextBody').textContent");
      const cliText = execFileSync(cli, ["tour", fixture, "first-steps", "--step", "2", "--data", data], { env: { ...process.env, ...isolated } }).toString().trimEnd();
      assert(aiText.trimEnd() === cliText, `AI text is the CLI's --step text:\n${aiText}\n---\n${cliText}`);
      await page.grantClipboard(base.slice(0, -1));
      await page.clickOn("#tourPanel [data-act=copy-ai]");
      await page.waitFor("/Copied/.test(document.getElementById('aiToast').textContent)", { what: "copied" });
      assert(await page.eval("navigator.clipboard.readText()") === aiText, "Copy for AI copies the step text");
    });

    await step("Tours editor: add a step from the selection, reorder, save; record a step", async () => {
      await openTour("tab=tours&tour=first-steps&step=2&edit=1");
      assert(await page.eval("document.getElementById('tabTours').classList.contains('editing') && !!document.querySelector('#tourPanel .tour-editbar')"), "edit bar and stripes");
      assert(await page.eval("document.getElementById('edLines').value") === "+0..+2", "existing step's lines as offsets (code.first)");
      // edit step 2: text, and the excerpt one line shorter with one marked line
      await page.eval(`(() => { const set = (id, v) => { const el = document.getElementById(id); el.value = v; el.dispatchEvent(new Event("input", { bubbles: true })); };
        set("edText", "Edited by hand: [[Player#spawn]] announces the player."); set("edLines", "+0..+1"); set("edMark", "9"); })()`);
      const node = await qa('node("Player")');
      await page.click(node.x, node.top + 10);
      await page.waitFor("!document.querySelector('#tourPanel [data-act=add-sel]').disabled", { what: "selection offered" });
      await page.clickOn("#tourPanel [data-act=add-sel]");
      assert(await page.eval("document.querySelectorAll('#tourPanel .steplist li').length") === 4, "step added");
      await page.clickOn("#tourPanel [data-up='2']");
      await page.clickOn("#tourPanel [data-act=save]");
      await page.waitFor("TourView.state.tour.steps.length === 4", { what: "saved" });
      const file = tourFile();
      assert(/key: s1[^]*?targets: \[Player\][^]*?key: s2[^]*?key: s3/.test(file), `new step second, keys kept:\n${file.slice(0, 600)}`);
      assert(/key: s2[^]*?lines: \+0\.\.\+1, mark: \[\+1\]/.test(file) && /Edited by hand/.test(file), `edit stored:\n${file}`);
      assert(/key: s1\n[^]*?lines: \+0\.\.\+5, mark: \[\+3, \+4\]/.test(file) && /key: s3\n[^]*?lines: \+0\.\.\+2, mark: \[\+1, \+2\]/.test(file), "untouched steps keep their lines");
      const saved = await page.eval("TourView.state.tour.steps.find((s) => s.key === 's2').code");
      assert(saved.from === 8 && saved.to === 9 && saved.mark.join() === "9", `round trip: ${JSON.stringify(saved)}`);
      // record step 1 with the fake microphone (the tour as on the server: discard the draft)
      await page.clickOn("#tourPanel [data-act=discard]");
      await openTour("tab=tours&tour=first-steps&step=1&edit=1");
      await page.clickOn("#tourPanel [data-act=rec]");
      await page.waitFor("document.querySelector('#tourPanel [data-act=rec-stop]')", { what: "recording" });
      await sleep(1200);
      await page.clickOn("#tourPanel [data-act=rec-stop]");
      await page.waitFor("TourView.state.tour.steps[0].audio.rec", { what: "recording uploaded", timeout: 15000 });
      assert(existsSync(join(data, "tours", "first-steps", "s1.rec.webm")), "recording stored by step key");
      assert(/Your recording/.test(await page.eval("document.getElementById('seekChip').textContent")), "recording source chip");
      await page.clickOn("#tourPanel [data-act=discard]");  // nothing else changed
      await page.waitFor("!document.getElementById('tabTours').classList.contains('editing')", { what: "editor closed" });
      assert(await page.eval("TourView.player.track.mode") === "rec", "a step with a recording plays it");
      await page.eval("window.__speech && (window.__speech.length = 0)");
      await page.clickOn("#tourPlay");
      await page.waitFor("TourView.player.track.pos > 0.2", { what: "recording plays" });
      await page.clickOn("#tourPlay");
      assert(!(await page.eval("(window.__speech || []).includes('start')")), "no speech for a recording");
    });

    await step("Tours: back to the graph tab", async () => {
      await openTour("tab=tours&tour=first-steps&step=1");
      await page.clickOn("#tabGraphBtn");
      await settle();
      assert(await page.eval("document.getElementById('canvas').parentElement.classList.contains('stage') && !state.focus"), "canvas home, no focus");
      assert(await page.eval("!document.getElementById('tabGraph').hidden && document.getElementById('tabTours').hidden"), "graph tab shown");
    });

    await step("dark and light themes: readable text", async () => {
      for (const theme of ["dark", "light"]) {
        await open(`level=9&theme=${theme}`);
        const colours = await page.eval(`(() => {
          const rgb = (c) => c.match(/[\\d.]+/g).slice(0, 3).map(Number);
          const name = document.querySelector('#fanFront .cls-name');
          const head = document.querySelector('#fanFront .cls-head');
          return { text: rgb(getComputedStyle(name).fill), back: rgb(getComputedStyle(head).fill),
            page: rgb(getComputedStyle(document.body).backgroundColor), theme: document.documentElement.dataset.theme || '' };
        })()`);
        const lum = ([r, g, b]) => [r, g, b].map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; })
          .reduce((sum, v, i) => sum + v * [0.2126, 0.7152, 0.0722][i], 0);
        const [a, b] = [lum(colours.text), lum(colours.back)].sort((x, y) => y - x);
        const ratio = (a + 0.05) / (b + 0.05);
        assert(ratio >= 4.5, `${theme}: class name contrast ${ratio.toFixed(2)} (${JSON.stringify(colours)})`);
        assert(theme === "dark" ? lum(colours.page) < 0.2 : lum(colours.page) > 0.6, `${theme}: page background ${colours.page}`);
        await page.screenshot(join(shots, `qa-e2e-${theme}.png`));
      }
    });

    /** Layout quality of the drawn view: readable first zoom, no overlapping boxes, no long
     *  detours, no piled labels. */
    async function checkLayout(label) {
      const report = await page.eval(`(() => {
        const rects = cy.nodes().map((n) => n.boundingBox({ includeLabels: false }));
        let overlaps = 0;
        rects.forEach((a, i) => rects.slice(i + 1).forEach((b) => {
          if (a.x1 < b.x2 - 2 && b.x1 < a.x2 - 2 && a.y1 < b.y2 - 2 && b.y1 < a.y2 - 2) overlaps++;
        }));
        let longBends = 0;
        cy.edges(".routed").forEach((e) => {
          const a = e.source().position(), b = e.target().position();
          const pts = [a, ...(e.segmentPoints() || []), b];
          let len = 0;
          for (let i = 1; i < pts.length; i++) len += Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y);
          if (len > Math.hypot(b.x - a.x, b.y - a.y) * 1.4 + 61) longBends++;
        });
        const labels = state.edgeLabelRects || [];
        let piled = 0;
        labels.forEach((a, i) => labels.slice(i + 1).forEach((b) => {
          if (a.x1 < b.x2 && b.x1 < a.x2 && a.y1 < b.y2 && b.y1 < a.y2) piled++;
        }));
        return { zoom: cy.zoom(), overlaps, longBends, piled, nodes: cy.nodes().length };
      })()`);
      assert(report.zoom >= 0.82 - 1e-6, `${label}: first zoom ${report.zoom.toFixed(2)} is below readable`);
      assert(!report.overlaps, `${label}: ${report.overlaps} overlapping boxes`);
      assert(!report.longBends, `${label}: ${report.longBends} lines on long detours`);
      assert(!report.piled, `${label}: ${report.piled} piled labels`);
    }

    for (const level of [1, 9]) {
      await step(`layout quality (fixture, level ${level})`, async () => {
        await open(`level=${level}`);
        await checkLayout(`level ${level}`);
      });
    }

    const appRepo = option("--app");
    if (appRepo) {
      const appData = option("--app-data") || mkdtempSync(join(tmpdir(), "coderstanding-e2e-app-"));
      const appPort = await freePort();
      const appServer = await startServer(appPort, appRepo, appData);
      const fixtureBase = base;
      base = `http://127.0.0.1:${appPort}/`;
      try {
        for (const method of ["layered", "organic"]) {
          for (const level of [1, 2]) {
            await step(`layout quality (${appRepo.split("/").pop()}, ${method}, level ${level})`, async () => {
              await page.eval("localStorage.clear()").catch(() => {});
              await open(`level=${level}&layout=${method}`);
              await page.screenshot(join(shots, `qa-e2e-app-${method}-l${level}.png`));
              await checkLayout(`${method} level ${level}`);
            });
            await step(`expanding keeps other boxes still (${method}, level ${level})`, async () => {
              await page.eval("localStorage.clear()").catch(() => {});
              await open(`level=${level}&layout=${method}`);
              const ids = await page.eval("cy.nodes().filter((n) => isExpandable(n.data('raw'))).map((n) => n.id()).slice(0, 6)");
              for (const id of ids) {
                const before = await qa("positions()");
                await page.eval(`toggleExpand(${JSON.stringify(id)})`);
                await settle();
                const after = await qa("positions()");
                const moved = Object.keys(before).filter((other) => other !== id && String(before[other]) !== String(after[other]));
                assert(!moved.length, `opening ${id} moved ${moved.length}: ${moved.slice(0, 3)}`);
              }
            });
          }
        }
      } finally {
        appServer.kill();
        base = fixtureBase;
        if (!option("--app-data")) rmSync(appData, { recursive: true, force: true });
      }
    }

    await step("app picker: switch apps and back, refresh, Projects… → Add project…", async () => {
      const second = join(data, "second-repo");
      mkdirSync(second, { recursive: true });
      const withMain = (name) => `class ${name}:\n    pass\n\nif __name__ == "__main__":\n    ${name}()\n`;  // a start, so level 1 shows it
      writeFileSync(join(second, "alpha.py"), withMain("Alpha"));
      writeFileSync(join(second, "beta.py"), "class Beta:\n    pass\n");
      execFileSync(cli, ["add", "second", second, "--data", join(data, "second-data")], { env: { ...process.env, ...isolated } });
      await open();
      const reloaded = async (app) => {
        await sleep(600);
        await page.waitFor(`window.CoderApps && CoderApps.current === ${JSON.stringify(app)} && window.cy && document.getElementById('overlay').hidden && cy.nodes().length > 0`, { what: `app ${app} loaded` });
        await page.eval(HELPERS);
        await settle();
      };
      const names = () => page.eval("cy.nodes().map((n) => n.data('raw').name).sort().join(',')");
      const optionTexts = await page.eval("[...document.querySelectorAll('#appPick option')].map((o) => o.textContent).join(' | ')");
      assert(/fixture · .*ago.* · \d+ classes/.test(optionTexts) && /second/.test(optionTexts), `picker lists both apps: ${optionTexts}`);
      const home = await names();
      await page.eval("void (cy.zoom(1.37), cy.pan({ x: 41, y: 23 }))");
      const levelBefore = await page.eval("state.level");
      await page.eval("document.getElementById('appPick').value = 'second'; document.getElementById('appPick').dispatchEvent(new Event('change'))");
      await reloaded("second");
      assert(new URL(await page.eval("location.href")).hash === "#second", "URL carries #second");
      assert(await names() !== home, "the graph changed with the app");
      await page.eval("document.activeElement.blur()");
      await page.key("[");  // previous app: wraps back to the first
      await reloaded("fixture");
      assert(await names() === home, "back on the first app's graph");
      assert(await page.eval("state.level") === levelBefore, "level came back");
      const camera = await page.eval("({ zoom: cy.zoom(), pan: cy.pan() })");
      assert(Math.abs(camera.zoom - 1.37) < 1e-6 && Math.abs(camera.pan.x - 41) < 1e-6 && Math.abs(camera.pan.y - 23) < 1e-6, `camera came back: ${JSON.stringify(camera)}`);
      // Rescan the second app after adding a class; the picker shows it without a manual reload
      await page.eval("document.getElementById('appPick').value = 'second'; document.getElementById('appPick').dispatchEvent(new Event('change'))");
      await reloaded("second");
      const before = await page.eval("CoderApps.list.find((a) => a.name === 'second').nodes");
      writeFileSync(join(second, "gamma.py"), "class Gamma:\n    pass\n");
      await page.clickOn("#rescanBtn");
      await sleep(600);
      await page.waitFor("window.cy && document.getElementById('overlay').hidden && CoderApps.list.find((a) => a.name === 'second').nodes > " + before, { what: "rescan done" });
      assert(!existsSync(join(second, ".coderstanding")), "rescan wrote nothing into the repo");
      // Scan a folder: registers, scans, and switches to it
      const third = join(data, "third-repo");
      mkdirSync(third, { recursive: true });
      writeFileSync(join(third, "delta.py"), withMain("Delta"));
      await page.clickOn("#appsBtn");  // Projects…
      await page.waitFor("document.getElementById('appsDialog').open", { what: "Projects… modal open" });
      await page.clickOn("#appsScanFolder");  // Add project…
      await page.eval(`(() => { const p = document.getElementById('scanPath'); p.value = ${JSON.stringify(third)}; p.dispatchEvent(new Event('input')); })()`);
      assert(await page.eval("document.getElementById('scanName').value") === "third-repo", "name suggested from the folder");
      await page.clickOn("#scanGo");
      await reloaded("third-repo");
      assert((await names()).includes("delta"), "scanned folder shown");
      assert(existsSync(join(data, "share", "third-repo", "graph.json")), "data went to the data home, not the repo");
      await open();
    });

    await step("app manager: list with facts, rename, remove from the list", async () => {
      const registry = () => JSON.parse(readFileSync(isolated.CODERSTANDING_REGISTRY, "utf8"));
      const row = (name) => `#appsList li[data-app="${name}"]`;
      const press = (name, text) => page.eval(`[...document.querySelectorAll('${row(name)} button')].find((b) => b.textContent === ${JSON.stringify(text)}).click()`);
      await open();
      await page.clickOn("#appsBtn");
      await page.waitFor(`document.getElementById('appsDialog').open && document.querySelectorAll('#appsList li[data-app]').length === ${Object.keys(registry()).length + 1}`, { what: "manager lists every app" });
      const facts = await page.eval(`document.querySelector('${row("second")} .app-facts').textContent`);
      assert(/(ago|just now) · scanner \d+ · \d+ classes, \d+ connections · graph\.json [\d.]+ k?B · 0 notes · 0 tours/.test(facts), `facts: ${facts}`);
      assert(await page.eval(`/open now/.test(document.querySelector('${row("fixture")}').textContent)`), "current app marked");
      await press("third-repo", "Rename");
      await page.eval(`(() => { const i = document.querySelector('${row("third-repo")} .rename-input'); i.value = 'third'; })()`);
      await press("third-repo", "Save name");
      await page.waitFor(`document.querySelector('${row("third")}')`, { what: "renamed row" });
      assert(registry().third && !registry()["third-repo"], "registry renamed");
      await press("third", "Remove…");
      assert(await page.eval(`/keep its files/.test(document.querySelector('${row("third")} [data-act="remove-keep"]').textContent) && Boolean(document.querySelector('${row("third")} [data-act="remove-delete"]'))`), "two clear remove choices in the modal");
      await page.eval(`document.querySelector('${row("third")} [data-act="remove-keep"]').click()`);
      await page.waitFor(`!document.querySelector('${row("third")}')`, { what: "removed row" });
      assert(!registry().third, "registry entry gone");
      assert(existsSync(join(data, "share", "third-repo", "graph.json")), "files kept");
      assert(!await page.eval("[...document.querySelectorAll('#appPick option')].some((o) => o.value === 'third')"), "picker updated");
      await page.clickOn("#appsClose");
      assert(!await page.eval("document.getElementById('appsDialog').open"), "dialog closed");
    });

    await step("hosted shell: everything from the helper, empty state without one", async () => {
      const helper = `http://127.0.0.1:${port}`;
      const shell = await startShell(roPort, helper);
      const home = base;
      try {
        base = `http://127.0.0.1:${roPort}/`;
        await open();
        const label = await page.eval("({ shown: !document.getElementById('readOnlyLabel').hidden, text: document.getElementById('readOnlyLabel').textContent })");
        assert(label.shown && label.text === "Running on this PC", `label: ${JSON.stringify(label)}`);
        assert(await page.eval(`CoderApps.url("/api/view") === "${helper}/api/view?app=" + encodeURIComponent(CoderApps.current)`), "reads go to the helper");
        assert(await page.eval("!document.getElementById('rescanBtn').hidden && !document.getElementById('rescanBtn').disabled"), "Refresh shown");
        assert(!await page.eval("document.getElementById('modeEdit').disabled"), "Edit layout enabled");
        const main = await qa('node("main.gd")');
        await page.click(main.x + 20, main.y + 4);
        await sleep(250);
        const fields = await page.eval("[...document.querySelectorAll('#details .note-input')].map((i) => i.disabled)");
        assert(fields.length && !fields.some(Boolean), `note fields editable: ${JSON.stringify(fields)}`);
        const wrote = await page.eval(`CoderApps.request("/api/layout", { method: "PUT", headers: { "Content-Type": "application/json" }, body: '{"items": [], "remove": []}' }).then(() => "ok", (e) => e.message)`);
        assert(wrote === "ok", `write through the helper (CORS preflight): ${wrote}`);
        assert(!/read-only/i.test(await page.eval("document.body.innerText")), "no read-only wording anywhere");
        await page.goto(`${base}?theme=dark&tab=tours&tour=first-steps&step=2`);
        await page.waitFor("/Step 2/.test(document.querySelector('#tourPanel .stepno')?.textContent || '')", { what: "hosted tour step" });
        assert(await page.eval("Boolean(document.querySelector('#tourPanel [data-act=edit]'))"), "hosted page edits tours through the helper");
        assert(await page.eval("Boolean(document.querySelector('#tourPanel a[data-act=md]'))"), "Save as Markdown on the hosted page");
      } finally {
        shell.close();
      }
      const deadHelper = `http://127.0.0.1:${await freePort()}`;  // no helper there
      expectedErrors.push(new RegExp(deadHelper.replace(/[.:/]/g, "\\$&")), /ERR_CONNECTION_REFUSED/);
      const dead = await startShell(roPort, deadHelper);
      try {
        await page.goto(base);
        await page.waitFor("!document.getElementById('overlay').hidden && /Open this page on your PC/.test(document.getElementById('overlay').textContent)", { what: "no-helper empty state" });
        const text = await page.eval("document.getElementById('overlay').textContent");
        assert(/systemctl --user start coderstanding-helper/.test(text) && /install-helper\.sh/.test(text), `how-to: ${text}`);
      } finally {
        dead.close();
        base = home;
      }
      await open();
    });

    await step("no console errors overall", async () => {
      assert(!errorsBefore().length, errorsBefore().join(" | "));
    });
  } finally {
    close();
    server.kill();
    if (!args.includes("--keep")) rmSync(data, { recursive: true, force: true });
  }
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} passed`);
  process.exit(failed.length ? 1 : 0);
}

main().catch((error) => { console.error(error); process.exit(2); });
