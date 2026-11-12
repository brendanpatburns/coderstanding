// End-to-end viewer checks in real headless Chromium (DevTools protocol, real mouse and keys).
// Run: node tests/e2e/run.mjs [--keep] [--step NAME-PART] [--only WORDS] [--shots DIR] [--app REPO --app-data DIR]
// --only runs just the checks whose name contains any of WORDS (repeatable or comma-separated,
// case-insensitive), e.g. --only "tours,specs". --step takes one exact name part (no comma split).
// Every check starts from resetPage() (cleared storage, fresh load, deepest level) and builds what it
// needs with scenario helpers (selectNode, registerSecondApp, ensureLinkedNote, rememberPreset), so any check runs alone.
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
const onlyStep = args.includes("--step") ? args[args.indexOf("--step") + 1] : null;
// --only a,b --only c: every value of every --only, split on commas, lowercased
const onlyWords = args.flatMap((arg, i) => (arg === "--only" ? (args[i + 1] ?? "").split(",") : []))
  .map((word) => word.trim().toLowerCase()).filter(Boolean);
const shots = args.includes("--shots") ? args[args.indexOf("--shots") + 1] : join(homedir(), "Downloads", "coderstanding-wip");
const data = mkdtempSync(join(tmpdir(), "coderstanding-e2e-data-"));
const results = [];
// never the real registry or data home
const isolated = { CODERSTANDING_REGISTRY: join(data, "apps.json"), CODERSTANDING_DATA_HOME: join(data, "share"),
  CODERSTANDING_USER: "Sam Tester" };  // a fixed human name, not the machine's git user

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
  settled() { return document.getElementById("overlay").hidden && cy.nodes().length > 0 && !cy.nodes().some((n) => n.animated()) && !cy.animated() && settledDomains(); },
  frames() { return [...document.querySelectorAll("#domBack .dom-frame, #domFront .dom-frame.card")].map((g) => g.dataset.frame).sort(); },
};
true`;

// ---------- Tours: the fixture's tour, silent speech ----------

const fixtureTour = join(fixture, ".coderstanding", "tours", "first-steps.md");
/** The tour file as the server keeps it (in the data folder: serve runs with --data). */
const tourFile = () => readFileSync(join(data, "tours", "first-steps.md"), "utf8");
/** The fixture's spec tree (game > player (AI draft) > helper; tools), copied into the data folder. */
const fixtureSpecs = join(fixture, ".coderstanding", "specs");
const specFile = (id, folder = "specs") => (existsSync(join(data, folder, `${id}.md`)) ? readFileSync(join(data, folder, `${id}.md`), "utf8") : null);

/** Every utterance speaks at volume 0 (no sound from the checks) and logs its events
 *  (`__speech`: names; `__speechLog`: {t, type, text} with performance.now() times). */
// Real speech is opt-in: speech-dispatcher plays out loud whatever the page volume, so by default
// Chromium gets no voices and the speech check skips. CODERSTANDING_E2E_SPEECH=1 turns it on.
const REAL_SPEECH = process.env.CODERSTANDING_E2E_SPEECH === "1";
const SILENT_SPEECH = `(() => {
  if (!window.speechSynthesis) return;
  window.__speech = [];
  window.__speechLog = [];
  const speak = speechSynthesis.speak.bind(speechSynthesis);
  speechSynthesis.speak = (u) => {
    u.volume = 0;
    for (const name of ["start", "boundary", "end", "error"]) u.addEventListener(name, (e) => {
      __speech.push(name + (e.error ? ":" + e.error : ""));
      __speechLog.push({ t: performance.now(), type: name, text: u.text });
    });
    speak(u);
  };
})();`;

async function main() {
  mkdirSync(shots, { recursive: true });
  // Scans are manual now (no rescan-if-stale): scan the fixture once before anything reads it.
  execFileSync(cli, ["scan", fixture, "-o", join(data, "graph.json")], { env: { ...process.env, ...isolated } });
  execFileSync(cli, ["set", fixture, "Player", "hint=AI wrote this", "--data", data]);  // an AI draft to accept
  mkdirSync(join(data, "tours"), { recursive: true });
  writeFileSync(join(data, "tours", "first-steps.md"), readFileSync(fixtureTour));
  mkdirSync(join(data, "specs"), { recursive: true });
  for (const id of ["game", "player", "helper", "tools"]) writeFileSync(join(data, "specs", `${id}.md`), readFileSync(join(fixtureSpecs, `${id}.md`)));
  mkdirSync(join(data, "memos"), { recursive: true });
  writeFileSync(join(data, "memos", "bus-note.md"), readFileSync(join(fixture, ".coderstanding", "memos", "bus-note.md")));
  const port = await freePort();
  const roPort = await freePort();  // the hosted shell step's page uses this server as its helper
  isolated.CODERSTANDING_ALLOW_ORIGIN = `http://127.0.0.1:${roPort}`;
  const server = await startServer(port);
  let base = `http://127.0.0.1:${port}/`;
  // --enable-speech-dispatcher: Chromium on Linux lists speech-dispatcher voices only with it;
  // fake microphone (a tone, never played: --mute-audio) for the tour recording check
  const { page, close } = await launch({ width: 1780, height: 1000,
    args: [...(REAL_SPEECH ? ["--enable-speech-dispatcher"] : []), "--use-fake-device-for-media-stream",
      "--use-fake-ui-for-media-stream"] });  // graph area ~1400x800
  await page.send("Page.addScriptToEvaluateOnNewDocument", { source: SILENT_SPEECH });
  const requests = [];  // every request the page makes over the whole run (offline guarantee, DESIGN 18)
  page.conn.on((method, params) => { if (method === "Network.requestWillBeSent") requests.push(params.request.url); });
  await page.send("Network.enable");
  const expectedErrors = [/favicon\.ico/];  // a step may add one it causes on purpose
  const errorsBefore = () => page.errors.filter((e) => !expectedErrors.some((pattern) => pattern.test(e)));

  // Boxes show names only (Box text "name") unless a check asks for `boxtext=`: the older checks'
  // spots and sizes assume that face; the "box faces" checks cover the taller ones.
  async function open(query = "") {
    // The Details fold under "In plain words" opens for the older checks (they use today's fields); `details=closed` = as first seen.
    // The checks rely on the switches as they always were (the Explore preset) with More options open (`preset=` / `more=` to test those).
    const pins = { theme: "dark", boxtext: "name", overview: "0", details: "open", preset: "explore", more: "open" };
    const query0 = query.replace(/^\?/, "");
    const names = new Set([...new URLSearchParams(query0).keys()]);
    const extra = Object.entries(pins).filter(([name]) => !names.has(name)).map(([name, value]) => `${name}=${value}&`).join("");
    await page.goto(`${base}?${extra}${query0}`);
    await page.eval(HELPERS);
    await page.waitFor("window.cy && document.getElementById('overlay').hidden && cy.nodes().length > 0", { what: "first view" });
    await settle();
  }
  async function settle(ms = 350) {
    await sleep(ms);
    await page.waitFor("window.__qa && __qa.settled()", { what: "view settled" });
  }
  const qa = (expr) => page.eval(`__qa.${expr}`);
  /** Drop remembered view switches (their URL names, viewer/view_options.js) so later checks start from the preset. */
  const forgetOptions = (...names) => page.eval(`Object.keys(localStorage).filter((k) => k.startsWith("coderstanding-view")).forEach((k) => {
    const v = JSON.parse(localStorage.getItem(k)); ${JSON.stringify(names)}.forEach((n) => delete v.changed?.[n]); localStorage.setItem(k, JSON.stringify(v)); }), true`);
  async function setLevel(key) {  // "Home" = the lowest ruler column, "End" = the deepest
    const levels = await page.eval("[...document.querySelectorAll('#rulerCols .rcol:not(:disabled)')].map((c) => c.dataset.level)");
    await page.clickOn(`#rulerCols .rcol[data-level="${key === "Home" ? levels[0] : levels.at(-1)}"]`);
    await settle();
  }
  /** Every check starts here: remembered view options cleared, a fresh load of the preset view
   *  (no selection, no open panels, no URL hash, graph tab, deepest default level). */
  async function resetPage() {
    await page.eval("localStorage.clear(), sessionStorage.clear(), true");
    await open();
    await setLevel("End");  // the checks were written for the deepest level (every class shown)
  }
  // Scenario helpers for the extra apps (idempotent: a check that needs them registers them itself).
  const withMain = (name) => `class ${name}:\n    pass\n\nif __name__ == "__main__":\n    ${name}()\n`;  // a start, so level 1 shows it
  const registered = () => (existsSync(isolated.CODERSTANDING_REGISTRY) ? Object.keys(JSON.parse(readFileSync(isolated.CODERSTANDING_REGISTRY, "utf8"))) : []);
  function writeAppFolder(folder, files) {
    mkdirSync(folder, { recursive: true });
    for (const [name, source] of Object.entries(files)) writeFileSync(join(folder, `${name}.py`), source);
  }
  function registerSecondApp() {
    const second = join(data, "second-repo");
    if (registered().includes("second")) return second;
    writeAppFolder(second, { alpha: withMain("Alpha"), beta: "class Beta:\n    pass\n" });
    execFileSync(cli, ["add", "second", second, "--data", join(data, "second-data")], { env: { ...process.env, ...isolated } });
    return second;
  }
  /** The app the picker check adds through Projects… > Add project…, registered directly. */
  function registerThirdApp() {
    const third = join(data, "third-repo");
    if (registered().some((name) => name.startsWith("third"))) return third;
    writeAppFolder(third, { delta: withMain("Delta") });
    execFileSync(cli, ["add", "third-repo", third, "--data", join(data, "share", "third-repo")], { env: { ...process.env, ...isolated } });
    return third;
  }
  /** Scenario: the app's view switches are remembered as a preset (a page reload on an app switch
   *  drops the URL's `?preset=`, so a check that switches apps needs it stored). */
  const rememberPreset = (app, preset) => page.eval(`localStorage.setItem("coderstanding-view:${app}", JSON.stringify({ preset: "${preset}", changed: {} })), true`);
  /** Scenario: click a class and wait until the details panel shows it. */
  async function selectNode(name) {
    const node = await qa(`node(${JSON.stringify(name)})`);
    await page.click(node.x + 20, node.y + 4);
    await sleep(250);
    await page.waitFor(`__qa.showing().includes(${JSON.stringify(name)})`, { what: `details of ${name}` });
  }
  async function step(name, fn) {
    if (onlyStep && !name.includes(onlyStep)) return;  // --step <part of a name>: run just that one
    if (onlyWords.length && !onlyWords.some((word) => name.toLowerCase().includes(word))) return;  // --only
    const before = errorsBefore().length;
    try {
      await resetPage();
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

    await step("depth ruler: columns, click a level, arrow keys, Levels… saves a name", async () => {
      const count = () => page.eval("cy.nodes().length");
      const text = () => page.eval("document.getElementById('rulerCols').textContent");
      assert(await page.eval("!document.getElementById('level') && !document.getElementById('specFollow')"), "old slider and follow checkbox gone");
      assert(await page.eval("document.querySelectorAll('#rulerCols .rcol').length >= 2"), "ruler shows a column per level");
      await setLevel("Home");
      const low = await count();
      const label = await page.eval("document.getElementById('rulerCount').textContent");
      assert(/^\d+ of \d+ classes$/.test(label), `count text: ${label}`);
      assert(await page.eval("document.querySelectorAll('#rulerCols .rcol.here').length === 1"), "one lit column");
      await setLevel("End");
      assert(await count() > low, "more classes at the deepest level");
      assert(await page.eval("document.getElementById('rulerCount').textContent") !== label, "count changes");
      await page.key("ArrowLeft");
      await settle();
      assert(await page.eval("state.level") < await page.eval("Number(document.querySelector('#rulerCols .rcol:last-child').dataset.level)"), "left arrow steps up");
      await page.clickOn("#levelsBtn");
      await page.eval(`(() => { const i = document.querySelector('#levelsNames input[data-name="1"]'); i.value = "Zebra"; })()`);
      await page.clickOn("#levelsSave");
      await settle();
      await page.waitFor("document.getElementById('rulerCols').textContent.includes('Zebra')", { what: "saved name shows" });
      assert((await text()).includes("1 Zebra"), "level 1 is named");
      await page.screenshot(join(shots, "depth-ruler.png"));
      await page.clickOn("#levels" + "Btn");
      await page.clickOn("#levelsReset");  // later steps add deeper classes: no saved count may clamp them
      await page.waitFor("!document.getElementById('rulerCols').textContent.includes('Zebra')", { what: "names reset" });
      await setLevel("End");  // the steps after this one expect the deepest level
    });

    await step("All types / Concrete", async () => {
      await page.clickOn("#viewConcrete");
      await settle();
      assert(await page.eval("document.getElementById('viewConcrete').getAttribute('aria-pressed')") === "true", "Concrete pressed");
      await page.clickOn("#viewAll");
      await settle();
      assert(await page.eval("document.getElementById('viewAll').getAttribute('aria-pressed')") === "true", "All types pressed");
    });

    await step("Show toggles: Outside code, Tests; the .tscn chip hides scenes", async () => {
      const has = (name) => qa(`node(${JSON.stringify(name)})`).then(Boolean);
      assert(await has("main.tscn") && await has("requests"), "scene and outside code shown at first");
      assert(!(await has("test_player.gd")), "tests hidden at first");
      for (const [box, name, shown] of [["#typeStrip [data-type=\".tscn\"]", "main.tscn", false], ["#showExternal", "requests", false], ["#showTest", "test_player.gd", true]]) {
        await page.clickOn(box);
        await settle();
        assert(await has(name) === shown, `${box} ${shown ? "shows" : "hides"} ${name}`);
        await page.clickOn(box);
        await settle();
        assert(await has(name) === !shown, `${box} back`);
      }
    });

    await step("Show: Communications switch is on and reloads the view (the fixture has no sockets)", async () => {
      assert(await page.eval("document.getElementById('showComm').checked"), "on at first");
      await page.clickOn("#showComm");
      await settle();
      assert(!(await page.eval("document.getElementById('showComm').checked")), "turned off");
      await page.clickOn("#showComm");
      await settle();
      assert(await qa("node(\"requests\")"), "view still drawn after toggling");
    });

    await step("Plain words: legend lists the view's kinds with plain words, click lights them and fades the rest, hide control hides", async () => {
      const entry = (kind) => `.legend [data-kind="${kind}"]`;
      const kind = await page.eval(`cy.edges().map((e) => e.data('raw').kinds[0])[0]`);
      const lines = () => page.eval(`cy.edges().filter((e) => e.data('raw').kinds.includes('${kind}')).length`);
      const before = await lines();
      assert(before > 0, "fixture has lines of a kind");
      const shown = await page.eval(`[...document.querySelectorAll('.legend [data-kind]')].map((b) => b.dataset.kind).sort().join()`);
      const inView = await page.eval(`[...new Set(cy.edges().flatMap((e) => e.data('raw').kinds))].sort().join()`);
      assert(shown.split(",").filter((k) => k !== "derived").join() === inView, `legend ${shown} = kinds in the view ${inView}`);
      assert(await page.eval(`document.querySelector('${entry(kind)}').textContent.length > ${kind.length}`) && await page.eval(`!!document.querySelector('${entry(kind)} .uml')`), "plain word first, UML name small after");
      await page.clickOn(entry(kind));
      assert(await page.eval("cy.edges('.kind-hit').length") === before, "click lights that kind's lines");
      assert(await page.eval(`cy.edges('.kind-dim').length === cy.edges().length - ${before}`), "the other lines fade");
      assert(await page.eval(`document.querySelector('${entry(kind)}').getAttribute('aria-pressed') === 'true'`), "entry shows it is lit");
      await page.clickOn(entry(kind));
      assert(await page.eval("cy.edges('.kind-hit, .kind-dim').length") === 0, "click again clears");
      await page.clickOn(`.legend [data-hide-kind="${kind}"]`);
      await settle();
      assert(await page.eval(`cy.edges().filter((e) => e.data('raw').kinds.every((k) => k === '${kind}')).length`) === 0, "hide removes the kind's lines");
      assert(await page.eval(`document.querySelector('${entry(kind)}').closest('.legend-entry').classList.contains('off')`), "the entry stays listed, marked hidden");
      await page.clickOn(`.legend [data-hide-kind="${kind}"]`);
      await settle();
      assert(await lines() === before, "show brings them back");
    });

    await step("Plain words: line hover sentence, glossary terms outside notes, Words list, old helper without words", async () => {
      // a line whose middle is on screen and clear of every box (taller box faces can cover a short line's middle)
      const mid = () => page.eval(`(() => { const clear = (m) => m.x > 0 && m.y > 0 && m.x < cy.width() && m.y < cy.height() && !cy.nodes(':visible').some((n) => { const r = n.renderedBoundingBox(); return m.x > r.x1 && m.x < r.x2 && m.y > r.y1 && m.y < r.y2; });
        const e = cy.edges().filter((x) => x.visible() && clear(x.renderedMidpoint()))[0]; const b = cy.container().getBoundingClientRect(), m = e.renderedMidpoint(); return { x: b.x + m.x, y: b.y + m.y }; })()`);
      const at = await mid();
      await page.move(at.x, at.y);
      await page.waitFor("document.querySelector('.word-tip') && !document.querySelector('.word-tip').hidden", { what: "line tip" });
      const tip = await page.eval("document.querySelector('.word-tip').textContent");
      assert(/ = /.test(tip) && (/\(no note yet\)/.test(tip) || /: \S/.test(tip)), `sentence with the kind's tip: ${tip}`);
      await page.move(5, 5);
      await page.waitFor("document.querySelector('.word-tip').hidden", { what: "tip hides" });
      // glossary: first use of a term in the page text gets a dotted underline and a definition; notes and canvas labels do not
      await page.waitFor("document.querySelector('.controls .term, #details .term, #ruler .term')", { what: "a term underlined" });
      assert(await page.eval("!document.querySelector('textarea .term, [data-field]:not(dt) .term, #canvas .term, button .term')"), "no terms in notes, boxes or buttons");
      assert(await page.eval("new Set([...document.querySelectorAll('#details .term')].map((t) => t.dataset.term)).size === document.querySelectorAll('#details .term').length"), "first use only");
      const term = await page.eval("document.querySelector('.controls .term, #details .term, #ruler .term').dataset.term");
      await page.eval(`document.querySelector('.term[data-term="${term}"]').focus()`);
      await page.waitFor("!document.querySelector('.word-tip').hidden && document.querySelector('.word-tip').textContent.length > 20", { what: "definition on focus" });
      await page.move(5, 5);
      assert(await page.eval("/god class/.test(FieldHelp.wordsList()) && /uses/.test(FieldHelp.wordsList())"), "Words list in the help");
      // an older helper sends no `words`: the legend falls back to today's labels and nothing breaks
      await page.eval(`FieldHelp.setRules({ rule: '', house: '', fields: {} }); renderLegend(state.current)`);
      assert(await page.eval("document.querySelectorAll('.legend [data-kind]').length > 0 && !document.querySelector('.legend .uml')"), "legend still built, kind names only");
      await page.eval(`api('/api/fields').then((r) => FieldHelp.setRules(r)).then(() => renderLegend(state.current))`);
      await page.waitFor("!!document.querySelector('.legend .uml')", { what: "words back" });
    });

    await step("Types strip: chips toggle a type's classes, hover lights them, names are never cut", async () => {
      const chip = (type) => `#typeStrip [data-type="${type}"]`;
      const count = (type) => page.eval(`cy.nodes().filter((n) => n.data('type') === '${type}').length`);
      const gd = await count(".gd");
      assert(gd > 0, "fixture has .gd classes");
      assert(await page.eval(`document.querySelector('${chip(".gd")}').textContent.includes('${gd}')`), "chip shows the count");
      const { x, y } = await page.centreOf(chip(".gd"));
      await page.move(x, y);
      assert(await page.eval("cy.nodes('.type-hit').length") === gd, "hover lights that type's boxes");
      await page.move(5, 5);
      await sleep(50);
      assert(await page.eval("cy.nodes('.type-hit').length") === 0, "mouse leave clears the light");
      await page.clickOn(chip(".gd"));
      await settle();
      assert(await count(".gd") === 0, "clicking hides the type's classes");
      assert(await page.eval(`document.querySelector('${chip(".gd")}').classList.contains('off')`), "the chip stays listed, marked off");
      await page.clickOn(chip(".gd"));
      await settle();
      assert(await count(".gd") === gd, "clicking again brings them back");
      const clipped = await page.eval(`[...document.querySelectorAll('.face')].filter((face) => {
        const frame = face.querySelector('.cls-head')?.getBoundingClientRect();
        return [...face.querySelectorAll('.cls-name, .cls-sub')].some((t) => t.getBoundingClientRect().right > frame.right + 0.5);
      }).length`);
      assert(clipped === 0, `${clipped} box texts run past their box`);
      assert(!(await page.eval("document.querySelector('#fanFront').textContent.includes('…')")), "no ellipsis in box labels");
      mkdirSync(shots, { recursive: true });
      await page.screenshot(join(shots, "type-chips.png"));
    });

    await step("Filters keep the view: a chip or Show switch only adds or removes its own classes", async () => {
      // regression: toggling a filter used to refit the camera and re-place every box
      const spots = () => page.eval(`JSON.stringify([...cy.nodes().map((n) => [n.id(), Math.round(n.position('x')), Math.round(n.position('y'))]), cy.zoom().toFixed(3), Math.round(cy.pan().x), Math.round(cy.pan().y)])`);
      const opened = await page.eval(`cy.nodes().filter((n) => n.data('type') === '.gd')[0].id()`);
      await page.eval(`state.expanded.add(${JSON.stringify(opened)}); loadView().then(() => { selectInGraph(${JSON.stringify(opened)}); cy.zoom(0.9); cy.pan({ x: 123, y: 77 }); })`);
      await settle();
      const before = await spots();
      for (const box of ['#typeStrip [data-type=".tscn"]', "#showExternal"]) {
        await page.clickOn(box);
        await settle();
        assert(await page.eval("cy.zoom().toFixed(3)") === "0.900", `${box}: zoom kept`);
        assert(await page.eval("Math.round(cy.pan().x) + ',' + Math.round(cy.pan().y)") === "123,77", `${box}: pan kept`);
        await page.clickOn(box);
        await settle();
      }
      const after = await spots();
      assert(after === before, "after hiding and showing again every box is where it was");
      assert(await page.eval(`state.expanded.has(${JSON.stringify(opened)}) && state.selected?.raw?.id === ${JSON.stringify(opened)}`), "the open class and the selection stay");
    });

    await step("Types strip: file-kind chips toggle a kind through the view request", async () => {
      // the fixture is all code, so draw the chips from a made-up view and check the click's request
      await page.eval(`renderTypeStrip({ ...state.current, file_kinds: { Code: 5, Data: 2 }, hidden_file_kinds: [] })`);
      assert(await page.eval("document.querySelectorAll('#typeStrip [data-file-kind]').length") === 2, "one chip per kind");
      await page.clickOn('#typeStrip [data-file-kind="Data"]');
      await settle();
      assert(await page.eval("state.hiddenFileKinds.join()") === "Data", "the click saves the hidden kind");
      assert(await page.eval("viewParams().getAll('hide_file_kind').join()") === "Data", "and the view request carries it");
      await page.eval(`toggleFileKind('Data')`);
      await settle();
      assert(await page.eval("state.hiddenFileKinds.length") === 0, "toggling again clears it");
    });

    await step("Nicknames on the box, and tag chips toggle a tag through the view request", async () => {
      const notesFile = join(data, "notes.jsonl");
      const saved = existsSync(notesFile) ? readFileSync(notesFile) : null;
      const levelBefore = await page.eval("state.level");  // later checks go on from this level
      try {
        execFileSync(cli, ["set", fixture, "Big", "nickname=World_map", "tags=qa_tag", "--data", data]);
        await open();
        await setLevel("End");
        const box = await page.eval(`(() => { const n = cy.nodes().filter((x) => x.data('raw').name === 'Big')[0];
          return n && { name: n.data('name'), marks: n.data('marks') }; })()`);
        assert(box && box.name === "World_map", `nickname is the box's main name: ${JSON.stringify(box)}`);
        assert(box.marks.split(" · ")[0] === "Big", `the real name is small under it: ${box.marks}`);
        assert(await page.eval("document.querySelectorAll('#typeStrip [data-tag=\"qa_tag\"]').length") === 1, "one chip for the tag");
        await page.clickOn('#typeStrip [data-tag="qa_tag"]');
        await settle();
        assert(await page.eval("state.hiddenTags.join()") === "qa_tag", "the click saves the hidden tag");
        assert(await page.eval("viewParams().getAll('hide_tag').join()") === "qa_tag", "and the view request carries it");
        assert(await page.eval("!cy.nodes().some((x) => x.data('raw').name === 'Big')"), "the tagged class is gone");
        assert(await page.eval("document.querySelector('#typeStrip [data-tag=\"qa_tag\"]').classList.contains('off')"), "its chip stays, off");
        await page.eval(`toggleTag('qa_tag')`);
        await settle();
        assert(await page.eval("state.hiddenTags.length") === 0, "toggling again clears it");
      } finally {
        if (saved) writeFileSync(notesFile, saved); else rmSync(notesFile, { force: true });
        await forgetOptions("hide_tag");
        await open();
        if (await page.eval("state.level") !== levelBefore) {
          await page.clickOn(`#rulerCols .rcol[data-level="${levelBefore}"]`);
          await settle();
        }
      }
    });

    await step("Long class and function names wrap, in the header and in open rows", async () => {
      await page.clickOn("#expandAllBtn");
      await settle();
      const long = await page.eval(`(() => {
        const n = cy.nodes().filter((x) => x.data('name').startsWith('VeryLong'))[0];
        return n && { lines: n.data('nameLines'), name: n.data('name'), fn: Object.values(n.data('fnLines'))[0] };
      })()`);
      assert(long, "the long class is shown");
      assert(long.lines.length > 1 && long.lines.join("") === long.name, "its name wraps onto lines, nothing lost");
      assert(long.fn.length > 1 && long.fn.join("").endsWith("()"), "its long function name wraps too");
      const past = await page.eval(`[...document.querySelectorAll('.face')].filter((face) => {
        const head = face.querySelector('.cls-head');
        const frame = head && head.getBoundingClientRect();
        return frame && [...face.querySelectorAll('.cls-name, .cls-sub, .fn-name, .fld')].some((t) => t.getBoundingClientRect().right > frame.right + 0.5);
      }).length`);
      assert(past === 0, `${past} boxes have text past their frame`);
      assert(!(await page.eval("document.querySelector('#fanFront').textContent.includes('…')")), "no ellipsis anywhere in boxes");
      await page.clickOn("#collapseAllBtn");
      await settle();
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
      // hover only previews while nothing is selected (plain panel rules), and an earlier check left a class selected
      const blank = await qa("empty()");
      await page.click(blank.x, blank.y);
      assert(await page.eval("state.selected === null"), "a click on empty canvas cleared the old selection");
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
      await selectNode("Player");
      await page.clickOn('#details [data-act="accept"][data-name="hint"]');
      await settle();
      const note = lastNote("node:Player", "hint");
      assert(note && !note.ai && note.hint === "AI wrote this", `accepted: ${JSON.stringify(note)}`);
    });

    await step("notes: Enter saves, Esc reverts, Ctrl+Enter and blur save, status", async () => {
      await selectNode("Big");
      const big = await qa('node("Big")');
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

    await step("details: Loaded by adds a dashed line, Remove takes it away", async () => {
      await selectNode("Big");
      await page.clickOn("#loadedPick");
      await page.type("bus");
      await sleep(500);
      await page.clickOn('#details [data-act="loaded-add"]');
      await settle();
      assert(JSON.stringify(lastNote("node:Big", "loaded_by")?.loaded_by) === JSON.stringify(["res://src/bus.gd"]), `loaded_by saved: ${JSON.stringify(lastNote("node:Big", "loaded_by"))}`);
      assert(await page.eval("cy.edges('.k-loaded-by').length") === 1, "one loaded-by line drawn");
      assert(await page.eval(`/bus/.test(document.querySelector('#details dd[data-field="loaded_by"]').textContent)`), "details list the loader");
      await page.clickOn('#details [data-act="loaded-remove"]');
      await settle();
      assert(await page.eval("cy.edges('.k-loaded-by').length") === 0, "line gone after Remove");
    });

    await step("edit a field rule and see it in the ? popup", async () => {
      await page.eval(`renderHelp()`);
      await page.clickOn('#details [data-act="edit-fields"]');
      await sleep(150);
      await page.eval(`(() => { const t = document.querySelector('#details textarea[data-field="hint"][data-key="what"]'); t.value = "Team wording for hints"; })()`);
      await page.clickOn('#details [data-act="fields-save"]');
      await settle();
      assert(await page.eval(`!document.querySelector('#details .fr-input')`), "form closed after saving");
      assert(await page.eval(`/Team wording for hints/.test(document.querySelector('#details .help-list').textContent) && /\\(this project\\)/.test(document.querySelector('#details .help-list').textContent)`), "help list shows the project rule");
      await page.eval(`document.querySelector('#details [data-act="edit-fields"]').click()`);
      await sleep(150);
      await page.clickOn('#details [data-act="fields-reset"][data-field="hint"]');
      await settle();
      await page.clickOn('#details [data-act="fields-cancel"]');
      await sleep(150);
      assert(await page.eval(`!/Team wording for hints/.test(document.querySelector('#details .help-list').textContent)`), "reset goes back to the default");
    });

    await step("double-click a line zooms; breadcrumb back", async () => {
      await setLevel("End");
      const line = await qa('edge("Player", "Big")');
      assert(line, "a Player > Big line");
      await page.dblclick(line.x, line.y);
      await settle();
      assert(await page.eval("state.crumbs.length") === 1, "one breadcrumb");
      assert(await page.eval("Number(document.querySelector('#rulerCols .rcol.here').dataset.level) === Math.min(state.current.level, state.current.scale.count)"), "ruler lights the level shown");
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

    await step("open box: private and fields fold into rows, a row opens its list; Self calls switch", async () => {
      const pid = (await qa('node("Player")')).id;
      await page.clickOn('#fanFront [aria-label="Expand Player"]');
      await settle();
      const labels = () => page.eval("[...document.querySelectorAll('#fanFront .grp-label')].map((t) => t.textContent)");
      assert(JSON.stringify(await labels()) === JSON.stringify(["1 private", "2 properties"]), `folded rows: ${await labels()}`);
      assert(!(await page.eval("document.querySelectorAll('#fanFront .fld').length")), "no field text while folded");
      assert(!(await page.eval("!!document.querySelector('#fanFront rect.fn-row[aria-label=\"Player._clamp_health\"]')")), "private function folded");
      const selfLink = await page.eval(`state.current.fanout.find((l) => l.internal && l.from === ${JSON.stringify(pid)})`);
      assert(selfLink && selfLink.to_fn === "_clamp_health", "take_hit -> _clamp_health is a self call");
      const box = await page.eval(`boxOf(${JSON.stringify(pid)})`);
      const rowY = await page.eval(`sectionsAt(boxOf(${JSON.stringify(pid)})).groups[0].top + FAN.groupRow / 2 + 2`);
      assert(await page.eval(`anchorOf(boxOf(${JSON.stringify(pid)}), "_clamp_health", "right").y`) === rowY, "its line attaches to the private row");
      const others = await qa("positions()");
      await page.clickOn('#fanFront .grp-row[data-group$="#property"]');
      await settle();
      assert(await page.eval("document.querySelectorAll('#fanFront .fld').length") === 2, "properties shown after a click");
      await page.clickOn('#fanFront .grp-row[data-group$="#private"]');
      await settle();
      assert(await page.eval("!!document.querySelector('#fanFront rect.fn-row[aria-label=\"Player._clamp_health\"]')"), "private function listed after a click");
      const opened = await qa("positions()");
      assert(Object.keys(others).every((id) => id === pid || String(others[id]) === String(opened[id])), "opening groups moves no other box");
      assert((await page.eval(`boxOf(${JSON.stringify(pid)})`)).h > box.h, "the box grew");
      // Self calls: shown by default, the Show switch hides them (remembered), the legend entry follows
      const internalWires = () => page.eval(`bundlesOf(shownLinks(state.current)).filter((b) => b.internal).length`);
      assert(await page.eval("document.getElementById('showSelfCalls').checked") && (await internalWires()) > 0, "self calls shown");
      await page.clickOn("#showSelfCalls");
      await settle();
      assert((await internalWires()) === 0 && await page.eval("document.querySelector('.legend [data-fan]').classList.contains('off')"), "Self calls off: none drawn, legend off");
      assert(JSON.stringify(await qa("positions()")) === JSON.stringify(opened), "hiding self calls moves nothing");
      await open("");
      await setLevel("End");  // the steps after this one expect the deepest level
      assert(!(await page.eval("document.getElementById('showSelfCalls').checked")), "Self calls choice remembered");
      await page.clickOn('.legend [data-fan]');
      await settle();
      assert(await page.eval("document.getElementById('showSelfCalls').checked"), "the legend entry switches it back");
      await page.clickOn("#collapseAllBtn");
      await settle();
      await page.eval("state.openGroups.clear(), true");
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
      await page.eval("state.fnFilter.clear()");  // later checks open Big again without a filter
      await page.clickOn("#collapseAllBtn");
      await settle();
    });

    await step("class Properties: only public fields are listed (_hidden skipped)", async () => {
      await page.clickOn('#fanFront [aria-label="Expand Big"]');
      await settle();
      const labels = () => page.eval("[...document.querySelectorAll('#fanFront .grp-label')].map((t) => t.textContent)");
      assert(JSON.stringify(await labels()) === JSON.stringify(["1 private", "1 property"]), `folded rows: ${await labels()}`);
      assert(await page.eval("document.querySelectorAll('#fanFront .fld').length") === 0, "collapsed by default");
      await page.eval(`toggleGroup("Big#property")`);  // the Big box is taller than the screen, so no pointer click
      await settle();
      const shownFields = await page.eval("[...document.querySelectorAll('#fanFront .fld')].map((t) => t.textContent).join()");
      assert(shownFields === "count: int", `open shows the public property only: ${shownFields}`);
      mkdirSync(shots, { recursive: true });
      await page.screenshot(join(shots, "class-props.png"));
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
      await page.eval("void cy.fit(undefined, 40)");  // the taller ruler row leaves less canvas: bring Unused into view
      const unused = await qa('node("Unused")');
      assert(unused, "Unused shown");
      await page.click(unused.x + 20, unused.y + 4);
      await sleep(250);
      await page.clickOn('#details [data-act="ignore-warning"]');
      await settle();
      assert(JSON.stringify(lastNote("node:Unused", "ignore_warnings")?.ignore_warnings) === '["unused"]', "ignored");
      await page.eval("document.querySelector('#details [data-act=\"unignore-warning\"]')?.closest('details')?.setAttribute('open', '')");
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

    // ---------- Specs sidebar (DESIGN 7) ----------
    const specBox = (id) => `#specTree [data-spec="${id}"]`;
    const specCard = (sel) => `#specTree .spec-box.sel > .spec-card ${sel}`;
    async function openSpecs(query = "specs=1") {
      await open(query);
      await page.waitFor("document.getElementById('app').classList.contains('side-open') && document.querySelector('#specTree .spec-box')", { what: "specs sidebar" });
    }
    async function specWait(expr, what) {
      await page.waitFor(expr, { what });
      await settle();
    }
    const setStage = (id, stage) => page.eval(`(() => { const s = document.querySelector('${specBox(id)} .stage-pick'); s.value = ${JSON.stringify(stage)}; s.dispatchEvent(new Event("change", { bubbles: true })); })()`);
    const choose = (sel, value) => page.eval(`(() => { const s = document.querySelector(${JSON.stringify(sel)}); s.value = ${JSON.stringify(value)}; s.dispatchEvent(new Event("change", { bubbles: true })); })()`);
    const setValue = (id, value) => page.eval(`(() => { const el = document.getElementById(${JSON.stringify(id)}); el.value = ${JSON.stringify(value)}; el.dispatchEvent(new Event("input", { bubbles: true })); })()`);

    await step("Specs: nested boxes, level slider folds (+N finer), zoom with breadcrumbs, search paths", async () => {
      await openSpecs();
      const tree = () => page.eval("[...document.querySelectorAll('#specTree .spec-pick')].map((b) => b.textContent)");
      assert((await tree()).join("|") === "The game|Player moves|Big helper steps|Tools", `tree: ${await tree()}`);
      assert(await page.eval(`document.querySelector('${specBox("player")} .stage-pick').value`) === "todo", "old status planned read as stage todo");
      assert(await page.eval(`document.querySelector('${specBox("game")} .stage-pick').selectedOptions[0].textContent`) === "in progress", "stage label");
      assert(/AI draft/.test(await page.eval(`document.querySelector('${specBox("player")} > .spec-row').textContent`)), "AI draft chip");
      await page.clickOn("#specLevel");
      await page.key("Home");
      await specWait("document.querySelectorAll('#specTree .spec-pick').length === 2", "level 1 folds");
      assert(await page.eval(`document.querySelector('${specBox("game")} .spec-finer').textContent`) === "+2 finer", "+2 finer");
      await page.clickOn(`${specBox("game")} .spec-finer`);
      await specWait("document.querySelectorAll('#specCrumbs button').length === 1", "zoomed");
      assert((await tree()).join("|") === "The game|Player moves", `zoom: it and one level more: ${await tree()}`);
      assert(/All › The game/.test(await page.eval("document.getElementById('specCrumbs').textContent")), "breadcrumbs");
      await page.clickOn("#specCrumbs [data-crumb='']");
      await specWait("!document.getElementById('specCrumbs').textContent", "back to all");
      await page.clickOn("#specSearch");
      await page.type("heavy");
      await specWait("document.querySelector('#specHits [data-hit]')", "search hit");
      assert(/^The game › Player moves › Big helper steps/.test(await page.eval("document.querySelector('#specHits [data-hit]').textContent")), "hit shows its path");
    });

    await step("Specs: selecting one focuses its classes (folded ones too); a class lists its specs", async () => {
      await openSpecs("specs=1&level=3");
      await page.clickOn("#specLevel");
      await page.key("Home");
      await specWait("document.querySelectorAll('#specTree .spec-pick').length === 2", "level 1 folds");
      await page.clickOn(`${specBox("game")} .spec-pick`);
      await specWait("cy.nodes('.focus').length >= 3", "graph focus");
      const focused = await page.eval("cy.nodes('.focus').map((n) => n.data('raw').name).sort().join()");
      assert(/Big/.test(focused) && /Player/.test(focused) && /main\.gd/.test(focused), `own and folded classes lit: ${focused}`);
      assert(await page.eval("state.focus.length") === 3, "the view asked with the box's focus");
      assert(await page.eval(`[...document.querySelectorAll('${specCard(".cchip.folded")}')].map((c) => c.textContent).join()`) === "Player,Big", "folded classes as outlined chips");
      await page.clickOn(`${specBox("game")} .spec-pick`);  // again: unselect
      await specWait("!state.focus && !cy.nodes('.focus').length", "focus cleared");
      const node = await qa('node("Player")');
      await page.click(node.x, node.top + 10);
      await page.waitFor("document.querySelector('#details [data-field=cards] [data-act=open-card]')", { what: "class lists its specs" });
      assert(/The game › Player moves/.test(await page.eval("document.querySelector('#details [data-field=cards]').textContent")), "spec path in the details panel");
      await page.clickOn("#details [data-act=open-card]");
      await specWait("document.querySelector('#specTree .spec-box.sel')?.dataset.spec === 'player'", "opened from the details panel");
    });

    await step("Specs stages and priorities: quick stage picker, filter, sort, priority selects", async () => {
      await openSpecs("specs=1&level=3");
      await setStage("helper", "in-progress");
      await specWait(`document.querySelector('${specBox("helper")} .stage-pick').value === 'in-progress'`, "stage picked");
      assert(/stage: in-progress/.test(specFile("helper")), `quick stage stored:\n${specFile("helper")}`);
      assert(/work: high/.test(await page.eval(`document.querySelector('${specBox("player")} > .spec-row').textContent`)), "work priority chip");
      assert(/imp: high/.test(await page.eval(`document.querySelector('${specBox("tools")} > .spec-row').textContent`)), "importance chip");
      await choose("#specSort", "priority");
      await specWait("document.querySelector('#specHits [data-hit]')", "sorted list");
      assert(await page.eval("document.querySelector('#specHits [data-hit]').dataset.hit") === "player", "high work priority first");
      await choose("#specSort", "importance");
      await specWait("document.querySelector('#specHits [data-hit]').dataset.hit === 'tools'", "high importance first");
      // Two quick changes whose first answer arrives last: the newest choice must still win (a stale load once overwrote it).
      await page.eval(`(() => { const real = window.fetch; window.fetch = (url, ...rest) => /api\\/specs\\?/.test(String(url)) && !/stage=/.test(String(url)) ? new Promise((resolve) => setTimeout(resolve, 600)).then(() => real(url, ...rest)) : real(url, ...rest); window.__realFetch = real; })()`);
      await choose("#specSort", "");
      await choose("#specStageFilter", "implemented");
      await new Promise((resolve) => setTimeout(resolve, 1200));
      await page.eval("window.fetch = window.__realFetch, true");
      await specWait("document.querySelectorAll('#specHits [data-hit]').length === 1", "stage filter");
      assert(await page.eval("document.querySelector('#specHits [data-hit]').dataset.hit") === "tools", "old status done read as implemented");
      await choose("#specStageFilter", "");
      await specWait("document.getElementById('specHits').hidden", "filters off: the tree");
      await page.clickOn(`${specBox("helper")} .spec-pick`);
      await choose("#specImportance", "medium");
      await specWait(`/imp: medium/.test(document.querySelector('${specBox("helper")} > .spec-row').textContent)`, "importance saved");
      assert(/importance: medium/.test(specFile("helper")), "importance stored");
    });

    await step("Notes tab: same component, N/A by default, add a note, link a line", async () => {
      await openSpecs("notes=1&level=3");
      assert(await page.eval("document.getElementById('cardTabMemos').getAttribute('aria-selected')") === "true", "Notes tab");
      assert(await page.eval(`document.querySelector('${specBox("bus-note")} .stage-pick').selectedOptions[0].textContent`) === "N/A", "N/A stage");
      assert(await page.eval("[...document.querySelector('#specStageFilter').options].map((o) => o.value).join()") === ",todo,in-progress,done,na", "the kind's stages");
      await page.clickOn("#specAdd");
      await specWait("document.querySelector('#specTree .spec-box.sel')?.dataset.spec === 'new-note'", "note added");
      assert(/title: New note/.test(specFile("new-note", "memos") || "") && !/stage:/.test(specFile("new-note", "memos")), "stored in memos/, default stage");
      await setValue("specTitle", "Ask about frames");
      await page.clickOn(specCard("[data-act=save]"));
      await specWait("!document.getElementById('specTitle')", "saved");
      const edge = await qa('edge("Player", "Big")');
      await page.click(edge.x, edge.y);
      await page.waitFor(`/Player → Big/.test(document.querySelector('${specCard("[data-act=attach-sel]")}').textContent)`, { what: "line offered" });
      await page.clickOn(specCard("[data-act=attach-sel]"));
      await specWait(`document.querySelector('${specCard(".cchip [data-detach]")}')`, "line linked");
      assert(/links: \[Player>Big\]/.test(specFile("new-note", "memos")), `line link stored:\n${specFile("new-note", "memos")}`);
      await specWait("cy.edges('.focus').length === 1", "the linked line lights up");
      await page.clickOn("#cardTabSpecs");
      await specWait("document.getElementById('cardTabSpecs').getAttribute('aria-selected') === 'true' && document.querySelector('#specTree [data-spec=game]')", "back to specs");
    });

    /** Scenario: the note the Notes tab check adds ("Ask about frames", linked to the Player>Big line), when it is not there yet. */
    const ensureLinkedNote = () => {
      if (specFile("new-note", "memos")) return;
      writeFileSync(join(data, "memos", "new-note.md"), "---\nid: new-note\ntitle: Ask about frames\nlinks: [Player>Big]\nby: sam\n---\n");
    };
    await step("Links both ways: badges on classes and lines, a line's details list its notes", async () => {
      ensureLinkedNote();
      await openSpecs("specs=1&level=3");
      assert(/§\d/.test(await page.eval("cy.getElementById('Player').data('marks')")), "class badge");
      const label = await page.eval("cy.edges().filter((e) => e.data('raw').to === 'Big' && e.source().data('raw').name === 'Player')[0].data('label')");
      assert(/^§2 /.test(label), `line badge: ${label}`);
      const edge = await qa('edge("Player", "Big")');
      await page.click(edge.x, edge.y);
      await page.waitFor("document.querySelectorAll('#details [data-field=cards] [data-act=open-card]').length === 2", { what: "line lists its notes" });
      assert(/Note Ask about frames/.test(await page.eval("document.querySelector('#details [data-field=cards]').textContent")), "note in a line's details");
      await page.clickOn("#details [data-act=open-card][data-folder=memos]");
      await specWait("document.getElementById('cardTabMemos').getAttribute('aria-selected') === 'true' && document.querySelector('#specTree .spec-box.sel')", "opened on the Notes tab");
      await page.clickOn("#showCards");
      await settle();
      assert(!/§/.test(await page.eval("cy.getElementById('Player').data('marks')")), "Show menu hides the badges");
      await page.clickOn("#showCards");
      await settle();
    });

    await step("Specs editing: add, edit, attach a class, re-parent, accept, delete", async () => {
      await openSpecs("specs=1&level=3");
      await page.clickOn("#specAdd");
      await specWait("document.getElementById('specTitle') && document.querySelector('#specTree .spec-box.sel')?.dataset.spec === 'new-spec'", "new spec in edit mode");
      assert(/title: New spec/.test(specFile("new-spec") || ""), "created on the server");
      await setValue("specTitle", "Saving games");
      await setValue("specText", "Writes [[Player]] to disk.");
      await setValue("specLevelSet", "2");
      await page.clickOn(specCard("[data-act=save]"));
      await specWait("!document.getElementById('specTitle') && /Saving games/.test(document.querySelector('#specTree .spec-box.sel .spec-pick').textContent)", "saved");
      assert(/title: Saving games/.test(specFile("new-spec")) && /level: 2/.test(specFile("new-spec")) && /Writes \[\[Player\]\] to disk\./.test(specFile("new-spec")), `edit stored:\n${specFile("new-spec")}`);
      await setStage("new-spec", "review");
      await specWait(`document.querySelector('${specBox("new-spec")} .stage-pick').value === 'review'`, "stage saved");
      assert(/stage: review/.test(specFile("new-spec")), "stage stored");
      const node = await qa('node("Player")');
      await page.click(node.x, node.top + 10);
      await page.waitFor(`!document.querySelector('${specCard("[data-act=attach-sel]")}').disabled`, { what: "selection offered" });
      await page.clickOn(specCard("[data-act=attach-sel]"));
      await specWait(`document.querySelector('${specCard(".cchip [data-detach]")}')`, "class attached");
      assert(/links: \[Player\]/.test(specFile("new-spec")), `class stored:\n${specFile("new-spec")}`);
      await page.clickOn("#specPicker");
      await page.type("big");
      await page.waitFor(`document.querySelector('${specCard(".spec-picks [data-attach]")}')`, { what: "picker results" });
      await page.clickOn(specCard(".spec-picks [data-attach]"));
      await specWait(`document.querySelectorAll('${specCard(".cchip [data-detach]")}').length === 2`, "picked class attached");
      assert(/links: \[Player, Big\]/.test(specFile("new-spec")), `picked class stored:\n${specFile("new-spec")}`);
      await page.clickOn(specCard("[data-act=in]"));  // under the spec above it (Tools)
      await specWait(`document.querySelector('${specBox("tools")} .spec-kids [data-spec="new-spec"]')`, "re-parented");
      assert(/parent: tools/.test(specFile("new-spec")), "parent stored");
      await page.clickOn(specCard("[data-act=out]"));
      await specWait(`!document.querySelector('${specBox("tools")} .spec-kids')`, "moved out");
      assert(!/parent:/.test(specFile("new-spec")), "back at the top level");
      await page.clickOn(`${specBox("player")} .spec-pick`);
      await specWait(`document.querySelector('${specCard("[data-act=accept]")}')`, "player selected");
      await page.clickOn(specCard("[data-act=accept]"));
      await specWait(`!document.querySelector('${specCard("[data-act=accept]")}')`, "accepted");
      assert(!/ai:/.test(specFile("player")) && /by: Sam Tester/.test(specFile("player")), `accepted:\n${specFile("player")}`);
      await page.clickOn(`${specBox("game")} .spec-pick`);
      await page.clickOn(specCard("[data-act=delete]"));
      await page.waitFor(`/sub-item move up/.test(document.querySelector('${specCard(".spec-confirm")}')?.textContent || "")`, { what: "in-page confirm" });
      await page.clickOn(specCard("[data-act=delete-yes]"));
      await specWait("!document.querySelector('#specTree [data-spec=\"game\"]')", "deleted");
      assert(specFile("game") === null && !/parent:/.test(specFile("player")), "deleted; its child moved to the top");
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

    await step("Tours guide: step highlight in the graph, camera glide, line note popups", async () => {
      await openTour("tab=tours&tour=first-steps&step=1");
      const inView = "cy.nodes('.focus').length > 0 && cy.nodes('.focus').every((n) => { const b = n.renderedBoundingBox(); return b.x1 >= 0 && b.y1 >= 0 && b.x2 <= cy.width() && b.y2 <= cy.height(); })";
      await page.waitFor(`!TourView.state.gliding && state.tourStep && ${inView}`, { what: "step framed in the canvas" });
      const lit = await page.eval("[...document.querySelectorAll('#fanFront .fn.lit .fn-name')].map((t) => t.textContent).join()");
      assert(lit === "_ready()", `focused function lit: ${lit}`);
      assert(await page.eval("document.querySelectorAll('#fanBack .wire.hot, #fanFront .wire.hot').length") > 0, "its lines lit");
      const more = await page.eval("[...document.querySelectorAll('#fanFront .grp-label')].map((t) => t.textContent).join()");
      assert((more.match(/\d+ more/g) || []).length >= 2, `the called classes open too, other functions folded: ${more}`);
      assert(await page.eval(inView), "step framed in the canvas");
      // the next step glides: the camera passes through in-between spots
      await page.eval("(() => { window.__cam = []; const t0 = performance.now(); const tick = () => { __cam.push(Math.round(cy.pan().x)); if (performance.now() - t0 < 900) requestAnimationFrame(tick); }; tick(); })()");
      await page.clickOn("#tourNext");
      await page.waitFor("TourView.state.cur === 1 && !TourView.state.gliding && state.current.focus?.nodes.includes('Player')", { what: "step 2 framed" });
      await sleep(300);
      const cams = await page.eval("[...new Set(window.__cam)].length");
      assert(cams >= 4, `camera glides (${cams} distinct spots)`);
      assert(await page.eval(inView), "step 2 framed in the canvas");
      // line notes: all shown while browsing; hidden at play start, popping up at their cue
      await page.clickOn("#tourBack");
      await page.waitFor("TourView.state.cur === 0 && !TourView.state.gliding", { what: "back on step 1" });
      const note = await page.eval("(() => { const n = document.querySelector('#codeSrc .lnote'); return n && [n.classList.contains('on'), n.textContent, n.previousElementSibling.querySelector('.no').textContent]; })()");
      assert(note && note[0] && note[1] === "Hands over to Player#spawn." && note[2] === "12", `note under line 12, shown: ${JSON.stringify(note)}`);
      assert(await page.eval("document.querySelectorAll('#codeSrc .ln.noted').length") === 1, "its line marked");
      await page.clickOn("#tourPlay");
      await page.waitFor("TourView.player.playing", { what: "playing" });
      assert(await page.eval("!document.querySelector('#codeSrc .lnote').classList.contains('on') || TourView.player.word >= TourView.state.noteCues[0]"), "hidden until the narration reaches it");
      await page.eval("(() => { const bar = document.getElementById('seekBar'); bar.value = 950; bar.dispatchEvent(new Event('input')); })()");
      await page.waitFor("document.querySelector('#codeSrc .lnote.on')", { what: "note pops up at its cue" });
      await page.clickOn("#tourPlay");  // pause
    });

    await step("Tours read notes", async () => {
      // A fake voice (no sound): each utterance starts after the one before and ends 12 ms per character later.
      await openTour("tab=tours&tour=first-steps&step=1");
      await page.eval(`(() => {
        const env = TourView.player.env;
        window.__realSynth = env.synth;
        window.__said = [];
        let timers = [], busy = Promise.resolve(), gen = 0;
        env.synth = {
          getVoices: () => [{ name: "fake", lang: "en-US", localService: true }],
          cancel: () => { gen++; timers.forEach(clearTimeout); timers = []; busy = Promise.resolve(); },
          speak: (u) => {
            const mine = gen;
            busy = busy.then(() => new Promise((done) => {
              if (mine !== gen) return done();
              __said.push(u.text);
              u.onstart?.();
              timers.push(setTimeout(() => { if (mine === gen) u.onend?.(); done(); }, 12 * u.text.length));
            }));
          },
        };
      })()`);
      assert(await page.eval("localStorage.getItem('coderstanding-tour-read-notes')") !== "1", "off by default");
      await page.clickOn("#tourReadNotes");
      assert(await page.eval("TourView.player.readNotes && localStorage.getItem('coderstanding-tour-read-notes') === '1'"), "on and remembered");
      await page.clickOn("#tourPlay");
      await page.waitFor("TourView.player.readingNote === 0", { what: "the note is being read", timeout: 15000 });
      assert(await page.eval("document.querySelector('#codeSrc .lnote.reading')?.textContent.includes('Hands over')"), "popup marked as being read");
      assert(await page.eval("__said.includes(\"Hands over to Player spawn.\")"), "the note was spoken");
      const held = await page.eval("[TourView.player.track.pos, TourView.player.word]");
      await sleep(150);
      assert(JSON.stringify(await page.eval("[TourView.player.track.pos, TourView.player.word]")) === JSON.stringify(held), "clock and highlight held while the note is read");
      await page.waitFor("TourView.player.readingNote === -1 && TourView.player.playing && !document.querySelector('#codeSrc .lnote.reading')", { what: "narration resumes", timeout: 15000 });
      await page.waitFor("TourView.player.track.at > 0 && __said.length > 2", { what: "step spoken again" });
      await page.clickOn("#tourPlay");  // pause
      // off again: remembered per viewer, and the note is no longer read
      await page.clickOn("#tourReadNotes");
      assert(await page.eval("!TourView.player.readNotes && localStorage.getItem('coderstanding-tour-read-notes') === '0'"), "off and remembered");
      await page.eval("TourView.player.env.synth = __realSynth");
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

    await step("Tours player: slider and highlight move on the clock, seek by characters", async () => {
      await openTour("tab=tours&tour=first-steps&step=1");
      assert((await page.eval("document.getElementById('seekDur').textContent")).startsWith("~"), "voice length is an estimate");
      await page.clickOn("#tourPlay");
      await page.waitFor("+document.getElementById('seekBar').value > 30 && document.querySelector('#tourPanel .w.now')", { what: "slider and highlight move" });
      await page.clickOn("#tourPlay");  // pause
      await page.eval("(() => { const bar = document.getElementById('seekBar'); bar.value = 500; bar.dispatchEvent(new Event('input')); })()");
      const word = await page.eval("TourView.player.word");
      const middle = await page.eval("CoderTours.wordAtChar(TourView.player.track.starts, TourView.player.track.total / 2)");
      assert(word === middle && word > 3, `seek to the middle (by characters): word ${word}, expected ${middle}`);
      const now = await page.eval("+document.querySelector('#tourPanel .w.now').dataset.s");
      assert(now <= word && await page.eval("document.querySelectorAll('#tourPanel .w.said').length") > 3, "highlight at the seeked word, earlier words dim");
    });

    await step("Tours speech: chunks in step with the voice (speech-dispatcher voices in Chromium)", async () => {
      await openTour("tab=tours&tour=first-steps&step=1");
      const voices = await page.eval("speechSynthesis.getVoices().length");
      if (!voices) { console.log("     skip: real speech is off (CODERSTANDING_E2E_SPEECH=1 to hear it)"); return; }
      await page.eval("window.__speech.length = 0");
      const voice = await page.eval("document.getElementById('tourVoice').value");
      // chunk sync: every chunk's start puts the highlight on its first word (sampled every frame)
      await page.eval("(() => { window.__words = []; const tick = () => { if (!window.__words) return; __words.push([performance.now(), TourView.player.word]); requestAnimationFrame(tick); }; tick(); })()");
      await page.clickOn("#tourPlay");  // a real click: Chromium speaks only after a user gesture
      await page.waitFor("__speech.some((e) => e === 'start' || e.startsWith('error'))", { what: "speech start", timeout: 8000 });
      const events = await page.eval("__speech.slice()");
      assert(!events.some((e) => e.startsWith("error")), `speech error: ${events}`);
      // chunk sync: every chunk's start puts the highlight on its first word (sampled every frame)
      const chunks = await page.eval("TourView.player.track.chunks");
      await page.waitFor(`__speechLog.filter((e) => e.type === "end").length >= ${chunks.length}`, { what: "every chunk spoken", timeout: 30000 })
        .catch(async (error) => { throw new Error(`${error.message}: ${JSON.stringify(await page.eval("__speechLog.map((e) => [Math.round(e.t), e.type, e.text.slice(0, 20)])"))}`); });
      await page.clickOn("#tourPlay");  // pause before the next step
      const { log, samples } = await page.eval("(() => { const out = { log: __speechLog.slice(), samples: __words.slice() }; window.__words = null; return out; })()");
      const starts = log.filter((e) => e.type === "start");
      assert(starts.length === chunks.length, `one start per chunk: ${starts.length} of ${chunks.length}`);
      const rows = starts.map((e, k) => {
        const before = samples.filter(([t]) => t < e.t).at(-1)?.[1] ?? -1;   // the clock's guess just before
        const after = samples.find(([t]) => t > e.t + 20)?.[1] ?? -1;        // after the start event
        return { chunk: k, first: chunks[k].first, before, after };
      });
      console.log(`     ${voices} voices, "${voice}"; chunk starts (first word / highlight just before / after): ${rows.map((r) => `${r.first}/${r.before}/${r.after}`).join(" ")}; ${await page.eval("__speech.filter((e) => e === 'boundary').length")} boundary events`);
      for (const r of rows) assert(r.after === r.first, `chunk ${r.chunk} start: highlight ${r.after}, first word ${r.first}`);
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

    await step("Refs: Copy text | Copy location on a tour step and on selected code lines; a ?ref= link opens its view (answer stubbed)", async () => {
      await openTour("tab=tours&tour=first-steps&step=2");
      await page.grantClipboard(base.slice(0, -1));
      const readClip = (type) => page.eval(`navigator.clipboard.read().then(async (items) => (await items[0].getType(${JSON.stringify(type)})).text())`);
      await page.clickOn("#tourPanel .stephead [data-copy-text]");
      await page.waitFor("/Copied text/.test(document.querySelector('#tourPanel .stephead [data-copy-text]').textContent)", { what: "copied text" });
      const stepText = await page.eval("navigator.clipboard.readText()");
      assert(/^\[\[Player#spawn\]\] tells everyone/.test(stepText), `step text: ${stepText}`);
      await page.clickOn("#tourPanel .stephead [data-copy-ref]");
      await page.waitFor("/Copied location/.test(document.querySelector('#tourPanel .stephead [data-copy-ref]').textContent)", { what: "copied location" });
      const text = await readClip("text/plain"), html = await readClip("text/html");
      assert(text === "@fixture tour first-steps step 2 (The player spawns)", `step location: ${text}`);
      assert(html.includes("?app=fixture&amp;ref=%40fixture%20tour%20first-steps%20step%202") && html.includes(">@fixture tour first-steps step 2"), `step link: ${html}`);
      // selected code lines: Ctrl+C is the normal copy, Ctrl+Shift+C the location
      await page.eval(`(() => {
        const lines = document.querySelectorAll('#codeSrc .ln'), range = document.createRange();
        range.setStart(lines[0].firstChild.firstChild, 0);
        range.setEnd(lines[1].lastChild, lines[1].lastChild.length);
        getSelection().removeAllRanges(); getSelection().addRange(range);
      })()`);
      const plain = await page.eval("(() => { const e = new ClipboardEvent('copy', { clipboardData: new DataTransfer(), cancelable: true, bubbles: true }); document.getElementById('codeSrc').dispatchEvent(e); return e.defaultPrevented; })()");
      assert(plain === false, "Ctrl+C on code is left to the browser (normal copy)");
      await page.key("c", { ctrl: true, shift: true });
      await page.waitFor("/Copied location/.test(document.getElementById('tourToast').textContent)", { what: "Ctrl+Shift+C" });
      assert(await page.eval("navigator.clipboard.readText()") === "@fixture src/player.gd:8-9 (Player#spawn +0..+1)", "Ctrl+Shift+C copies the lines' location");
      // the right-click menu on the selection offers both; Copy text = the raw lines, no numbers
      await page.eval("document.getElementById('codeSrc').dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 300, clientY: 200 }))");
      await page.waitFor("!!document.querySelector('.copy-menu [data-copy-ref]')", { what: "context menu" });
      assert(await page.eval("document.querySelector('.copy-menu [data-copy-ref]').dataset.copyRef") === "@fixture src/player.gd:8-9 (Player#spawn +0..+1)", "menu location is the selection's");
      await page.clickOn(".copy-menu [data-copy-text]");
      await page.waitFor("/Copied text/.test(document.querySelector('.copy-menu [data-copy-text]')?.textContent)", { what: "menu copy text" });
      const code = await page.eval("navigator.clipboard.readText()");
      assert(code.split("\n").length === 2 && !/^\d/.test(code), `raw lines: ${JSON.stringify(code)}`);
      // the code pane's head has the pair for the whole excerpt; a step's right-click menu has both too
      assert(await page.eval("document.querySelectorAll('#tourCode .codehead .copy-pair button').length") === 2, "code head pair");
      await page.eval("getSelection().removeAllRanges()");
      await page.eval("document.getElementById('stepText').dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 100, clientY: 100 }))");
      assert(await page.eval("document.querySelector('.copy-menu [data-copy-ref]')?.dataset.copyRef") === "@fixture tour first-steps step 2 (The player spawns)", "step menu location");
      await page.key("Escape");
      assert(await page.eval("!document.querySelector('.copy-menu')"), "Esc closes the menu");
      // a ?ref= link: /api/ref (stubbed; the backend is another branch) names the view, the page opens it
      const search = await page.eval(`(async () => {
        history.replaceState(null, "", "/?app=fixture&ref=" + encodeURIComponent("@fixture tour first-steps step 3"));
        const real = window.fetch;
        window.fetch = (url, ...rest) => String(url).includes("/api/ref?")
          ? Promise.resolve(new Response(JSON.stringify({ ok: true, kind: "step", view: { tab: "tours", tour: "first-steps", step: 3 } }), { status: 200 }))
          : real(url, ...rest);
        const found = await openRefFromUrl();
        window.fetch = real;
        return { search: location.search, kind: found.kind };
      })()`);
      assert(search.kind === "step" && !search.search.includes("ref=") && /tab=tours/.test(search.search) && /step=3/.test(search.search), `rewritten address: ${search.search}`);
      await openTour(search.search.slice(1));
      assert(await tourPanel(".stepno") === "Step 3/3", `link opened step: ${await tourPanel(".stepno")}`);
      // the view options a ref's answer names: hl lights the step's lines, file + hl says where, select shows the class's pair
      await openTour("tab=tours&tour=first-steps&step=2&hl=9-10");
      assert(await page.eval("document.querySelectorAll('#codeSrc .ln.ref-hit').length") === 2, "hl lights two lines");
      await open("file=src/player.gd&hl=8-9");
      const where = await page.eval("document.getElementById('refNote').hidden ? '' : document.getElementById('refNoteText').textContent");
      assert(/src\/player\.gd:8-9/.test(where), `file banner: ${where}`);
      await open("level=9&select=Player");
      await page.waitFor("!!document.querySelector('#details [data-copy-ref]')", { what: "class details pair" });
      assert(await page.eval("document.querySelector('#details [data-copy-ref]').dataset.copyRef") === "@fixture Player", "class location");
      // a miss: a short message with the did-you-mean names
      await page.eval(`(async () => {
        history.replaceState(null, "", "/?app=fixture&ref=" + encodeURIComponent("@fixture Plaer"));
        window.fetch = () => Promise.resolve(new Response(JSON.stringify({ ok: false, error: "no such class", did_you_mean: ["Player"] }), { status: 404 }));
        await openRefFromUrl();
      })()`);
      const miss = await page.eval("document.getElementById('refNote').hidden ? '' : document.getElementById('refNoteText').textContent");
      assert(/no such class/.test(miss) && /Did you mean: Player/.test(miss), `miss banner: ${miss}`);
    });

    await step("Tours editor: add a step from the selection, reorder, save; record a step", async () => {
      await openTour("tab=tours&tour=first-steps&step=2&edit=1");
      assert(await page.eval("document.getElementById('tabTours').classList.contains('editing') && !!document.querySelector('#tourPanel .tour-editbar')"), "edit bar and stripes");
      assert(await page.eval("document.getElementById('edLines').value") === "+0..+2", "existing step's lines as offsets (code.first)");
      // edit step 2: text, and the excerpt one line shorter with one marked line
      await page.eval(`(() => { const set = (id, v) => { const el = document.getElementById(id); el.value = v; el.dispatchEvent(new Event("input", { bubbles: true })); };
        set("edText", "Edited by hand: [[Player#spawn]] announces the player."); set("edLines", "+0..+1"); set("edMark", "9"); })()`);
      await page.waitFor("!TourView.state.gliding", { what: "step framed" });
      await page.eval("cy.center(cy.getElementById('Player')) && true");  // the step's own layout may put it anywhere
      await sleep(100);
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
      assert(/key: s1\n[^]*?notes: \[\{lines: \+4\.\.\+4, text: "Hands over to \[\[Player#spawn\]\]\.", cue: announces the start\}\]/.test(file), "the editor sends a step's line notes back unchanged");
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

    await step("Tours: comments on a step and a code line, reply, resolve, badges", async () => {
      const commentsFile = join(data, "tours", "first-steps.comments.jsonl");
      await openTour("tab=tours&tour=first-steps&step=1");
      await page.waitFor("document.querySelector('#tourComments [data-cdraft=new]')", { what: "comment box" });
      await page.clickOn("#tourComments [data-cdraft=new]");
      await page.type("Explain the **bus** here");
      await page.clickOn("#tourComments [data-cact=send][data-ckey=new]");
      await page.waitFor("document.querySelectorAll('#tourComments .cthread').length === 1", { what: "step comment shown" });
      const me = await page.eval("CoderComments.state.me");
      assert(me && me !== "you", `comments carry a real name: ${me}`);
      assert(await page.eval("document.querySelector('#tourComments .cbody b')?.textContent") === "bus", "Markdown rendered");
      assert(await page.eval("document.querySelector('#tourDots [data-go=\"0\"] .cbadge')?.textContent") === "1", "badge on dot 1");
      assert(await page.eval("document.querySelector('#tourPanel .steplist [data-go=\"0\"] .cbadge')?.textContent") === "1", "badge in the step list");
      // a code line: the gutter's "+"
      const line = await page.eval("+document.querySelectorAll('#codeSrc .ln')[1].querySelector('.no').textContent");
      await page.clickOn("#codeSrc .ln:nth-child(2) .cadd");
      await page.waitFor(`document.querySelector('#tourComments .cline')?.textContent.includes('Line ${line}')`, { what: "line picked" });
      await page.clickOn("#tourComments [data-cdraft=new]");
      await page.type("Why this call?");
      await page.clickOn("#tourComments [data-cact=send][data-ckey=new]");
      await page.waitFor("document.querySelectorAll('#tourComments .cthread').length === 2", { what: "line comment shown" });
      assert(await page.eval(`document.querySelector('#codeSrc .ln:nth-child(2) .cmark')?.textContent`) === "1", "line marked in the gutter");
      assert(await page.eval("document.querySelector('#tourDots [data-go=\"0\"] .cbadge').textContent") === "2", "badge counts 2");
      assert(await page.eval("document.querySelector('#tourList [aria-current=true] .cbadge')?.textContent") === "2", "open count on the tour in the list");
      // reply to the first thread, then resolve it (folded)
      await page.clickOn("#tourComments .cthread [data-cact=reply]");
      await page.waitFor("document.querySelector('#tourComments [data-cdraft^=reply]')", { what: "reply box" });
      await page.clickOn("#tourComments [data-cdraft^=reply]");
      await page.type("On it.");
      await page.clickOn("#tourComments [data-cact=send][data-ckey^=reply]");
      await page.waitFor("document.querySelectorAll('#tourComments .cthread:first-child .cmt').length === 2", { what: "reply shown" });
      await page.clickOn("#tourComments .cthread [data-cact=resolve]");
      await page.waitFor("document.querySelector('#tourComments details.cthread.resolved:not([open])')", { what: "resolved thread folded" });
      assert(await page.eval("document.querySelector('#tourDots [data-go=\"0\"] .cbadge').textContent") === "1", "badge drops to 1");
      const rows = readFileSync(commentsFile, "utf8").trim().split("\n").map((l) => JSON.parse(l));
      assert(rows.length === 3 && rows.every((r) => r.by === me) && rows.some((r) => r.anchor && r.resolved === undefined)
        && rows.some((r) => r.resolved?.by === me), `comments file: ${JSON.stringify(rows)}`);
      const cliText = execFileSync(cli, ["tour", fixture, "first-steps", "--comments", "--data", data], { env: { ...process.env, ...isolated } }).toString();
      assert(/comments: 1 open, 1 resolved/.test(cliText) && cliText.includes(`main.gd:${line}`), `CLI comments: ${cliText}`);
      // leave no open threads for later checks
      await page.clickOn("#tourComments .cthread:not(.resolved) [data-cact=resolve]");
      await page.waitFor("!document.querySelector('#tourDots .cbadge')", { what: "no open comments" });
    });

    await step("Tours: back to the graph tab", async () => {
      await openTour("tab=tours&tour=first-steps&step=1");
      await page.clickOn("#tabGraphBtn");
      await settle();
      assert(await page.eval("document.getElementById('canvas').parentElement.classList.contains('stage') && !state.focus"), "canvas home, no focus");
      assert(await page.eval("!document.getElementById('tabGraph').hidden && document.getElementById('tabTours').hidden"), "graph tab shown");
    });

    await step("Tour view options: a tour's and a step's view apply while it plays, leaving restores yours, editor View section", async () => {
      const file = join(data, "tours", "view-steps.md");
      writeFileSync(file, readFileSync(fixtureTour, "utf8").replace("id: first-steps\ntitle: How the game starts\n",
        "id: view-steps\ntitle: Viewed steps\nview: {lines: all, selfcalls: false, hide_kind: [dependency]}\n")
        .replace("    by: claude\n    ai: true\n", "    by: claude\n    ai: true\n    view: {boxtext: full}\n"));
      try {
        await open("level=9&boxtext=saved");  // the user's own switches (no URL override of box text)
        const mine = await page.eval("JSON.stringify({ lines: state.lines, kinds: state.hiddenKinds, fan: state.fanHidden, box: state.boxText })");
        await openTour("tab=tours&tour=view-steps&step=1&boxtext=saved");
        assert(await page.eval("state.lines === 'all' && state.fanHidden.includes('self') && state.hiddenKinds.includes('dependency')"), "the tour's view applies");
        assert(await page.eval("(state.current.hidden_kinds || []).includes('dependency')"), "the step graph asks the server without those lines");
        assert(/Lines: All equal/.test(await tourPanel("#stepView")), `step panel says the view: ${await tourPanel("#stepView")}`);
        await page.clickOn("#tourNext");
        await settle();
        assert(await page.eval("state.boxText === 'full' && state.lines === 'all'"), "step 2's own view on top of the tour's");
        await page.clickOn("#tourBack");
        await settle();
        assert(await page.eval(`state.boxText === ${JSON.stringify(JSON.parse(mine).box)}`), "back on step 1: the user's box text");
        await page.clickOn("#tabGraphBtn");
        await settle();
        assert(await page.eval("JSON.stringify({ lines: state.lines, kinds: state.hiddenKinds, fan: state.fanHidden, box: state.boxText })") === mine, "leaving the Tours tab restores the user's options");
        // an old tour (no view) plays with the user's options
        await openTour("tab=tours&tour=first-steps&step=1&boxtext=saved");
        assert(await page.eval("JSON.stringify({ lines: state.lines, kinds: state.hiddenKinds, fan: state.fanHidden, box: state.boxText })") === mine, "old tour: unchanged");
        // the URL beats the tour
        await openTour("tab=tours&tour=view-steps&step=1&boxtext=saved&lines=explained");
        assert(await page.eval("state.lines === 'explained' && state.fanHidden.includes('self')"), "URL > tour");
        // editor: a View section built from the table, tour-wide or this step only, Use current graph settings, Save
        await openTour("tab=tours&tour=view-steps&step=1&edit=1&boxtext=saved");
        await page.eval("document.getElementById('edViewBox').open = true, true");
        const count = await page.eval("document.querySelectorAll('#edViewBox [data-view-opt]').length");
        assert(count === await page.eval("ViewOptions.TOUR_OPTIONS.length"), `one control per tour option: ${count}`);
        assert(await page.eval("document.getElementById('edView-lines').value === 'all' && document.getElementById('edView-selfCalls').value === 'false'"), "controls show the tour's view");
        assert(!(await page.eval("document.getElementById('edViewBox').textContent.includes('Claude')")), "UI says AI");
        await page.clickOn('#edViewBox [data-view-scope="step"]');
        assert(await page.eval("/tour: All equal/.test(document.getElementById('edView-lines').options[0].textContent)"), "a step shows what the tour sets");
        await page.eval("(() => { const el = document.getElementById('edView-boxText'); el.value = 'name'; el.dispatchEvent(new Event('change', { bubbles: true })); })()");
        await settle();
        assert(await page.eval("state.boxText === 'name'"), "a live preview of the edited view");
        await page.clickOn('#edViewBox [data-view-scope="tour"]');
        await page.clickOn('#edViewBox [data-act="view-current"]');
        const expected = await page.eval("JSON.stringify(ViewOptions.toStored(ViewOptions.diff(graphOptions(), ViewOptions.defaults(), ViewOptions.TOUR_OPTIONS.map((o) => o.key))))");
        assert(await page.eval("JSON.stringify(TourView.state.draft.view)") === expected, `Use current graph settings: ${expected}`);
        await page.clickOn('#tourPanel [data-act="save"]');
        await settle();
        const saved = readFileSync(file, "utf8");
        assert(/\n  - key: s1\n(    .*\n)*    view: \{boxtext: name\}\n/.test(saved), `step 1's view saved: ${saved.split("---")[1]}`);
        assert(await page.eval("JSON.stringify(TourView.state.tour.view)") === expected, "the tour's view saved");
      } finally {
        rmSync(file, { force: true });
        await open();
      }
    });

    await step("Presets: Read / Explore / Edit, Custom, More options marks, remembered per app, ?preset=, search box", async () => {
      const q = "boxtext=saved&overview=saved&details=saved&level=9";  // no URL switches: what the preset says
      await open(`${q}&preset=&more=closed`);
      assert(await page.eval("document.getElementById('moreOptions').hidden && document.getElementById('moreBtn').getAttribute('aria-expanded') === 'false'"), "More options closed");
      assert(await page.eval("['presetCtl', 'graphSearch', 'ruler', 'moreBtn'].every((id) => document.getElementById(id).closest('#calmbar'))"), "one calm row: preset, search, ruler, More options");
      await page.clickOn("#moreBtn");
      assert(await page.eval("!document.getElementById('moreOptions').hidden && /Fewer options/.test(document.getElementById('moreBtn').textContent)"), "More options opens");
      await page.clickOn("#presetRead");
      await settle();
      const pressed = () => page.eval("[...document.querySelectorAll('#presetCtl [aria-pressed=true]')].map((b) => b.dataset.preset).join()");
      assert(await pressed() === "read" && await page.eval("document.getElementById('presetCustom').hidden"), "Read pressed");
      assert(await page.eval("!state.show.external && !state.show.test && state.fanHidden.includes('self') && state.domainView.includes('on') && state.boxText === 'short' && state.lines === 'explained'"), "Read's switches");
      assert(await page.eval("document.getElementById('showExternal').closest('label').querySelector('.set-by')?.textContent === 'Read'"), "More options marks what the preset set");
      assert(/^Read:/.test(await page.eval("document.getElementById('presetNote').textContent")), "the note says what Read is");
      await page.clickOn("#showExternal");
      await settle();
      assert(await pressed() === "" && !(await page.eval("document.getElementById('presetCustom').hidden")), "a changed switch: Custom");
      assert(await page.eval("document.getElementById('showExternal').closest('label').querySelector('.set-by.changed')"), "the changed switch is marked");
      assert(await page.eval("JSON.stringify(JSON.parse(localStorage.getItem('coderstanding-view:fixture')))") === JSON.stringify({ preset: "read", changed: { external: true } }), "Custom remembers the preset and the changed switches");
      await open(`${q}&preset=`);
      assert(await page.eval("state.show.external && state.fanHidden.includes('self') && !document.getElementById('presetCustom').hidden"), "remembered per app after a reload");
      await page.clickOn("#presetExplore");
      await settle();
      assert(await pressed() === "explore" && await page.eval("state.show.external && !state.fanHidden.length && !state.domainView.includes('on')"), "Explore = the switches as they always were");
      assert(await page.eval("JSON.parse(localStorage.getItem('coderstanding-view:fixture')).changed.external === undefined"), "a preset drops the changes");
      await page.clickOn("#presetEdit");
      await settle();
      assert(await page.eval("state.mode === 'edit' && document.getElementById('app').classList.contains('editing')"), "Edit = Edit layout on");
      await open(`${q}&preset=read`);
      assert(await page.eval("state.mode === 'view' && !state.show.external"), "?preset=read");
      assert(await page.eval("JSON.parse(localStorage.getItem('coderstanding-view:fixture')).preset") === "edit", "?preset= is not saved");
      // search: dims the classes whose name lacks the words, Enter selects the first, Esc clears
      await page.clickOn("#graphSearch");
      await page.type("playe");
      await sleep(150);
      assert(await page.eval("cy.nodes().filter((n) => !n.hasClass('dim')).map((n) => n.data('raw').name).join() === 'Player'"), "search dims the rest");
      await page.key("Enter");
      await settle();
      assert(await page.eval("state.selected?.raw?.name === 'Player'"), "Enter selects the match");
      await page.key("Escape");
      await sleep(100);
      assert(await page.eval("!cy.nodes('.dim').length"), "Esc clears the search");
      // phone width: the calm row wraps, nothing sticks out
      await page.conn.send("Emulation.setDeviceMetricsOverride", { width: 400, height: 860, deviceScaleFactor: 1, mobile: true });
      await sleep(300);
      const over = await page.eval("[...document.querySelectorAll('#calmbar > *')].filter((el) => el.getBoundingClientRect().right > window.innerWidth + 1).map((el) => el.id || el.className)");
      assert(!over.length, `calm row fits a phone: ${over}`);
      await page.conn.send("Emulation.clearDeviceMetricsOverride", {});
      await page.clickOn("#presetExplore");  // the other checks expect the switches as they always were
      await settle();
      await open();
    });

    await step("Domains: frames, fold, Every line, drag, remembered, double-click focus, level, Esc", async () => {
      const notesFile = join(data, "notes.jsonl");
      const saved = existsSync(notesFile) ? readFileSync(notesFile) : null;
      try {
        for (const [cls, domain] of [["Player", "Game"], ["res://src/main.gd", "Game"], ["Big", "World"]]) {
          execFileSync(cli, ["set", fixture, cls, `domain=${domain}`, "--data", data]);
        }
        await open("level=1");
        await setLevel("End");
        assert(await page.eval("!document.getElementById('showDomains').disabled"), "Domains toggle is live");
        assert(await page.eval("document.getElementById('betweenCtl').classList.contains('off')"), "Between frames greyed while off");
        await page.clickOn("#showDomains");
        await settle(700);
        const frames = await qa("frames()");
        assert(["Game", "Unsorted", "World"].every((f) => frames.includes(f)), `frames: ${frames}`);
        assert(await page.eval("!document.getElementById('betweenCtl').classList.contains('off')"), "Between frames live");
        assert(await page.eval("cy.edges('.dom-hide').length > 0 && document.querySelectorAll('#domFront .dom-bundle').length > 0"), "lines between frames bundled");
        // opening a class keeps every other box and frame where it is; only its own frame grows
        const frameRects = () => page.eval("Object.fromEntries(dom.frames.map((b) => [b.name, openFrameRect(b)]))");
        const bigNode = await page.eval("cy.nodes().filter((n) => n.data('raw').name === 'Big').id()");
        const boxesClosed = await qa("positions()"), framesClosed = await frameRects();
        await page.eval(`toggleExpand(${JSON.stringify(bigNode)})`);
        await settle(700);
        const boxesOpen = await qa("positions()"), framesOpen = await frameRects();
        const movedBoxes = Object.keys(boxesClosed).filter((id) => id !== bigNode && String(boxesClosed[id]) !== String(boxesOpen[id]));
        assert(!movedBoxes.length, `opening Big moved boxes: ${movedBoxes}`);
        const movedFrames = Object.keys(framesClosed).filter((n) => n !== "World" && JSON.stringify(framesClosed[n]) !== JSON.stringify(framesOpen[n]));
        assert(!movedFrames.length, `opening Big moved frames: ${movedFrames}`);
        const [wc, wo] = [framesClosed.World, framesOpen.World];
        assert(wo.x === wc.x && wo.y === wc.y && wo.w >= wc.w && wo.h > wc.h, `World grows in place: ${JSON.stringify(wc)} -> ${JSON.stringify(wo)}`);
        await page.eval(`toggleExpand(${JSON.stringify(bigNode)})`);
        await settle(700);
        assert(JSON.stringify(await qa("positions()")) === JSON.stringify(boxesClosed) && JSON.stringify(await frameRects()) === JSON.stringify(framesClosed), "closing restores boxes and frames");
        // a click on the header folds the frame into a card; Every line; a click on the card opens it
        await page.clickOn('#domFront .dom-frame.header[data-frame="Game"] .head');
        await settle(800);
        assert(await page.eval("dom.folded.has('Game') && cy.nodes('.dom-gone').length === 2"), "Game folded, its boxes in the card");
        await page.clickOn("#everyBtn");
        await settle();
        assert(await page.eval("state.domainView.includes('every') && cy.edges('.dom-hide').every((e) => [e.source(), e.target()].some((n) => dom.frameOf.get(n.id()) === 'Game'))"), "Every line: only lines on the folded card are redrawn");
        await page.clickOn('#domFront .dom-frame.card[data-frame="Game"] .head');
        await settle(800);
        assert(await page.eval("!dom.folded.size && !cy.nodes('.dom-gone').length"), "card opens again");
        // drag a frame by its body: its boxes move with it, nothing saved
        const body = await page.eval(`(() => { const b = dom.byName.get("World"); const r = frameRect(b); const box = cy.container().getBoundingClientRect();
          const z = cy.zoom(), p = cy.pan(); return { x: box.x + (r.x + 6) * z + p.x, y: box.y + (r.y + r.h - 6) * z + p.y }; })()`);
        const before = await qa("positions()");
        await page.drag(body, { x: body.x + 60, y: body.y + 40 });
        await settle();
        const after = await qa("positions()");
        const big = await page.eval("cy.nodes().filter((n) => n.data('raw').name === 'Big').id()");
        assert(after[big][0] > before[big][0] + 20, "Big moved with its frame");
        assert(await page.eval("dom.stack.at(-1) === 'World'"), "dragged frame on top");
        // remembered per app
        await open("");
        await setLevel("End");
        assert(await page.eval("state.domainView.includes('on') && state.domainView.includes('every') && dom.frames.length > 0"), "toggles remembered");
        // double-click a header: just that domain; the level still works; Esc goes back
        await page.dblclick(...Object.values(await page.centreOf('#domFront .dom-frame.header[data-frame="Game"] .head')));
        await settle(700);
        assert(await page.eval("state.current.domain === 'Game' && !dom.frames.length"), "Game focused");
        assert(await page.eval("cy.nodes().filter((n) => !n.data('raw').faded).every((n) => n.data('raw').domain === 'Game')"), "only Game's classes");
        assert(await page.eval("cy.nodes().some((n) => n.data('raw').faded)"), "the rest around it");
        const playerNode = await page.eval("cy.nodes().filter((n) => n.data('raw').name === 'Player').id()");
        const focusClosed = await qa("positions()");
        await page.eval(`toggleExpand(${JSON.stringify(playerNode)})`);
        await settle(700);
        const focusOpen = await qa("positions()");
        const movedInFocus = Object.keys(focusClosed).filter((id) => id !== playerNode && String(focusClosed[id]) !== String(focusOpen[id]));
        assert(!movedInFocus.length, `opening Player in the focus moved: ${movedInFocus}`);
        await page.eval(`toggleExpand(${JSON.stringify(playerNode)})`);
        await settle(700);
        assert(JSON.stringify(await qa("positions()")) === JSON.stringify(focusClosed), "closing in the focus restores the picture");
        assert((await page.eval("document.getElementById('crumbs').textContent")).includes("Game"), "breadcrumb names the domain");
        await setLevel("Home");
        assert(await page.eval("state.current.domain === 'Game' && state.current.level === 1"), "level 1 inside the focus");
        await page.key("Escape");
        await settle(700);
        assert(await page.eval("!state.domainFocus && !state.current.domain && dom.frames.length > 0"), "Esc: back to the frames");
        await page.clickOn("#showDomains");
        await settle(700);
        assert(await page.eval("!dom.frames.length && !cy.elements('.dom-hide, .dom-gone').length"), "off: no frames");
      } finally {
        if (saved) writeFileSync(notesFile, saved); else rmSync(notesFile, { force: true });
        await forgetOptions("domains", "between");
        await open();
      }
    });

    await step("Overview: story chips, domain cards, Show on map remembered, ?overview=0, Ask AI, themes, phone", async () => {
      const notesFile = join(data, "notes.jsonl");
      const saved = existsSync(notesFile) ? readFileSync(notesFile) : null;
      const shown = "!document.getElementById('overview').hidden";
      try {
        for (const [cls, domain] of [["Player", "Game"], ["res://src/main.gd", "Game"], ["Big", "World"]]) {
          execFileSync(cli, ["set", fixture, cls, `domain=${domain}`, "--data", data]);
        }
        execFileSync(cli, ["set", fixture, "domain:Game", "description=The part the player plays in.", "--data", data]);
        execFileSync(cli, ["set", fixture, "domain:World", "hint=Where everything happens", "--data", data]);
        await open("level=0&overview=1");
        assert(await page.eval(shown), "level 0 opens as the overview");
        assert(await page.eval("document.querySelectorAll('#ovCards .dcard').length === 2 && document.querySelector('.dcard[data-d=Game] .db').textContent.includes('The part the player plays in.')"), "a card per domain with its description");
        assert(await page.eval("document.querySelector('.dcard[data-d=World] .db').textContent.includes('Where everything happens')"), "hint when there is no description");
        assert(await page.eval("document.querySelector('.dcard[data-d=Game] .keys code')"), "key classes");
        assert(await page.eval("/Ask AI/.test(document.getElementById('ovStory').textContent) && document.querySelectorAll('#ovStory .dchip').length > 0"), "no sentence yet: a generated one with chips and an Ask AI button");
        await page.clickOn("#ovStory [data-ask]");
        await settle(300);
        assert(await page.eval("/Copied a request/.test(document.getElementById('copyOut').textContent) || (!document.getElementById('copyBox').hidden && document.getElementById('copyText').value.includes('domain:*'))"), "Ask AI copies a request");
        // a written sentence: chips for the domain names, the colours of the frames
        execFileSync(cli, ["set", fixture, "domain:*", "description=You play in the Game, which keeps the World going.", "--data", data]);
        await open("level=0&overview=1");
        assert(await page.eval("[...document.querySelectorAll('#ovStory .dchip')].map((c) => c.dataset.d).join() === 'Game,World' && !document.querySelector('#ovStory [data-ask]') && /AI draft/.test(document.getElementById('ovStory').textContent)"), "written sentence: two chips, AI draft mark, no Ask AI");
        assert(await page.eval("document.querySelector('#ovStory .dchip').style.getPropertyValue('--h') === String(CoderDomains.hueOf('Game'))"), "chip colour = the frame colour");
        await page.clickOn("#ovStory .dchip[data-d=World]");
        assert(await page.eval("document.querySelector('.dcard[data-d=Game]').classList.contains('dim') && !document.querySelector('.dcard[data-d=World]').classList.contains('dim')"), "chip click lights its card");
        await page.clickOn("#ovStory .dchip[data-d=World]");
        assert(await page.eval("!document.querySelector('.dcard.dim')"), "second click clears");
        for (const theme of ["light", "dark", "contrast"]) {
          await open(`level=0&overview=1&theme=${theme}`);
          assert(await page.eval(shown), `${theme}: overview`);
          if (theme === "light") await page.screenshot(join(shots, "overview-light.png"));
          if (theme === "contrast") await page.screenshot(join(shots, "overview-contrast.png"));
        }
        await page.conn.send("Emulation.setDeviceMetricsOverride", { width: 400, height: 800, deviceScaleFactor: 1, mobile: false });
        await open("level=0&overview=1&theme=dark");
        assert(await page.eval("document.getElementById('overview').scrollWidth <= document.getElementById('overview').clientWidth + 1"), `phone width: no sideways scroll ${await page.eval("JSON.stringify([state.level, document.getElementById('overview').hidden, document.getElementById('overview').scrollWidth, document.getElementById('overview').clientWidth])")}`);
        await page.screenshot(join(shots, "overview-phone.png"));
        await page.conn.send("Emulation.setDeviceMetricsOverride", { width: 1400, height: 900, deviceScaleFactor: 1, mobile: false });
        // Show on map: remembered; the Overview button comes back; ?overview=0 always the map
        await open("level=0&overview=1");
        await page.clickOn("#ovMap");
        await settle(300);
        assert(await page.eval("document.getElementById('overview').hidden && !document.getElementById('overviewBtn').hidden && cy.nodes().length > 0"), "map shown with an Overview button");
        await open("level=0&overview=saved");  // not a value: the remembered choice
        assert(await page.eval("document.getElementById('overview').hidden"), "the map choice is remembered");
        await page.clickOn("#overviewBtn");
        assert(await page.eval(shown + " && document.getElementById('overviewBtn').hidden"), "Overview button brings it back");
        await open("level=0");
        assert(await page.eval("document.getElementById('overview').hidden && cy.nodes().length > 0"), "?overview=0 (added by open) forces the map");
        await open("level=1&overview=1");
        assert(await page.eval("document.getElementById('overview').hidden && document.getElementById('overviewBtn').hidden"), "only level 0 has it");
        // a renamed or removed domain leaves its notes harmless
        execFileSync(cli, ["set", fixture, "Big", "domain=", "--data", data]);
        await open("level=0&overview=1");
        assert(await page.eval(shown + " && document.querySelectorAll('#ovCards .dcard').length === 1"), "domain gone: its notes stay, no crash");
      } finally {
        if (saved) writeFileSync(notesFile, saved); else rmSync(notesFile, { force: true });
        await forgetOptions("overview");
        await open();
      }
    });

    await step("box faces: Box text switch, what it does (2 lines), Ask AI, taller boxes never overlap", async () => {
      const notesFile = join(data, "notes.jsonl");
      const saved = existsSync(notesFile) ? readFileSync(notesFile) : null;
      const long = "VeryLongInventoryItemSlotControllerForTheWholeGameUi";
      try {
        execFileSync(cli, ["set", fixture, long, `hint=${"Keeps every slot of the inventory in step with the items the player holds, ".repeat(3)}`, "--data", data]);
        execFileSync(cli, ["set", fixture, "Player", "domain=Game", "--data", data]);
        await open("level=9&theme=light&boxtext=default");  // not a mode: the saved choice, else the default
        assert(await page.eval("document.querySelector('#boxTextCtl [data-boxtext=short]').getAttribute('aria-pressed') === 'true'"), "Name + what it does is the default");
        const face = (id) => page.eval(`(() => { const n = cy.nodes().filter((x) => x.data('raw').name === ${JSON.stringify(id)})[0];
          return n && { desc: n.data('descLines'), foot: n.data('footLines'), h: n.data('h'), w: n.data('w') }; })()`);
        const player = await face("Player");
        assert(player.desc.join(" ") === "AI wrote this", `Player says what it does: ${JSON.stringify(player.desc)}`);
        const longFace = await face(long);
        assert(longFace.desc.length === 2 && longFace.desc[1].endsWith("…"), `long text clamped to 2 lines: ${JSON.stringify(longFace.desc)}`);
        assert(await page.eval("document.querySelectorAll('#fanFront .ask-btn').length > 0 && /No description yet/.test(document.getElementById('fanFront').textContent)"), "no description: Ask AI");
        assert(!(await page.eval("document.getElementById('fanFront').textContent.includes('Claude')")), "UI says AI");
        const past = await page.eval(`[...document.querySelectorAll('#fanFront .face')].filter((g) => {
          const frame = g.querySelector('.box-bg')?.getBoundingClientRect();
          return frame && [...g.querySelectorAll('.cls-name, .cls-desc, .cls-sub, .ask-btn, .b-used')].some((t) => { const r = t.getBoundingClientRect(); return r.right > frame.right + 0.5 || r.bottom > frame.bottom + 0.5; });
        }).length`);
        assert(past === 0, `${past} boxes have text past their frame`);
        await page.grantClipboard(base.slice(0, -1));
        await page.eval("document.querySelector('#fanFront .ask-btn').dispatchEvent(new MouseEvent('click', { bubbles: true }))");
        await page.waitFor("/Copied a request for the AI/.test(document.getElementById('copyOut').textContent) || !document.getElementById('copyBox').hidden", { what: "Ask AI note" });
        const request = await page.eval("document.getElementById('copyBox').hidden ? navigator.clipboard.readText() : document.getElementById('copyText').value");
        assert(/coderstanding set .* hint=/.test(request), `Ask AI copies a request, never calls an AI: ${request}`);
        await page.eval("document.getElementById('copyBox').hidden = true");
        await checkLayout("short faces");
        await page.clickOn("#boxTextCtl [data-boxtext=full]");
        await settle();
        assert((await face(long)).desc.length > 2 && (await face("Player")).foot.join(" ").includes("Game"), "Full: all of it, domain in the footer");
        await checkLayout("full faces");
        await page.clickOn("#boxTextCtl [data-boxtext=name]");
        await settle();
        const named = await face("Player");
        assert(!named.desc && named.h < player.h, "Name: today's face, shorter");
        await open("level=9&boxtext=saved");
        assert(await page.eval("state.boxText === 'name'"), "Box text remembered per app");
        await open("level=9&boxtext=short");
        assert(await page.eval("state.boxText === 'short' && cy.nodes().some((n) => n.data('descLines'))"), "boxtext= URL option");
        await page.clickOn("#boxTextCtl [data-boxtext=short]");
      } finally {
        if (saved) writeFileSync(notesFile, saved); else rmSync(notesFile, { force: true });
      }
    });

    await step("box faces: explained lines first, faint until an end is hovered or selected; All equal; old helper", async () => {
      const notesFile = join(data, "notes.jsonl");
      const saved = existsSync(notesFile) ? readFileSync(notesFile) : null;
      try {
        execFileSync(cli, ["set", fixture, "Player>Big", "hint=The player steps the big class", "--data", data]);
        await open("level=9&theme=light");
        const counts = await page.eval("document.getElementById('linesCount').textContent");
        assert(/^\d+ explained · \d+ faint$/.test(counts), `count text: ${counts}`);
        const quiet = await page.eval("cy.edges('.quiet').map((e) => e.id())");
        assert(quiet.length > 0 && await page.eval("cy.edges().filter((e) => !e.hasClass('quiet')).some((e) => e.data('raw').hint)"), "hinted lines full, the rest quiet");
        const end = await page.eval(`cy.getElementById(${JSON.stringify(quiet[0])}).source().id()`);
        await page.eval(`void cy.getElementById(${JSON.stringify(end)}).emit('mouseover')`);
        assert(await page.eval(`cy.getElementById(${JSON.stringify(quiet[0])}).hasClass('near')`), "hovering an end brings the line back");
        await page.eval(`void cy.getElementById(${JSON.stringify(end)}).emit('mouseout')`);
        assert(!(await page.eval(`cy.getElementById(${JSON.stringify(quiet[0])}).hasClass('near')`)), "and faint again after");
        await page.eval(`void select({ type: "node", raw: cy.getElementById(${JSON.stringify(end)}).data("raw") })`);
        assert(await page.eval(`cy.getElementById(${JSON.stringify(quiet[0])}).hasClass('near')`), "selecting an end keeps it full");
        await page.eval("void select(null)");
        await page.clickOn("#linesCtl [data-lines=all]");
        await settle();
        assert(await page.eval("cy.edges('.quiet').length === 0 && /all at the same weight/.test(document.getElementById('linesCount').textContent)"), "All equal: no faint lines");
        await open("level=9&lines=explained");
        assert(await page.eval("cy.edges('.quiet').length > 0"), "lines= URL option");
        // an older helper sends no `quiet` / `lines`: everything still draws, all equal
        await page.eval("draw({ ...state.current, lines: undefined, domains: undefined, edges: state.current.edges.map(({ quiet, ...e }) => e), nodes: state.current.nodes.map(({ used_by, ...n }) => n) })");
        await settle();
        assert(await page.eval("cy.edges('.quiet').length === 0 && cy.nodes().length > 0 && document.querySelectorAll('#fanFront .face').length > 0"), "old helper's view renders");
        await page.clickOn("#linesCtl [data-lines=explained]");
        await settle();
      } finally {
        if (saved) writeFileSync(notesFile, saved); else rmSync(notesFile, { force: true });
      }
    });

    await step("Plain panel: click fades the unconnected, In plain words on top, Details fold, hover keeps it, Esc, lines, reduced motion", async () => {
      await open("level=9&details=closed&boxtext=short");
      await page.eval("cy.fit(undefined, 30), true");
      await settle(200);
      const dimmed = () => page.eval("cy.nodes('.foc-dim').map((n) => n.data('raw').name).sort().join()");
      const player = await qa('node("Player")');
      await page.click(player.x + 20, player.y + 4);
      await page.waitFor("[...document.querySelectorAll('#fanFront .face.foc-fade')].some((g) => getComputedStyle(g).animationName === 'foc-fade')", { what: "faces fade out" });
      await settle(250);
      assert((await qa("showing()")) === "node:Player", `Player selected: ${await qa("showing()")}`);
      const near = await page.eval("cy.getElementById('Player').closedNeighborhood('node').map((n) => n.id()).sort().join()");
      const faded = await dimmed();
      assert(faded && !near.split(",").some((id) => faded.split(",").includes(id)), `unconnected fade, neighbours stay: ${faded} / ${near}`);
      assert(await page.eval("cy.edges('.foc-dim').every((e) => e.source().id() !== 'Player' && e.target().id() !== 'Player')"), "Player's lines stay lit");
      assert(await page.eval("document.querySelectorAll('#fanFront .face.dim').length") > 0, "faded faces too");
      const panel = await page.eval(`(() => { const d = document.getElementById('details'); const p = d.querySelector('.plain');
        return { first: d.firstElementChild === p, title: p?.querySelector('h3')?.textContent, text: p?.textContent || '',
          fold: d.querySelector('details.details-fold')?.open, zoom: Boolean(d.querySelector('details.details-fold [data-act="zoom-node"]')) }; })()`);
      assert(panel.first && panel.title === "Player", `In plain words on top: ${JSON.stringify(panel)}`);
      assert(/Used by \(\d+\)/.test(panel.text) && /Uses \(\d+\)/.test(panel.text) && /level \d+/.test(panel.text), `lists and where: ${panel.text}`);
      assert(panel.fold === false && panel.zoom, "today's panel under a closed Details fold");
      assert(!/Claude/.test(panel.text), "UI says AI");
      // hover another class: the panel stays on Player
      const big = await qa('node("Big")');
      const other = await page.eval("cy.nodes('.foc-dim').filter((n) => !n.data('raw').faded)[0]?.data('raw').name || ''");
      const hover = other ? await qa(`node(${JSON.stringify(other)})`) : big;
      await page.move(hover.x + 20, hover.y + 4);
      await sleep(150);
      assert(await page.eval("cy.nodes('.hover').length === 1 && cy.$('.hover').id() !== 'Player'"), "the pointer is over another class");
      assert((await qa("showing()")) === "node:Player", "hover does not replace the selected panel");
      // an un-hinted line: Ask AI copies a request for that line's hint
      const ask = await page.eval("Boolean(document.querySelector('#details .plain [data-act=\"ask-line\"]'))");
      assert(ask, "un-hinted lines have Ask AI");
      await page.eval("navigator.clipboard.writeText = async (text) => { window.__copied = text; }, true");  // what Ask AI copies
      await page.clickOn('#details .plain [data-act="ask-line"]');
      await page.waitFor("/Copied a request for the AI/.test(document.getElementById('copyOut').textContent)", { what: "Ask AI note" });
      const request = await page.eval("window.__copied");
      assert(/the line .* → .*coderstanding set \S+ \S+>\S+ hint=/.test(request), `line hint request: ${request}`);
      await page.eval("document.getElementById('copyBox').hidden = true");
      // the Details fold is remembered
      await page.eval("document.querySelector('#details details.details-fold > summary').click()");
      await sleep(100);
      assert(await page.eval("state.detailsOpen && JSON.parse(localStorage.getItem('coderstanding-view')).changed.details === 'open'"), "fold state kept");
      await page.eval("document.querySelector('#details details.details-fold > summary').click()");
      await page.screenshot(join(shots, "plain-panel-dark.png"));
      // Esc clears the selection and the fade
      await page.move((await qa("empty()")).x, (await qa("empty()")).y);
      await page.key("Escape");
      await settle(250);
      assert(!(await qa("showing()")) && !(await dimmed()), "Esc clears");
      // a line: "A uses B" in plain words, both ends lit
      const edge = await qa('edge("Player", "Big")');
      assert(edge, "line Player → Big drawn");
      await page.click(edge.x, edge.y);
      await settle(250);
      const sentence = await page.eval("document.querySelector('#details .plain .line-sentence')?.textContent || ''");
      assert(/^Player .+ Big/.test(sentence), `line sentence: ${sentence}`);
      const lineDim = await dimmed();
      assert(!lineDim.split(",").includes("Player") && !lineDim.split(",").includes("Big") && lineDim, `both ends lit, the rest fades: ${lineDim}`);
      // click on empty canvas clears
      const away = await qa("empty()");
      await page.click(away.x, away.y);
      await settle(250);
      assert(!(await qa("showing()")) && !(await dimmed()), "empty canvas click clears");
      // nothing selected: hover previews as before
      const again = await qa('node("Player")');
      await page.move(again.x + 20, again.y + 4);
      await sleep(150);
      assert((await qa("showing()")) === "node:Player", `hover previews with nothing selected: ${await qa("showing()")}`);
      // themes and phone width
      for (const theme of ["light", "contrast", "extreme"]) {
        await open(`level=9&details=closed&boxtext=short&theme=${theme}&select=Player`);
        await page.screenshot(join(shots, `plain-panel-${theme}.png`));
      }
      // extreme theme: no fades; what the focus leaves out is dashed at full opacity
      const extreme = await page.eval(`(() => { const face = document.querySelector('#fanFront .face.dim'); const node = cy.nodes('.foc-dim')[0]; const edge = cy.edges('.foc-dim')[0];
        return { face: face && getComputedStyle(face).opacity, dash: face && getComputedStyle(face.querySelector('.box-bg')).strokeDasharray,
          node: node && [node.style('opacity'), node.style('border-style')], edge: edge ? [edge.style('opacity'), edge.style('line-style')] : null }; })()`);
      assert(extreme.face === "1" && extreme.dash && extreme.dash !== "none", `extreme: dimmed faces dashed at opacity 1: ${JSON.stringify(extreme)}`);
      assert(extreme.node && Number(extreme.node[0]) === 1 && extreme.node[1] === "dashed", `extreme: dimmed boxes dashed: ${JSON.stringify(extreme)}`);
      assert(!extreme.edge || (Number(extreme.edge[0]) === 1 && extreme.edge[1] === "dashed"), `extreme: dimmed lines dashed: ${JSON.stringify(extreme)}`);
      await page.send("Emulation.setDeviceMetricsOverride", { width: 400, height: 860, deviceScaleFactor: 1, mobile: true });
      try {
        await open("level=9&details=closed&boxtext=short&theme=light&select=Player");
        const wide = await page.eval("document.querySelector('#details .plain').getBoundingClientRect().width");
        assert(wide >= 250, `phone: the plain block keeps a readable width (${wide})`);
        assert(await page.eval("document.documentElement.scrollWidth <= window.innerWidth + 1"), "phone: no sideways scroll");
        await page.eval("document.getElementById('details').scrollIntoView(), true");
        await page.screenshot(join(shots, "plain-panel-phone.png"));
      } finally {
        await page.send("Emulation.setDeviceMetricsOverride", { width: 1780, height: 1000, deviceScaleFactor: 1, mobile: false });
      }
      // reduced motion: no fade animation
      await page.send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] });
      try {
        await open("level=9&details=closed&boxtext=short");
        await page.eval("cy.fit(undefined, 30), true");
        await settle(200);
        const target = await qa('node("Player")');
        await page.click(target.x + 20, target.y + 4);
        await sleep(30);
        const faces = await page.eval("[...document.querySelectorAll('#fanFront .face.dim')].map((g) => getComputedStyle(g).animationName)");
        assert(faces.length && faces.every((n) => n === "none"), `reduced motion: no fade animation (${faces})`);
      } finally {
        await page.send("Emulation.setEmulatedMedia", { features: [] });
      }
    });

    await step("Planned work: Add box asks first on a real name, planned face and details, Only / Hide, stub row, Delete", async () => {
      await page.clickOn("#modeEdit");
      await sleep(200);
      await page.clickOn("#addBoxBtn");
      await sleep(150);
      await page.eval(`document.getElementById("planName").value = "Big"`);
      await page.clickOn('#planForm button[type="submit"]');
      await settle();
      assert(await page.eval(`!document.getElementById("planAsk").hidden && /real code/.test(document.getElementById("planAsk").textContent)`), "asks first on a real name");
      assert(!notes().some((n) => n.key === "node:Big" && n.made), "nothing written while asking");
      await page.eval(`(() => { document.getElementById("planName").value = "node_store"; document.getElementById("planHint").value = "Keeps hand-made boxes"; document.getElementById("planStage").value = "todo"; })()`);
      await page.clickOn('#planForm button[type="submit"]');
      await settle(600);
      const made = lastNote("node:node_store", "made");
      assert(made?.made === "planned" && made.stage === "todo" && made.by === "Sam Tester" && !made.ai, `planned box saved: ${JSON.stringify(made)}`);
      assert(await page.eval(`document.querySelectorAll(".face.made-planned").length === 1`), "planned face drawn (dashed)");
      assert(await page.eval(`/1 planned/.test(document.getElementById("planCount").textContent) && !document.getElementById("planCount").hidden`), "toolbar count");
      await page.eval(`select({ type: "node", raw: cy.getElementById("node_store").data("raw") })`);
      await sleep(150);
      assert(await page.eval(`/Not built yet/.test(document.getElementById("details").textContent)`), "details banner");
      await page.eval(`(() => { const s = document.querySelector('#details select[data-note="priority"]'); s.value = "high"; s.dispatchEvent(new Event("change", { bubbles: true })); })()`);
      await settle();
      assert(lastNote("node:node_store", "priority")?.priority === "high", "work priority saved");
      await page.clickOn("#planCount");
      await page.waitFor(`document.getElementById("app").classList.contains("plan-only") && cy.nodes(".plan-dim").length > 0`, { what: "Only planned work fades the rest" });
      await page.eval(`setOption("planned", "hide")`);
      await settle();
      assert(await page.eval(`cy.getElementById("node_store").empty()`), "Hide leaves it off the map");
      await page.eval(`setOption("planned", "show")`);
      await settle();
      await page.eval(`select({ type: "node", raw: cy.getElementById("node_store").data("raw") })`);
      await sleep(150);
      await page.clickOn('#details [data-act="plan-drop"]');
      await page.clickOn('#details [data-act="plan-drop-yes"]');
      await settle();
      assert(await page.eval(`cy.getElementById("node_store").empty()`) && !notes().some((n) => n.key === "node:node_store" && n.made), "Delete removes it");
      await page.eval(`toggleExpand("res://src/bus.gd")`);
      await settle();
      assert(await page.eval(`[...document.querySelectorAll(".fn.stub .fn-mark")].some((m) => m.textContent.trim() === "stub")`), "a stub function row is marked");
      await page.eval(`toggleExpand("res://src/bus.gd")`);
      await settle();
      await page.clickOn("#modeView");
      await sleep(200);
    });

    await step("Progress tracks: picker, ring pills, project number, click selects, details, overview bars, spec chips, themes", async () => {
      const notesFile = join(data, "notes.jsonl"), settingsFile = join(data, "settings.json");
      const saved = existsSync(notesFile) ? readFileSync(notesFile) : null;
      const savedSettings = existsSync(settingsFile) ? readFileSync(settingsFile) : null;
      try {
        const settings = savedSettings ? JSON.parse(savedSettings) : {};
        writeFileSync(settingsFile, JSON.stringify({ ...settings, tracks: [{ name: "Checked", done: { tag: "checked" } }] }));
        execFileSync(cli, ["set", fixture, "Player", "tags=checked", "domain=Game", "--data", data]);
        await open("track=Checked");
        assert(await page.eval(`[...document.getElementById("trackCtl").options].map((o) => o.value).join() === ",Described,Tested,Built,Checked"`), "picker: none, the built-in tracks, the project's own");
        assert(await page.eval(`document.getElementById("trackCtl").value === "Checked"`), "?track= picks it");
        const pills = await page.eval(`[...document.querySelectorAll(".prog-pill")].map((p) => p.getAttribute("aria-label"))`);
        assert(pills.length === await page.eval(`cy.nodes().filter((n) => n.data("raw").progress).length`) && pills.length > 1, `a pill on every box that counts something: ${pills.length}`);
        assert(pills.some((t) => /^Checked: 1 of \d+ done \(\d+%\)$/.test(t)) && pills.some((t) => /^Checked: 0 of \d+ done \(0%\)$/.test(t)), `pill words: ${pills.join(" | ")}`);
        assert(await page.eval(`/^\\d+% \\(1\\/\\d+\\)/.test(document.getElementById("trackSum").textContent) && !document.getElementById("trackSum").hidden`), "the project's number beside the picker");
        const lit = await page.eval(`(() => { const p = document.querySelector(".prog-pill:has(.prog-done)"); p.dispatchEvent(new MouseEvent("click", { bubbles: true })); return p.dataset.id; })()`);
        await sleep(150);
        assert(await page.eval(`state.selected?.raw?.id === ${JSON.stringify(lit)} && Boolean(document.querySelector("#details .prog-bar"))`), "a pill click selects its box; the details show its bar");
        for (const theme of ["light", "contrast", "extreme"]) {
          await open(`track=Checked&theme=${theme}`);
          const looks = await page.eval(`(() => { const p = document.querySelector(".prog-pill:has(.prog-done)"); const s = (sel) => getComputedStyle(p.querySelector(sel));
            return { done: s(".prog-done").stroke, track: s(".prog-track").stroke, text: s("text").fill, back: s("rect").fill }; })()`);
          assert(looks.done !== looks.track && looks.text !== looks.back, `${theme}: ring and text stand out ${JSON.stringify(looks)}`);
        }
        await open("level=0&overview=1&track=Checked");
        assert(await page.eval(`/Checked 100% · 1\\/1/.test(document.querySelector('.dcard[data-d=Game] .prog-bar')?.textContent || "")`), "overview: a bar on the domain card");
        assert(await page.eval(`getComputedStyle(document.querySelector('.dcard[data-d=Game] .prog-meter i')).width !== "0px"`), "the bar is filled (CSSOM width)");
        await open("specs=1&track=Checked");
        await page.waitFor(`document.querySelector('[data-spec="player"] .chip.prog')`, { what: "spec chip" });
        const chip = await page.eval(`document.querySelector('[data-spec="player"] .chip.prog').textContent`);
        assert(/^50% Checked$/.test(chip), `a spec reads its own and its finer specs' linked classes (Player done, Big not): ${chip}`);
        await page.eval(`setOption("track", "")`);
        await settle();
        assert(await page.eval(`!document.querySelector(".prog-pill") && document.getElementById("trackSum").hidden && !document.querySelector(".chip.prog")`), "none: no pills, no number, no chips");
      } finally {
        if (saved) writeFileSync(notesFile, saved); else rmSync(notesFile, { force: true });
        if (savedSettings) writeFileSync(settingsFile, savedSettings); else rmSync(settingsFile, { force: true });
        await forgetOptions("track");
        await open();
      }
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

    await step("Contrast theme", async () => {
      await open("level=9&theme=contrast");
      assert(await page.eval("document.documentElement.getAttribute('data-theme')") === "contrast", "root attribute");
      assert(await page.eval("getComputedStyle(document.documentElement).colorScheme") === "dark", "color-scheme dark");
      assert(await page.eval("getComputedStyle(document.body).backgroundColor") === "rgb(0, 0, 0)", "black page");
      assert(await page.eval("document.getElementById('themeBtn').textContent") === "Theme: contrast", "button label");
      await page.eval("document.getElementById('themeBtn').click()");  // contrast -> extreme
      assert(await page.eval("document.documentElement.getAttribute('data-theme')") === "extreme", "button cycles to extreme");
      await page.eval("applyTheme('contrast')");
      assert(await page.eval("localStorage.getItem('coderstanding-theme')") === "contrast", "remembered");
      await settle();
      await page.screenshot(join(shots, "contrast-graph.png"));
      await openTour("tab=tours&tour=first-steps&step=2&theme=contrast");
      assert(await page.eval("document.documentElement.getAttribute('data-theme')") === "contrast", "tours keep the theme");
      await page.screenshot(join(shots, "contrast-tour.png"));
    });

    await step("Extreme theme", async () => {
      await open("level=1&theme=extreme");
      assert(await page.eval("document.documentElement.getAttribute('data-theme')") === "extreme", "root attribute");
      assert(await page.eval("getComputedStyle(document.body).backgroundColor") === "rgb(0, 0, 0)", "black page");
      assert(await page.eval("document.getElementById('themeBtn').textContent") === "Theme: extreme", "button label");
      assert(await page.eval("getComputedStyle(document.documentElement).getPropertyValue('--op-dim').trim()") === "1", "no fade token");
      await page.eval("document.getElementById('themeBtn').click()");  // extreme -> auto
      assert(await page.eval("document.documentElement.hasAttribute('data-theme')") === false, "button cycles back to auto");
      await page.eval("applyTheme('extreme')");
      assert(await page.eval("localStorage.getItem('coderstanding-theme')") === "extreme", "remembered");
      await page.eval("(() => { const f = document.getElementById('filter'); f.selectedIndex = 1; f.dispatchEvent(new Event('change', { bubbles: true })); })()");
      await settle();
      assert(await page.eval("cy.nodes('.dim').length") > 0, "a class is picked, the rest are dimmed");
      assert(await page.eval("cy.nodes('.dim')[0].style('opacity')") === "1", "dimmed boxes keep full opacity");
      assert(await page.eval("cy.nodes('.dim')[0].style('border-style')") === "dashed", "dimmed boxes are dashed");
      await page.screenshot(join(shots, "extreme-graph.png"));
      await openTour("tab=tours&tour=first-steps&step=1&theme=extreme");
      assert(await page.eval("document.documentElement.getAttribute('data-theme')") === "extreme", "tours keep the theme");
      await page.screenshot(join(shots, "extreme-tour.png"));
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

    await step(`box layers: selected on top, open under the rest (fixture)`, async () => {
      await open("level=3");
      const ids = await page.eval("cy.nodes().filter((n) => isExpandable(n.data('raw'))).map((n) => n.id()).slice(0, 3)");
      if (ids.length < 3) return;
      await page.eval(`toggleExpand(${JSON.stringify(ids[0])})`);
      await settle();
      // faces are SVG groups; later in the DOM = drawn on top
      const order = () => page.eval(`stackedNodes().map((n) => n.id())`);
      let ranked = await order();
      assert(ranked[0] === ids[0], `open unselected box should be drawn first (under), got ${ranked.slice(0, 2)}`);
      await page.eval(`select({ type: "node", raw: cy.getElementById(${JSON.stringify(ids[0])}).data("raw") })`);
      await settle();
      ranked = await order();
      assert(ranked.at(-1) === ids[0], "selected box should be drawn last (on top)");
      const faces = await page.eval(`[...document.querySelectorAll("#fanFront g.face")].filter((g) => g.querySelector(".box-bg")).length`);
      assert(faces > 0, "each face carries its own opaque background");
      await page.eval("select(null)");
      await settle();
      ranked = await order();
      assert(ranked[0] === ids[0], "deselected open box goes back under the others");
    });
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
      execFileSync(cli, ["scan", appRepo, "-o", join(appData, "graph.json")], { env: { ...process.env, ...isolated } });
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
      const second = registerSecondApp();
      await rememberPreset("fixture", "explore");
      await open();
      const reloaded = async (app) => {
        await sleep(600);
        await page.waitFor(`window.CoderApps && CoderApps.current === ${JSON.stringify(app)} && window.cy && document.getElementById('overlay').hidden && cy.nodes().length > 0`, { what: `app ${app} loaded` });
        await page.eval(HELPERS);
        await settle();
      };
      const names = () => page.eval("cy.nodes().map((n) => n.data('raw').name).sort().join(',')");
      const optionTexts = await page.eval("[...document.querySelectorAll('#appPick option')].map((o) => o.textContent).join(' | ')");
      assert(/fixture · .*(ago|just now).* · \d+ classes/.test(optionTexts) && /second/.test(optionTexts), `picker lists both apps: ${optionTexts}`);
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
      writeAppFolder(third, { delta: withMain("Delta") });
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
      registerSecondApp();
      registerThirdApp();
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
      await press("third", "Redo levels…");
      assert(await page.eval(`/AI-drafted/.test(document.querySelector('${row("third")} [data-act="relevel-ai"]').textContent) && /yours too/.test(document.querySelector('${row("third")} [data-act="relevel-all"]').textContent) && !document.querySelector('${row("third")} .relevel-names').checked`), "redo levels: AI-only and all choices, names kept by default");
      await press("third", "Cancel");
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
        await page.eval("window.beats = []; const real = window.fetch; window.fetch = (u, o) => real(u, o).then((r) => { if (String(u).endsWith('/api/alive')) window.beats.push(r.status); return r; }); 1");
        await page.waitFor("window.beats.includes(200)", { what: "heartbeat answered by the helper" });
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
        assert(/coderstanding helper install/.test(text) && /install-helper\.sh/.test(text), `how-to: ${text}`);
        assert(await page.eval("document.querySelector('#overlay a[data-act=start-helper]')?.getAttribute('href') === 'coderstanding://start'"), "Start helper link");
      } finally {
        dead.close();
        base = home;
      }
      await open();
    });

    await step("Start here: button lists start tours, plays one in the Tours tab, numbered badges on the boxes", async () => {
      const startTour = join(data, "tours", "start-game.md");
      const body = ["res://src/main.gd", "Player", "Big"].map((t) => `## ${t.split("/").pop()}\n[[${t}]] is on the path.\n`).join("\n");
      writeFileSync(startTour, `---\nid: start-game\ntitle: "Start here: the game"\nstart: true\nby: claude\nai: true\nsteps:\n${["res://src/main.gd", "Player", "Big"].map((t, i) => `  - key: p${i}\n    targets: [${t}]\n`).join("")}---\n${body}`);
      try {
        await open("");
        const btn = "document.getElementById('startHereBtn')";
        assert(await page.eval(`!!${btn} && ${btn}.textContent.trim() === 'Start here'`), "Start here button on the graph tab");
        await page.eval(`${btn}.click()`);
        await page.waitFor("!document.getElementById('tabTours').hidden && /Step 1\\/3/.test(document.querySelector('#tourPanel .stepno')?.textContent || '')", { what: "start tour opened at step 1" });
        await settle();
        assert(await page.eval("document.querySelector('#tourList .pick[aria-current] .chip')?.textContent") === "Start", "list marks the start tour");
        const badges = () => page.eval("[...document.querySelectorAll('#startBadges .stepbadge')].map((b) => b.textContent + (b.classList.contains('now') ? '*' : ''))");
        await page.waitFor("document.querySelectorAll('#startBadges .stepbadge').length > 0", { what: "step badges" });
        assert((await badges()).includes("1*"), `badge 1 is the current step: ${await badges()}`);
        await page.screenshot(join(shots, "start-here-tour.png"));
        await page.eval("document.getElementById('tourNext').click()");
        await settle();
        await page.waitFor("document.querySelector('#startBadges .stepbadge.now')?.textContent === '2'", { what: "badge 2 current" });
        await open("tab=tours");  // leaving the tab clears the badges
        await page.eval("document.getElementById('tabGraphBtn').click()");
        await settle();
        assert(await page.eval("document.querySelectorAll('#startBadges .stepbadge').length") === 0, "no badges on the graph tab");
        // Two start tours: the button offers a picker; none: a request for the AI.
        writeFileSync(join(data, "tours", "start-two.md"), readFileSync(startTour, "utf8").replace("id: start-game", "id: start-two").replace("the game", "the second way"));
        await page.eval("document.getElementById('startHereBtn').click()");
        await page.waitFor("!document.getElementById('startMenu').hidden && document.querySelectorAll('#startMenu .start-pick').length === 2", { what: "route picker" });
        await page.screenshot(join(shots, "start-here-picker.png"));
        await page.eval("document.getElementById('startHereBtn').click()");
      } finally {
        rmSync(startTour, { force: true });
        rmSync(join(data, "tours", "start-two.md"), { force: true });
      }
      await page.eval("document.getElementById('startHereBtn').click()");
      await page.waitFor("!document.getElementById('startMenu').hidden && document.querySelector('#startMenu [data-ask]')", { what: "no start tours: copy a request for AI" });
      await page.eval("document.getElementById('startHereBtn').click()");
    });

    await step("offline: strict CSP on the page, every request to this PC or data:/blob:", async () => {
      const response = await fetch(base);
      const csp = response.headers.get("content-security-policy") || "";
      assert(/default-src 'self'/.test(csp) && /connect-src 'self'/.test(csp) && /script-src 'self'/.test(csp), `CSP: ${csp}`);
      assert(response.headers.get("referrer-policy") === "no-referrer", "Referrer-Policy");
      const local = /^(https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?\/|data:|blob:)/;
      const outside = requests.filter((url) => !local.test(url));
      assert(requests.length > 50, `recorded ${requests.length} requests`);
      assert(!outside.length, `requests off this PC: ${outside.slice(0, 5).join(" ")}`);
      console.log(`     ${requests.length} requests, all local`);
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
