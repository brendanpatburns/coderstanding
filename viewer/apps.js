// Project picker (the UI says "project"; code, API and #hash keep "app"): which registered project the viewer shows, Refresh, and "Add project…".
// One server serves every app; each API call carries ?app=<name>. Switching saves the camera,
// level and zoom of the app you leave (sessionStorage, used once) and reloads cleanly on #<name>.
// The hosted page is only a shell: its hosted.json names the local helper
// (`coderstanding serve` on this PC) and every API call goes there; without a helper it shows how
// to start one. apiBase() is the one place that picks where a request goes.
(function () {
const LAST_KEY = "coderstanding-last-app";
const STATE_KEY = "coderstanding-app-state:";
const POLL_MS = 700;
const PROBE_MS = 1500;      // the local helper answers fast or not at all
const HOSTED_LABEL = "Running on this PC";
const NO_HELPER_HTML = "<strong>Open this page on your PC with Coderstanding running.</strong><br>"
  + "Your maps stay on your PC; this page talks to it. Start the helper with "
  + "<code>systemctl --user start coderstanding-helper</code> (or the tray icon), then reload. "
  + "If the browser asks to reach local devices, allow it.<br>"
  + "Not installed yet? Run <code>scripts/install-helper.sh</code> once in the Coderstanding repo.";
const NO_APPS_HTML = "No projects registered. Use “Projects… → Add project…” above, or "
  + "<code>coderstanding add &lt;name&gt; &lt;folder&gt;</code>.";

const store = (kind) => { try { return window[kind]; } catch { return null; } };
function read(kind, key) { try { return store(kind).getItem(key); } catch { return null; } }
function write(kind, key, value) {
  try { value === null ? store(kind).removeItem(key) : store(kind).setItem(key, value); } catch { /* private window */ }
}

/** "12 min ago" for an ISO time; "never scanned" without one. */
function ago(iso, now = Date.now()) {
  const time = Date.parse(iso || "");
  if (Number.isNaN(time)) return "never scanned";
  const minutes = Math.max(0, Math.round((now - time) / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  if (minutes < 60 * 24) return `${Math.round(minutes / 60)} h ago`;
  return `${Math.round(minutes / 1440)} d ago`;
}

function optionText(app, now) {
  const size = app.nodes == null ? "" : ` · ${app.nodes} classes`;
  return `${app.name} · ${app.scanning ? "refreshing…" : ago(app.scannedAt, now)}${size}`;
}

/** Which app to open: #hash, else the app serve was given, else the last one used, else the first. */
function chooseApp(names, { hash, initial, last }) {
  return [hash, initial, last].find((name) => name && names.includes(name)) || names[0] || null;
}

/** The app `step` places along from `current`, wrapping. */
function stepApp(names, current, step) {
  if (names.length < 2) return current;
  return names[(names.indexOf(current) + step + names.length) % names.length];
}

/** Where API calls go: "" (the server that sent this page) unless this is the hosted page,
 * then the local helper's URL (null: none answered, so nothing loads). */
function apiBase(hosted, helper) {
  return hosted ? helper : "";
}

const CoderApps = { ago, optionText, chooseApp, stepApp, apiBase, HOSTED_LABEL, bytesText, factsText,
  current: null, list: [],
  hosted: false,    // this page came from the hosted shell (hosted.json), not from a local serve
  helper: null,     // the local helper's URL once it answered (hosted page only)
  /** app.js sets this: returns the state to keep when leaving an app (level, crumbs, camera). */
  getState: () => null };

/** The full URL of an /api/ path: apiBase + path, with ?app=<current> unless it names one
 * already (server-made URLs like tour audio) or is /api/apps. */
CoderApps.url = (path) => {
  const base = apiBase(CoderApps.hosted, CoderApps.helper);
  if (!CoderApps.current || !path.startsWith("/api/") || path.startsWith("/api/apps")
      || /[?&]app=/.test(path)) return base + path;
  return base + path + (path.includes("?") ? "&" : "?") + "app=" + encodeURIComponent(CoderApps.current);
};

const sleep = (ms) => new Promise((ok) => setTimeout(ok, ms));

/** fetch an /api/ path as JSON (throws the server's error). */
CoderApps.request = async (path, options = {}) => {
  const response = await fetch(CoderApps.url(path), options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
};

/** The local helper's URL from hosted.json: deploy/hosted.json names it on the hosted shell;
 * viewer/hosted.json (a local serve) has none, so the API is on this same server. */
async function hostedHelper() {
  try {
    return (await (await fetch("hosted.json", { cache: "no-store" })).json()).helper || null;
  } catch { return null; }
}

/** True when the local helper at `url` answers (its app listing). */
async function probeHelper(url) {
  try {
    return (await fetch(`${url}/api/apps`, { signal: AbortSignal.timeout(PROBE_MS) })).ok;
  } catch { return false; }
}

/** The state saved when this app was left; handed out once. */
CoderApps.takeState = () => {
  const key = STATE_KEY + CoderApps.current;
  const text = read("sessionStorage", key);
  write("sessionStorage", key, null);
  try { return JSON.parse(text); } catch { return null; }
};

function leave() {
  write("sessionStorage", STATE_KEY + CoderApps.current, JSON.stringify(CoderApps.getState()));
}

function open(name, keepState = true) {
  if (keepState) leave();
  write("localStorage", LAST_KEY, name);
  history.replaceState(null, "", location.pathname + "#" + encodeURIComponent(name));
  location.reload();
}

const $ = (id) => document.getElementById(id);
const message = (text, isError = false) => {
  $("appMsg").textContent = text;
  $("appMsg").classList.toggle("error", isError);
};
const fetchApps = () => CoderApps.request("/api/apps");

function fillPicker() {
  $("appPick").replaceChildren(...CoderApps.list.map((app) => {
    const option = document.createElement("option");
    option.value = app.name;
    option.textContent = optionText(app, Date.now());
    return option;
  }));
  $("appPick").value = CoderApps.current;
  const here = CoderApps.list.find((app) => app.name === CoderApps.current);
  $("appPick").title = here ? `${here.path}\ndata: ${here.data}` : "";
  $("rescanBtn").disabled = !here || here.scanning;
  $("rescanBtn").textContent = here?.scanning ? "Refreshing…" : "Refresh";
}

/** Poll until `name` stops scanning; resolves to its final listing entry. */
async function untilScanned(name) {
  for (;;) {
    const listing = await fetchApps();
    const app = listing.apps.find((a) => a.name === name);
    CoderApps.list = listing.apps;
    fillPicker();
    if (!app || !app.scanning) return app;
    await sleep(POLL_MS);
  }
}

const send = (path, body) => CoderApps.request(path, { method: "POST",
  headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

/** Wait for a scan to end, then show the result:
 * error line, or reload (`switchTo`: open that app). */
async function finishScan(name, switchTo) {
  message(`Refreshing ${name}…`);
  const app = await untilScanned(name);
  if (app?.error) { message(`Refresh failed: ${app.error}`, true); return; }
  message("");
  if (switchTo) open(name, false); else open(name);
}

async function rescan() {
  message("");
  $("rescanBtn").disabled = true;
  try {
    await send("/api/rescan", {});
    await finishScan(CoderApps.current, false);
  } catch (error) { message(`Refresh failed: ${error.message}`, true); fillPicker(); }
}

async function scanFolder(event) {
  event.preventDefault();
  const body = { path: $("scanPath").value.trim(), name: $("scanName").value.trim() };
  $("scanGo").disabled = true;
  message("");
  try {
    await send("/api/apps", body);
    $("scanForm").hidden = true;
    await finishScan(body.name, true);
  } catch (error) { message(error.message, true); }
  $("scanGo").disabled = false;
}

function toggleScanForm(show = $("scanForm").hidden) {
  $("scanForm").hidden = !show;
  if (show) $("scanPath").focus();
}

function suggestName() {
  if ($("scanName").dataset.typed) return;
  $("scanName").value = $("scanPath").value.trim().replace(/\/+$/, "").split("/").pop().replace(/[^A-Za-z0-9._-]/g, "-");
}

function onKey(event) {
  if (event.ctrlKey || event.metaKey || event.altKey || !["[", "]"].includes(event.key)) return;
  if (event.target.closest?.("input, textarea, select, [contenteditable]")) return;
  const names = CoderApps.list.map((app) => app.name);
  const next = stepApp(names, CoderApps.current, event.key === "[" ? -1 : 1);
  if (next !== CoderApps.current) open(next);
}

// ---------- app manager: every app the server knows, with Open, Rescan, Rename, Remove ----------

/** "3.2 MB" for a byte count; "" without one. */
function bytesText(bytes) {
  if (bytes == null) return "";
  const units = ["B", "kB", "MB", "GB"];
  let value = bytes, unit = 0;
  while (value >= 1000 && unit < units.length - 1) { value /= 1000; unit++; }
  return `${value < 10 && unit ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

/** The facts line of one app in the manager. */
function factsText(app, now) {
  const scanner = app.scannerVersion == null ? "" : ` · scanner ${app.scannerVersion}${app.staleScanner ? " (old, refresh)" : ""}`;
  const counts = app.nodes == null ? "" : ` · ${app.nodes} classes, ${app.edges} connections`;
  const graph = app.graphBytes == null ? "" : ` · graph.json ${bytesText(app.graphBytes)}`;
  return `${app.scanning ? "refreshing…" : ago(app.scannedAt, now)}${scanner}${counts}${graph}`
    + ` · ${app.notes} notes · ${app.tours} tours`;
}

/** A DOM element: el("button", { className: "btn", onclick }, "text", child...). */
function el(tag, { dataset = {}, ...props } = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  Object.assign(node.dataset, dataset);
  node.append(...children.filter((child) => child !== null && child !== undefined && child !== false));
  return node;
}

const manager = { apps: [], open: null, mode: null };  // open: the row showing rename/remove

const appsMessage = (text, isError = false) => {
  $("appsMsg").textContent = text;
  $("appsMsg").classList.toggle("error", isError);
};

async function loadManager() {
  manager.apps = (await CoderApps.request("/api/apps?details=1")).apps;
  renderManager();
}

/** After a remove or rename: show the new list; leave an app that is gone. */
function afterChange(listing, goTo) {
  manager.apps = listing.apps;
  manager.open = manager.mode = null;
  CoderApps.list = listing.apps;
  if (goTo !== undefined) { open(goTo, false); return; }
  fillPicker();
  renderManager();
}

async function removeApp(app, deleteData, deleteNotes) {
  try {
    const query = `?data=${deleteData ? 1 : 0}&notes=${deleteNotes ? 1 : 0}`;
    const result = await CoderApps.request(`/api/apps/${encodeURIComponent(app.name)}${query}`, { method: "DELETE" });
    appsMessage(`Removed ${app.name}` + (result.deleted.length ? `; deleted ${result.deleted.join(", ")}` : "; its files stay"));
    const gone = app.name === CoderApps.current;
    afterChange(result, gone ? (result.apps[0]?.name ?? "") : undefined);
  } catch (error) { appsMessage(`Remove failed: ${error.message}`, true); }
}

async function renameApp(app, name) {
  try {
    const result = await CoderApps.request(`/api/apps/${encodeURIComponent(app.name)}`, { method: "PATCH",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
    appsMessage(`Renamed ${app.name} to ${name}`);
    afterChange(result, app.name === CoderApps.current ? name : undefined);
  } catch (error) { appsMessage(`Rename failed: ${error.message}`, true); }
}

async function rescanApp(app, options = {}) {
  try {
    await CoderApps.request(`/api/rescan?app=${encodeURIComponent(app.name)}`, { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(options) });
    app.scanning = true;
    renderManager();
    const done = await untilScanned(app.name);
    appsMessage(done?.error ? `Refresh failed: ${done.error}` : `Refreshed ${app.name}`, Boolean(done?.error));
    await loadManager();
  } catch (error) { appsMessage(`Refresh failed: ${error.message}`, true); }
}

function renameRow(app) {
  const input = el("input", { className: "rename-input", value: app.name, ariaLabel: `New name for ${app.name}` });
  const save = () => renameApp(app, input.value.trim());
  input.addEventListener("keydown", (event) => { if (event.key === "Enter") save(); });
  setTimeout(() => input.select());
  return el("div", { className: "app-actions" }, input,
    el("button", { className: "btn small", onclick: save }, "Save name"),
    el("button", { className: "btn small", onclick: () => { manager.open = null; renderManager(); } }, "Cancel"));
}

/** The in-modal remove question: keep the files, or delete the scan data (own files only if ticked). */
function removeRow(app) {
  const own = app.files?.own || [];
  const notesBox = own.length ? el("input", { type: "checkbox", className: "remove-notes" }) : null;
  return el("div", { className: "app-confirm", role: "group", ariaLabel: `Remove ${app.name}` },
    el("div", {}, `Remove ${app.name}? The repo itself is never touched. Data folder: ${app.data}`),
    el("div", { className: "choices" },
      el("button", { className: "btn small", dataset: { act: "remove-keep" }, onclick: () => removeApp(app, false, false) },
        "Remove from the list (keep its files)"),
      el("button", { className: "btn small danger", dataset: { act: "remove-delete" },
        onclick: () => removeApp(app, true, Boolean(notesBox?.checked)) },
        `Remove and delete its scan data (${(app.files?.generated || []).join(", ") || "nothing made yet"})`),
      el("button", { className: "btn small", onclick: () => { manager.open = null; renderManager(); } }, "Cancel")),
    notesBox && el("label", {}, notesBox, ` Also delete what you and the AI wrote there: ${own.join(", ")}`));
}

/** The in-modal "Redo levels" question: opt-in, AI-drafted level notes by default, names kept unless ticked. */
function relevelRow(app) {
  const names = el("input", { type: "checkbox", className: "relevel-names" });
  const redo = (relevel) => {
    manager.open = null;
    rescanApp(app, { relevel, resetLevelNames: names.checked });
  };
  return el("div", { className: "app-confirm", role: "group", ariaLabel: `Redo levels of ${app.name}` },
    el("div", {}, `Refresh ${app.name} and guess every level again. The per-class level notes are cleared so the fresh guess shows; ` +
      "what was cleared is saved as notes.levels-<time>.bak.jsonl in the data folder. Your level count and names are kept unless ticked below."),
    el("div", { className: "choices" },
      el("button", { className: "btn small", dataset: { act: "relevel-ai" }, onclick: () => redo("ai") },
        "Redo, clearing AI-drafted levels only"),
      el("button", { className: "btn small danger", dataset: { act: "relevel-all" }, onclick: () => redo("all") },
        "Redo, clearing yours too"),
      el("button", { className: "btn small", onclick: () => { manager.open = null; renderManager(); } }, "Cancel")),
    el("label", {}, names, " Also reset the level count and names"));
}

function appRow(app, now) {
  const here = app.name === CoderApps.current;
  const act = (mode) => () => { manager.open = app.name; manager.mode = mode; renderManager(); };
  const busy = manager.open === app.name;
  return el("li", { dataset: { app: app.name } },
    el("div", { className: "app-name" }, app.name, here && el("span", { className: "chip" }, "open now")),
    el("div", { className: "app-path", title: "Repo folder" }, app.path),
    el("div", { className: "app-path", title: "Data folder (graph, notes, layout, tours)" }, `data: ${app.data}`),
    el("div", { className: "app-facts" }, factsText(app, Date.now())),
    app.error && el("div", { className: "app-error" }, `Scan error: ${app.error}`),
    busy && manager.mode === "rename" ? renameRow(app) : el("div", { className: "app-actions" },
      el("button", { className: "btn small", disabled: here, onclick: () => open(app.name) }, "Open"),
      el("button", { className: "btn small", disabled: app.scanning, onclick: () => rescanApp(app) }, app.scanning ? "Refreshing…" : "Refresh"),
      el("button", { className: "btn small", disabled: app.scanning, dataset: { act: "relevel" }, onclick: act("relevel") }, "Redo levels…"),
      el("button", { className: "btn small", onclick: act("rename") }, "Rename"),
      el("button", { className: "btn small", onclick: act("remove") }, "Remove…")),
    busy && manager.mode === "relevel" && relevelRow(app),
    busy && manager.mode === "remove" && removeRow(app));
}

function renderManager() {
  const now = Date.now();
  $("appsList").replaceChildren(...manager.apps.map((app) => appRow(app, now)));
  if (!manager.apps.length) $("appsList").append(el("li", { className: "muted" }, "No projects registered yet."));
}

async function openManager() {
  manager.open = manager.mode = null;
  appsMessage("");
  $("appsDialog").showModal();
  try { await loadManager(); } catch (error) { appsMessage(error.message, true); }
}

function bindManager() {
  $("appsBtn").addEventListener("click", openManager);
  $("appsClose").addEventListener("click", () => $("appsDialog").close());
  $("appsScanFolder").addEventListener("click", () => { $("appsDialog").close(); toggleScanForm(true); });
}

/** The hosted page with the helper found: say that everything runs on this PC. */
function showHosted() {
  $("readOnlyLabel").hidden = false;
  $("readOnlyLabel").textContent = HOSTED_LABEL;
  $("readOnlyLabel").title = `Maps, scans and changes stay on this PC (${CoderApps.helper}); this page only hosts the viewer`;
}

/** What the page shows when start() found nothing to open (HTML). */
CoderApps.emptyHtml = () => (CoderApps.hosted && !CoderApps.helper ? NO_HELPER_HTML : NO_APPS_HTML);

/** Pick the app (before anything else loads), build the toolbar. False: no apps at all. */
CoderApps.start = async () => {
  const helper = await hostedHelper();
  CoderApps.hosted = Boolean(helper);
  if (helper) {
    CoderApps.helper = await probeHelper(helper) ? helper : null;
    if (!CoderApps.helper) return false;  // nothing to show: emptyHtml() says how to start one
  }
  const listing = await fetchApps();
  CoderApps.list = listing.apps;
  const names = listing.apps.map((app) => app.name);
  CoderApps.current = chooseApp(names, { hash: decodeURIComponent(location.hash.slice(1)),
    initial: listing.initial, last: read("localStorage", LAST_KEY) });
  if (CoderApps.current) write("localStorage", LAST_KEY, CoderApps.current);
  fillPicker();
  if (CoderApps.hosted) showHosted();
  $("appPick").addEventListener("change", () => open($("appPick").value));
  $("rescanBtn").addEventListener("click", rescan);
  bindManager();
  $("scanCancel").addEventListener("click", () => toggleScanForm(false));
  $("scanForm").addEventListener("submit", scanFolder);
  $("scanPath").addEventListener("input", suggestName);
  $("scanName").addEventListener("input", () => { $("scanName").dataset.typed = "1"; });
  document.addEventListener("keydown", onKey);
  const here = listing.apps.find((app) => app.name === CoderApps.current);
  if (here?.scanning) finishScan(here.name, false);  // a scan began before this page loaded
  return Boolean(CoderApps.current);
};

if (typeof module !== "undefined") module.exports = CoderApps;
else window.CoderApps = CoderApps;
})();
