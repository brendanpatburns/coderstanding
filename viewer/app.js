// Coderstanding viewer. Draws what the server's view engine returns; no graph logic here.
"use strict";

const CAP = 1500;  // most nodes the viewer draws at once
const KIND_ORDER = ["generalization", "realization", "composition", "association", "signal", "dependency"];
const THEMES = ["auto", "light", "dark"];
// Auto layout, line bends and label spots live in layout.js (CoderLayout).
const QUIET_EDGES = 25;  // above this many lines, call names show only on hover (hints always)
const ROUTE_CAP = 800;   // above this many lines, skip bending lines around boxes (speed)
const EDGE_FONT = 10.5;

const state = {
  meta: null, level: 1, view: "all", show: { scene: true, external: true, test: false },
  filter: "",            // "" | "status:<s>" | "tag:<t>": dims the classes that do not match
  crumbs: [],            // zoom path: [{label, zoom: {from, to, level, via}}]
  mode: "view",          // "view" | "edit"
  saved: new Map(),      // key -> {key, x, y, pinned} from layout.json
  auto: new Map(),       // key -> {x, y} automatic spots for this session (not saved)
  undo: [], redo: [],    // layout changes: [{key, before, after}]
  current: null,         // last /api/view response
  selected: null,        // {type: "node"|"edge", raw}
  nodeInfo: new Map(),   // id -> /api/node response
  expanded: new Set(),   // class ids opened into their functions (per view, not saved)
  activeFn: null,        // {cls, fn, ...} the hovered or selected function: its lines light up
  hotBundle: null,       // key of the fan-out line under the mouse: its names show
  fnFilter: new Map(),   // class id -> filter text for its function list
  listScroll: new Map(), // "<id>#fn" | "<id>#field" -> first shown row of a long list
  edgeLabelRects: [],    // where class-line labels sit, so fan-out labels keep off them
  names: new Map(),      // id -> name shown on screen (with a path when two names clash)
  stackShown: false,
  focus: null,           // targets the view highlights (a tour step, DESIGN 7c): the server's `focus`
};
let cy = null;
const $ = (id) => document.getElementById(id);

// ---------- helpers ----------

const api = (path, options) => CoderApps.request(path, options);

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function token(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim();
}

const measureCtx = document.createElement("canvas").getContext("2d");
function textWidth(text, font) {
  measureCtx.font = font;
  return measureCtx.measureText(text).width;
}

function nodeName(id) {
  if (state.names.has(id)) return state.names.get(id);
  const node = state.current?.nodes.find((n) => n.id === id);
  return node ? node.name : id;
}

// ---------- names on screen: enough path to tell clashing names apart ----------

function fileTail(file, parts) {
  return file.split("/").slice(-parts).join("/");
}

/** The shortest file tails (at least folder/file) that differ for every node in `group`. */
function distinctTails(group) {
  const most = Math.max(...group.map((n) => n.file.split("/").length));
  for (let parts = 2; parts < most; parts++) {
    const tails = group.map((n) => fileTail(n.file, parts));
    if (new Set(tails).size === tails.length) return tails;
  }
  return group.map((n) => n.file || n.id);
}

/** id -> shown name: the API's name, or for names shared by several shown nodes the file path
 *  (`src/main.gd`), after the name when it is not the file's own name. */
function displayNames(nodes) {
  const names = new Map(nodes.map((n) => [n.id, n.name]));
  const byName = new Map();
  nodes.forEach((n) => byName.set(n.name, [...(byName.get(n.name) || []), n]));
  for (const group of byName.values()) {
    if (group.length < 2 || group.some((n) => !n.file)) continue;
    distinctTails(group).forEach((tail, i) => {
      const n = group[i];
      const base = n.file.split("/").pop();
      const own = base === n.name || base.replace(/\.[^.]+$/, "") === n.name;
      names.set(n.id, own ? tail : `${n.name} (${tail})`);
    });
  }
  return names;
}

// ---------- theme ----------

function currentTheme() {
  try { return localStorage.getItem("coderstanding-theme") || "auto"; } catch { return "auto"; }
}

function applyTheme(name) {
  if (name === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", name);
  $("themeBtn").textContent = `Theme: ${name}`;
  try { localStorage.setItem("coderstanding-theme", name); } catch { /* private window */ }
  if (cy) cy.style(graphStyle());
}

function cycleTheme() {
  const now = document.documentElement.getAttribute("data-theme") || "auto";
  applyTheme(THEMES[(THEMES.indexOf(now) + 1) % THEMES.length]);
}

// ---------- graph style (from the CSS tokens, so both themes match the page) ----------

function nodeStyles(t) {
  return [
    { selector: "node", style: {
      shape: "round-rectangle", "corner-radius": 7, width: "data(w)", height: "data(h)",
      "background-color": t.surface, "border-width": 1.3, "border-color": t.ink,
      label: "data(label)", "text-wrap": "wrap", "text-valign": "center", "text-halign": "center",
      "font-family": t.mono, "font-size": 12, "font-weight": 500, color: t.ink, "line-height": 1.25 } },
    { selector: "node.k-interface", style: { "border-style": "dashed", "font-style": "italic" } },
    { selector: "node.abstract", style: { "font-style": "italic" } },
    { selector: "node.k-external", style: { "background-color": t.ext, "border-color": t.muted, "corner-radius": 16 } },
    { selector: "node.k-scene", style: { "background-color": t.scene, "border-color": t.accent, "corner-radius": 2 } },
    { selector: "node.warn", style: { "border-color": t.warn } },
    { selector: "node.pinned", style: { "border-width": 2.8 } },
    { selector: "node.faded", style: { "background-color": t.bg, "border-color": t.faded, color: t.faded } },
    { selector: "node.hover", style: { "border-color": t.accent, "border-width": 2.2 } },
    { selector: "node.sel", style: { "border-color": t.accent, "border-width": 2.4 } },
    { selector: "node.dim", style: { opacity: 0.25 } },
    { selector: "node.focus", style: { "border-color": t.hl, "border-width": 2.6 } },
  ];
}

function edgeStyles(t) {
  return [
    { selector: "edge", style: {
      "curve-style": "bezier", width: 1.6, "line-color": t.accent, "target-arrow-shape": "vee",
      "target-arrow-color": t.accent, "source-arrow-color": t.accent, "arrow-scale": 1.1,
      label: "data(label)", "font-family": t.body, "font-size": 10.5, color: t.ink,
      "text-outline-color": t.surface, "text-outline-width": 3, "min-zoomed-font-size": 8,
      "text-wrap": "ellipsis", "text-max-width": 200, "text-margin-x": "data(lx)", "text-margin-y": "data(ly)" } },
    { selector: "edge.routed", style: { "curve-style": "segments", "edge-distances": "node-position",
      "segment-distances": "data(bendD)", "segment-weights": "data(bendW)", "segment-radii": 24 } },
    { selector: "edge.layer-bad", style: {
      "line-color": t.warn, "target-arrow-color": t.warn, "source-arrow-color": t.warn, width: 2.4 } },
    { selector: "edge.crowded", style: { "text-opacity": 0 } },
    { selector: "edge.k-composition", style: {
      "source-arrow-shape": "diamond", "source-arrow-fill": "filled", "target-arrow-shape": "none" } },
    { selector: "edge.k-dependency", style: { "line-style": "dashed", "line-dash-pattern": [6, 4] } },
    { selector: "edge.k-signal", style: { "line-style": "dotted", width: 2.6 } },
    { selector: "edge.k-generalization, edge.k-realization", style: {
      "line-color": t.muted, "target-arrow-color": t.muted, "target-arrow-shape": "triangle",
      "target-arrow-fill": "hollow" } },
    { selector: "edge.k-realization", style: { "line-style": "dashed", "line-dash-pattern": [11, 5] } },
    // Hollow outlined line = the connection runs through hidden classes.
    { selector: "edge.derived", style: {
      width: 2.4, "line-color": t.surface, "line-outline-width": 1.6, "line-outline-color": t.derived,
      "target-arrow-color": t.derived, "source-arrow-color": t.derived, color: t.derived } },
    // Grey = outside the zoom.
    { selector: "edge.faded", style: {
      "line-color": t.faded, "target-arrow-color": t.faded, "source-arrow-color": t.faded,
      color: t.faded, width: 1.3 } },
    { selector: "edge.faded.derived", style: { "line-color": t.surface, "line-outline-color": t.faded } },
    { selector: "edge.quiet", style: { "text-opacity": 0 } },
    { selector: "edge.hover", style: { width: 3.2, "text-opacity": 1, "z-index": 10 } },
    { selector: "edge.sel", style: {
      "line-color": t.hl, "target-arrow-color": t.hl, "source-arrow-color": t.hl, color: t.hl } },
    { selector: "edge.sel.derived", style: { "line-color": t.surface, "line-outline-color": t.hl } },
    { selector: "edge.dim", style: { opacity: 0.2 } },
    { selector: "edge.focus", style: {
      "line-color": t.hl, "target-arrow-color": t.hl, "source-arrow-color": t.hl, color: t.hl, width: 2.6 } },
    { selector: "edge.focus.derived", style: { "line-color": t.surface, "line-outline-color": t.hl } },
  ];
}

function graphStyle() {
  const names = ["bg", "surface", "ink", "muted", "accent", "derived", "hl", "faded", "ext", "scene", "warn"];
  const t = Object.fromEntries(names.map((n) => [n, token(n)]));
  t.mono = token("f-mono");
  t.body = token("f-body");
  return [...nodeStyles(t), ...edgeStyles(t), ...fanStyles()];
}

// ---------- engine output -> Cytoscape elements ----------

const HIDDEN_FLAGS = ["partial", "singleton", HUB_FLAG];  // singleton shows as `*`, hub as a badge

/** The name on a box: the shown name, `*` for a singleton (as in the mock-up). */
function boxName(node) {
  return `${state.names.get(node.id) || node.name}${node.flags.includes("singleton") ? "*" : ""}`;
}

/** Flags without a badge, status and #tags, for the box's second line. */
function nodeMarks(node) {
  const flags = node.flags.filter((f) => !HIDDEN_FLAGS.includes(f) && !START_BADGES[f]);
  return [...flags, node.status, ...(node.tags || []).map((t) => `#${t}`)].filter(Boolean).join(" · ");
}

function nodeElement(node) {
  const name = boxName(node);
  const marks = nodeMarks(node);
  const label = marks ? `${name}\n${marks}` : name;
  const lines = label.split("\n");
  const font = `500 12px ${token("f-mono")}`;
  const width = Math.max(...lines.map((line) => textWidth(line, font))) + 24;
  const data = fanNodeData(node, { id: node.id, name, marks, label, w: width, h: lines.length * 15 + 14, raw: node });
  const classes = [`k-${node.kind}`, node.faded && "faded", node.flags.includes("abstract") && "abstract",
    node.warnings?.length && "warn", state.saved.get(node.id)?.pinned && "pinned",
    isExpandable(node) && "expandable", data.open && "open", inFocus("nodes", node.id) && "focus"];
  return { group: "nodes", data, classes: classes.filter(Boolean).join(" ") };
}

function primaryKind(kinds) {
  return KIND_ORDER.find((k) => kinds.includes(k)) || kinds[0] || "dependency";
}

function callSummary(calls, max = 3) {
  const names = [...new Set(calls.map((c) => c.member))];  // one per member, not per caller
  const more = names.length > max ? ` +${names.length - max}` : "";
  return names.slice(0, max).join(", ") + more;
}

function edgeLabel(edge) {
  const text = (edge.layer?.length ? "⚠ " : "") + (edge.hint || callSummary(edge.calls));
  if (!edge.as) return text;
  return text ? `as ${edge.as}: ${text}` : `as ${edge.as}`;
}

function edgeElement(edge) {
  const classes = [`k-${primaryKind(edge.kinds)}`, !edge.direct && "derived", edge.faded && "faded",
    edge.fanned && "fanned", edge.layer?.length && "layer-bad", inFocus("edges", `${edge.from}→${edge.to}`) && "focus"];
  return { group: "edges",
    data: { id: `e:${edge.key}`, source: edge.from, target: edge.to, label: edgeLabel(edge), raw: edge,
      lx: 0, ly: 0, bendD: [0], bendW: [0.5] },
    classes: classes.filter(Boolean).join(" ") };
}

/** True when the server's focus (a tour step's targets) lists this class id or "from→to" line. */
function inFocus(kind, id) {
  return Boolean(state.focus && state.current?.focus?.[kind]?.includes(id));
}

function elementsOf(view) {
  state.names = displayNames(view.nodes);
  const ids = new Set(view.nodes.map((n) => n.id));
  const edges = view.edges.filter((e) => ids.has(e.from) && ids.has(e.to)).map(edgeElement);
  if (edges.length > QUIET_EDGES) {
    edges.filter((e) => !e.data.raw.hint).forEach((e) => { e.classes += " quiet"; });
  }
  return [...view.nodes.map(nodeElement), ...edges];
}

// ---------- positions ----------

function spotOf(id) {
  return state.saved.get(id) || state.auto.get(id);
}

/** The auto layout for the drawn classes, without moving anything; returns id -> {x, y}.
 *  `fixed` spots (id -> {x, y}) stay put and the other classes are placed around them;
 *  `moreEdges` ({from, to, kinds}) add lines that aren't drawn, e.g. from lower levels. */
function computeLayout(fixed = new Map(), method = layoutMethod().method) {
  const { nodes, edges } = layoutInput();
  const at = Object.fromEntries(nodes.map((n) => [n.id, spotOf(n.id)]).filter(([, spot]) => spot));
  return CoderLayout.layout(nodes, edges, fixed, { method, center: selectedClass(), origin: at });
}

/** The drawn classes and lines as layout.js input (box sizes, label room). */
function layoutInput() {
  const nodes = cy.nodes().map((n) => {
    const raw = n.data("raw");
    return { id: n.id(), w: n.data("w"), h: n.data("h"), level: raw.level, kind: raw.kind, flags: raw.flags };
  });
  const font = `${EDGE_FONT}px ${token("f-body")}`;
  const edges = cy.edges().map((e) => ({ from: e.data("source"), to: e.data("target"), kinds: e.data("raw").kinds,
    label: Math.min(200, textWidth(e.data("label") || "", font)) / 2 }));
  return { nodes, edges };
}

// ---------- auto layout method (Organic, Layered, Hub, Grid), remembered per browser ----------

const METHOD_KEY = "coderstanding-layout-method";

function savedMethod() {
  try { return localStorage.getItem(METHOD_KEY) || CoderLayout.METHODS[0]; } catch { return CoderLayout.METHODS[0]; }
}

/** The chosen method, or Grid when the drawn graph is too big for a force layout. */
function layoutMethod() {
  return CoderLayout.pickMethod($("autoMethod").value, cy.nodes().length);
}

function onMethod() {
  try { localStorage.setItem(METHOD_KEY, $("autoMethod").value); } catch { /* private window */ }
  updateAutoTitle();
  autoLayout();
}

/** The drawn class the selection belongs to (a function's class, a line's "from" class), or null. */
function selectedClass() {
  const sel = state.selected;
  const id = sel?.type === "fn" ? sel.raw.cls : sel?.type === "edge" ? sel.raw.from : sel?.raw?.id;
  return id && cy.getElementById(id).nonempty() ? id : null;
}

/** Hub names its centre on the Auto layout button and menu ("Hub around net.gd"). */
function updateAutoTitle() {
  let title = "Place every unpinned class automatically";
  if ($("autoMethod").value === "hub" && cy.nodes().nonempty()) {
    const { nodes, edges } = layoutInput();
    const centre = CoderLayout.hubCentre(nodes, edges, selectedClass());
    title = `Hub around ${nodeName(centre)}`;
  }
  $("autoBtn").title = title;
  $("autoMethod").title = title;
}

/** Spots of the drawn classes that `keep` says must stay put. */
function spotsOf(keep) {
  const spots = new Map();
  cy.nodes().forEach((n) => {
    const spot = keep(n.id());
    if (spot) spots.set(n.id(), spot);
  });
  return spots;
}

/** Fill automatic spots for every class, once, level by level (level 1 laid out on its own,
 *  each next level placed around the earlier ones), so spots don't jump between levels. */
async function seedAutoSpots() {
  const viewAt = (level) => api(`/api/view?${new URLSearchParams({ level, view: "all" })}`);
  const views = await Promise.all(Array.from({ length: state.meta.max_level }, (_, i) => viewAt(i + 1)));
  if (views.at(-1).nodes.length > CAP) return;
  const steps = views.map((view) => {
    cy.elements().remove();
    cy.add(elementsOf(view));
    return layoutInput();
  });
  cy.elements().remove();
  const spots = CoderLayout.layoutByLevels(steps, state.saved, { method: $("autoMethod").value });
  for (const [id, spot] of spots) if (!state.saved.has(id)) state.auto.set(id, spot);
}

/** Draw every box at its spot, or at `kept` (id -> closed spot, e.g. unsaved View-mode moves) where given. */
function placeNodes(kept = null) {
  const missing = cy.nodes().filter((n) => !spotOf(n.id()));
  if (missing.nonempty()) {
    const fresh = computeLayout(spotsOf(spotOf));
    missing.forEach((n) => state.auto.set(n.id(), fresh.get(n.id())));
  }
  cy.nodes().forEach((n) => n.position(grownSpot(n.id(), kept?.get(n.id()) || spotOf(n.id()))));
  routeEdges();
}

// ---------- lines: bends around boxes and label spots (geometry in layout.js) ----------

/** Bend points as Cytoscape segment weights and distances (relative to the centre line). */
function segmentData(a, b, bends) {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len2 = dx * dx + dy * dy || 1;
  const len = Math.sqrt(len2);
  return {
    bendW: bends.map((p) => ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2),
    bendD: bends.map((p) => ((p.x - a.x) * -dy + (p.y - a.y) * dx) / len),
  };
}

/** Lines that would run through other boxes (e.g. skipping a column) bend around them. */
function bendEdges(edges) {
  const rects = new Map(cy.nodes().map((n) => [n.id(), rectOf(n)]));
  edges.forEach((e) => {
    const a = e.source().position();
    const b = e.target().position();
    const others = [...rects].filter(([id]) => id !== e.source().id() && id !== e.target().id()).map(([, r]) => r);
    const bends = e.source() === e.target() ? [] : CoderLayout.routeAround(a, b, others);
    e.toggleClass("routed", bends.length > 0);
    if (bends.length) e.data(segmentData(a, b, bends));
  });
}

/** Points along a drawn line, for its label spot. */
function edgePath(e) {
  const s = e.sourceEndpoint();
  const t = e.targetEndpoint();
  const bends = e.hasClass("routed") ? e.segmentPoints() : null;
  if (bends) return [s, ...bends, t];
  const cp = e.controlPoints()?.[0];
  if (!cp) return [s, t];
  return Array.from({ length: 9 }, (_, i) => {
    const k = i / 8;
    const u = 1 - k;
    return { x: u * u * s.x + 2 * u * k * cp.x + k * k * t.x, y: u * u * s.y + 2 * u * k * cp.y + k * k * t.y };
  });
}

/** Spread the shown labels so none sits on a box or another label (busy hubs at level 1). */
function placeEdgeLabels(edges) {
  const font = `${EDGE_FONT}px ${token("f-body")}`;
  const shown = edges.filter((e) => e.data("label") && !e.hasClass("quiet"));
  const items = shown.map((e) => ({ id: e.id(), w: Math.min(200, textWidth(e.data("label"), font)) + 6, h: 15,
    path: edgePath(e), required: Boolean(e.data("raw").hint) }));
  const boxes = cy.nodes().map((n) => {
    const r = rectOf(n);
    return { x1: r.x1 - 2, x2: r.x2 + 2, y1: r.y1 - 2 - (n.data("top") ? FACE.badge : 0), y2: r.y2 + 2 };
  });
  const spots = CoderLayout.placeLabels(items, boxes);
  state.edgeLabelRects = [];
  shown.forEach((e, i) => {
    const spot = spots.get(e.id());
    e.toggleClass("crowded", !spot);
    if (!spot) return;
    const mid = e.midpoint();
    e.data({ lx: spot.x - mid.x, ly: spot.y - mid.y });
    const { w, h } = items[i];
    state.edgeLabelRects.push({ x1: spot.x - w / 2, x2: spot.x + w / 2, y1: spot.y - h / 2, y2: spot.y + h / 2 });
  });
}

/** Bend lines around boxes, then place their labels; also after drags and Auto layout. */
function routeEdges() {
  const edges = cy.edges().filter((e) => !e.hasClass("fanned"));
  if (edges.length <= ROUTE_CAP) bendEdges(edges);
  placeEdgeLabels(edges);
  scheduleFan();
}

function isPinned(id) {
  return Boolean(state.saved.get(id)?.pinned);
}

function applyGrab() {
  cy.nodes().forEach((n) => (state.mode === "view" && isPinned(n.id()) ? n.ungrabify() : n.grabify()));
}

// ---------- layout edits (Edit mode), with undo ----------

async function applyChanges(changes, side) {
  const put = [];
  const remove = [];
  for (const change of changes) {
    const item = change[side];
    if (item) { state.saved.set(change.key, item); put.push(item); }
    else { state.saved.delete(change.key); remove.push(change.key); }
    const node = cy.getElementById(change.key);
    if (node.nonempty()) {
      node.toggleClass("pinned", isPinned(change.key));
      const spot = grownSpot(change.key, spotOf(change.key));
      if (spot) node.animate({ position: { x: spot.x, y: spot.y }, duration: 200 });
    }
  }
  applyGrab();
  updateUndoButtons();
  setTimeout(routeEdges, 250);
  try {
    await api("/api/layout", { method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items: put, remove }) });
  } catch (error) {
    showOverlay(`Could not save the layout: ${error.message}`, true);
  }
}

function commit(changes) {
  if (!changes.length) return;
  state.undo.push(changes);
  state.redo = [];
  applyChanges(changes, "after");
}

function undo() {
  const changes = state.undo.pop();
  if (!changes) return;
  state.redo.push(changes);
  applyChanges(changes, "before");
}

function redo() {
  const changes = state.redo.pop();
  if (!changes) return;
  state.undo.push(changes);
  applyChanges(changes, "after");
}

function updateUndoButtons() {
  $("undoBtn").disabled = !state.undo.length;
  $("redoBtn").disabled = !state.redo.length;
}

function moveChange(node, pinned = isPinned(node.id())) {
  const { x, y } = closedSpot(node);  // saved spots are closed-box spots
  return { key: node.id(), before: state.saved.get(node.id()) || null, after: { key: node.id(), x, y, pinned } };
}

function onDragFree(event) {
  if (state.mode === "edit") commit([moveChange(event.target)]);
}

function togglePin(id) {
  const node = cy.getElementById(id);
  if (node.nonempty() && state.mode === "edit") commit([moveChange(node, !isPinned(id))]);
  renderSelected();
}

function autoLayout() {
  const { method, fellBack } = layoutMethod();
  if (fellBack) flashNote(`${cy.nodes().length} classes: too many for ${$("autoMethod").value}, used Grid.`);
  const fresh = computeLayout(spotsOf((id) => (isPinned(id) ? state.saved.get(id) : null)), method);
  const movable = cy.nodes().filter((n) => !isPinned(n.id()));
  if (state.mode === "view") {  // preview only: snaps back on the next view change
    movable.forEach((n) => n.animate({ position: fresh.get(n.id()), duration: 300 }));
    setTimeout(() => { routeEdges(); fitReadable(); }, 350);
    return;
  }
  setTimeout(fitReadable, 50);  // after the saved spots are drawn
  commit(movable.map((n) => {
    const { x, y } = fresh.get(n.id());
    return { key: n.id(), before: state.saved.get(n.id()) || null, after: { key: n.id(), x, y, pinned: false } };
  }));
}

function setMode(mode) {
  state.mode = mode;
  $("app").classList.toggle("editing", mode === "edit");
  $("modeView").setAttribute("aria-pressed", String(mode === "view"));
  $("modeEdit").setAttribute("aria-pressed", String(mode === "edit"));
  placeNodes();  // drop unsaved View-mode moves
  applyGrab();
  updateUndoButtons();
  renderSelected();
}

// ---------- loading and drawing a view ----------

function currentZoom() {
  return state.crumbs.at(-1)?.zoom || null;
}

function viewParams() {
  const params = new URLSearchParams({ level: state.level, view: state.view,
    scene: state.show.scene ? 1 : 0, external: state.show.external ? 1 : 0, test: state.show.test ? 1 : 0 });
  const zoom = currentZoom();
  if (zoom) {
    params.set("zoom_from", zoom.from);
    if (zoom.to) params.set("zoom_to", zoom.to);
    params.set("zoom_level", zoom.level);
    zoom.via.forEach((id) => params.append("via", id));
  }
  state.expanded.forEach((id) => params.append("expand", id));
  (state.focus || []).forEach((id) => params.append("focus", id));
  return params;
}

function showOverlay(html, isError = false) {
  const overlay = $("overlay");
  overlay.innerHTML = `<p>${html}</p>`;
  overlay.classList.toggle("error", isError);
  overlay.hidden = false;
}

let loadToken = 0;
/** `inPlace`: redraw without moving the camera or any box, even ones dragged or previewed in View mode. */
async function loadView({ inPlace = false } = {}) {
  const mine = ++loadToken;
  let view;
  try {
    [view] = await Promise.all([api(`/api/view?${viewParams()}`), refreshFilter()]);  // tags set elsewhere show up
  } catch (error) {
    showOverlay(esc(`Could not load the view: ${error.message}`), true);
    return;
  }
  if (mine !== loadToken) return;  // a newer request is on its way
  state.current = view;
  keepExpanded(view);
  updateCounts(view);
  renderCrumbs();
  if (view.nodes.length > CAP) {
    cy.elements().remove();
    showOverlay(`${view.nodes.length} classes in this view; the viewer draws at most ${CAP}.
      Lower the detail level, switch off a type, or zoom into a connection.`);
    return;
  }
  $("overlay").hidden = true;
  draw(view, inPlace);
}

function draw(view, inPlace = false) {
  const kept = inPlace ? new Map(cy.nodes().map((n) => [n.id(), closedSpot(n)])) : null;
  cy.batch(() => {
    cy.elements().remove();
    cy.add(elementsOf(view));
  });
  placeNodes(kept);
  applyGrab();
  applyFilter();
  if (!inPlace) fitReadable();
  if (!view.nodes.length) showOverlay("Nothing to show in this view.");
  keepSelection();
  updateAutoTitle();
  drawFan();
}

const READABLE_ZOOM = 0.82;  // box names (13.5px) stay at least ~11px on screen

/** Fit the view, but never below a readable zoom: when the graph is bigger than the window,
 *  fit the biggest important part that still reads (all classes without outside code, else the
 *  app's start and hubs) and let the user pan; still too big: start at the app's start. */
function fitReadable() {
  const nodes = cy.nodes();
  if (nodes.empty()) return;
  const classes = nodes.filter((n) => n.data("raw").kind !== "external");
  const key = nodes.filter((n) => isKeyClass(n.data("raw")));
  const parts = [nodes, classes, key].filter((part) => part.nonempty());
  for (const part of parts) {
    cy.fit(part, 40);
    if (cy.zoom() >= READABLE_ZOOM) {
      if (part !== nodes && cy.zoom() > 1) { cy.zoom(1); cy.center(part); }
      return;
    }
  }
  const start = key.filter((n) => isStartClass(n.data("raw")));
  cy.zoom(READABLE_ZOOM);
  cy.center(start.nonempty() ? start : parts.at(-1));
}

function isStartClass(raw) {
  return ["godot-main", "program-main"].some((flag) => (raw.flags || []).includes(flag));
}

function isKeyClass(raw) {
  return isStartClass(raw) || (raw.flags || []).includes("hub");
}

function keepSelection() {
  const sel = state.selected;
  if (sel?.type === "fn") {
    state.selected = fnById(sel.raw.id);
    state.activeFn = state.selected?.raw || null;
    renderSelected();
    return;
  }
  const id = sel && (sel.type === "node" ? sel.raw.id : `e:${sel.raw.key}`);
  const element = id && cy.getElementById(id);
  if (element && element.nonempty()) {
    element.addClass("sel");
    state.selected = { type: sel.type, raw: element.data("raw") };
  } else {
    state.selected = null;
  }
  renderSelected();
}

function updateCounts(view) {
  $("levelOut").textContent = `Level ${state.level} · ${view.counts.shown}/${view.counts.total}`;
  $("levelOut").title = `${view.counts.shown} of ${view.counts.total} classes shown`;
}

// ---------- zoom and breadcrumbs ----------

function zoomLevel() {
  const zoom = currentZoom();
  return zoom ? zoom.level + 1 : state.level;
}

function pushZoom(label, zoom) {
  if (state.focus) return;  // a tour step's view: no zooming
  state.crumbs.push({ label, zoom });
  state.selected = null;
  loadView();
}

function zoomIntoEdge(edge) {
  const via = [...new Set(edge.via.flat())];
  pushZoom(`${nodeName(edge.from)}→${nodeName(edge.to)}`,
    { from: edge.from, to: edge.to, level: zoomLevel(), via });
}

function zoomIntoNode(node) {
  pushZoom(node.name, { from: node.id, to: null, level: zoomLevel(), via: [] });
}

function goToCrumb(index) {
  state.crumbs = state.crumbs.slice(0, index + 1);
  state.selected = null;
  loadView();
}

function renderCrumbs() {
  const nav = $("crumbs");
  const root = `Level ${state.level}`;
  if (!state.crumbs.length) {
    nav.innerHTML = `<span class="here">${esc(root)}</span><span>· Double-click a line or class to zoom · ${triangleIcon()} opens a class into its functions</span>`;
    return;
  }
  const parts = [`<button data-crumb="-1">${esc(root)}</button>`];
  state.crumbs.forEach((crumb, i) => {
    const last = i === state.crumbs.length - 1;
    parts.push(last ? `<span class="here">${esc(crumb.label)}</span>`
      : `<button data-crumb="${i}">${esc(crumb.label)}</button>`);
  });
  nav.innerHTML = parts.join("<span>›</span>");
}

// ---------- details panel (each row is a named field; Hint and Description are editable) ----------

function field(name, label, valueHtml) {
  if (valueHtml === "" || valueHtml == null) return "";
  return `<dt>${esc(label)}${FieldHelp.helpButton(name)}</dt><dd data-field="${name}">${valueHtml}</dd>`;
}

// ---------- editable notes: hint and description of the selected class or connection ----------

const NOTE_FIELDS = [
  { name: "hint", label: "Hint", tag: "input", placeholder: "Short, one line (shown on the line or box)" },
  { name: "description", label: "Description", tag: "textarea", placeholder: "Longer, in your own words" },
  { name: "status", label: "Status", tag: "select", nodeOnly: true },
  { name: "tags", label: "Tags", tag: "input", nodeOnly: true, placeholder: "net, audio (comma separated)" },
  { name: "rules", label: "Rules", tag: "textarea", nodeOnly: true, fnToo: true, placeholder: "Never do X here… (one rule per line)" },
  { name: "review", label: "Review", tag: "textarea", fnToo: true, hideEmpty: true, placeholder: "Code-review findings: smells, risks, suggested splits (not the description)" },
];

/** The notes-store key of a details item: the shown A→B pair for a connection. */
function noteKey(item) {
  return item.key !== undefined ? `edge:${item.from}→${item.to}` : `node:${item.id}`;
}

function noteValue(spec, item) {
  const value = item[spec.name];
  return Array.isArray(value) ? value.join(", ") : value || "";
}

function noteInput(spec, item) {
  const value = noteValue(spec, item);
  const attrs = `class="note-input" data-note="${spec.name}" data-orig="${esc(value)}" placeholder="${esc(spec.placeholder || "")}" aria-label="${spec.label}"`;
  if (spec.tag === "select") {
    const options = ["", ...(state.meta?.statuses || [])].map((s) =>
      `<option value="${esc(s)}"${s === value ? " selected" : ""}>${esc(s || "none")}</option>`);
    return `<select ${attrs}>${options.join("")}</select>`;
  }
  if (spec.tag === "input") return `<input type="text" ${attrs} value="${esc(value)}">`;
  return `<textarea rows="2" ${attrs}>${esc(value)}</textarea>`;
}

const CODE_AUTHOR = "code";  // values the scanner took from a header comment

function noteStatus(spec, item) {
  const by = item[`${spec.name}_by`];
  let chip = "";
  if (item[`${spec.name}_ai`]) {
    const accept = ` <button class="btn small" data-act="accept" data-name="${spec.name}">Accept</button>`;
    chip = `<span class="chip" title="by ${esc(by)}">AI draft</span>${accept}`;
  } else if (by === CODE_AUTHOR && item[spec.name]) {
    chip = `<span class="chip quiet" title="Taken from the header comment in the code. Edit it to make it yours.">from code comment</span>`;
  }
  if (item.stale?.includes(spec.name)) {
    const still = ` <button class="btn small" data-act="still-true" data-name="${spec.name}" title="Keep the text; mark it up to date with the code">Still true</button>`;
    chip += ` <span class="chip stale" title="The code under this changed after the note was written. Check it, then edit it or mark it still true.">code changed since written</span>${still}`;
  }
  return `<div class="note-status">${chip}<span class="saved" hidden>Saved</span><span class="note-error" hidden></span></div>`;
}

function noteFields(item) {
  const isNode = item.key === undefined;
  const isFn = item.fn !== undefined;  // a function: hint, description and rules only
  return NOTE_FIELDS.filter((spec) => !(spec.hideEmpty && !item[spec.name] && state.mode !== "edit"))
    .filter((spec) => (isFn ? !spec.nodeOnly || spec.fnToo : isNode || !spec.nodeOnly))
    .map((spec) => field(spec.name, spec.label, noteInput(spec, item) + noteStatus(spec, item))).join("");
}

function growTextarea(box) {
  box.style.height = "auto";
  box.style.height = `${box.scrollHeight}px`;
}

function growAllNotes() {
  $("details").querySelectorAll("textarea.note-input").forEach(growTextarea);
}

function editingNote() {
  return document.activeElement?.classList?.contains("note-input") && $("details").contains(document.activeElement);
}

function noteStatusOf(name) {
  return $("details").querySelector(`[data-field="${name}"] .note-status`);
}

function flashSaved(name) {
  const saved = noteStatusOf(name)?.querySelector(".saved");
  if (!saved) return;
  saved.hidden = false;
  setTimeout(() => { saved.hidden = true; }, 1500);
}

async function sendNote(path, method, body, name) {
  try {
    await api(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  } catch (error) {
    const box = noteStatusOf(name)?.querySelector(".note-error");
    if (box) { box.textContent = error.message; box.hidden = false; }
    return;
  }
  await loadView({ inPlace: true });  // the hint now shows on the line or box; the camera stays
  flashSaved(name);
}

function saveNote(input) {
  const item = shownItem($("details").dataset.showing);
  const name = input.dataset.note;
  if (!item || input.value === input.dataset.orig) return;
  sendNote("/api/notes", "PUT", { key: noteKey(item.raw), fields: { [name]: input.value } }, name);
}

function acceptNote(item, name) {
  sendNote("/api/notes/accept", "POST", { key: noteKey(item.raw), fields: [name] }, name);
}

function stillTrueNote(item, name) {
  sendNote("/api/notes/still-true", "POST", { key: noteKey(item.raw), fields: [name] }, name);
}

function onNoteKey(event) {
  const input = event.target;
  if (!input.classList?.contains("note-input")) return;
  if (event.key === "Escape") {
    input.value = input.dataset.orig;
    growTextarea(input);
    input.blur();
    event.stopPropagation();
  } else if (event.key === "Enter" && (input.tagName === "INPUT" || event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    input.blur();  // focusout saves
  }
}

function onNoteInput(event) {
  if (event.target.tagName === "TEXTAREA") growTextarea(event.target);
}

function onNoteBlur(event) {
  if (event.target.classList?.contains("note-input") && event.target.tagName !== "SELECT") saveNote(event.target);
}

function onNoteChange(event) {
  if (event.target.classList?.contains("note-input") && event.target.tagName === "SELECT") saveNote(event.target);
}

function callList(calls) {
  return Lists.collapsedList(calls.map((c) => {
    const callers = c.callers.length ? ` title="called from ${esc(c.callers.join(", "))}"` : "";
    return `<li${callers}><code>${esc(c.member)}</code> <span class="count">×${c.count}</span></li>`;
  }));
}

function edgeStatus(edge) {
  if (edge.faded) return "outside the zoom";
  return edge.direct ? "direct" : "through hidden classes";
}

/** The architecture rules a line breaks, each with its reason (`why`). */
function layerList(layers = []) {
  if (!layers.length) return "";
  return `<ul class="warn-list">${layers.map((l) => `<li><span class="tag warn">${esc(l.rule)}</span>
    ${l.why ? esc(l.why) : ""} <span class="count">${esc(l.edge)} at ${esc(l.file)}:${l.line}</span></li>`).join("")}</ul>`;
}

function renderEdge(edge) {
  const calls = Lists.mergeCalls(edge.calls);
  const via = Lists.collapsedList(edge.via.map((path) => `<li>${path.map((id) => esc(nodeName(id))).join(" → ")}</li>`));
  const kinds = edge.kinds.map((k) => `<span class="tag">${esc(k)}</span>`).join("");
  $("details").innerHTML = `
    <h3>${esc(nodeName(edge.from))} → ${esc(nodeName(edge.to))}</h3>
    <div class="sub">${esc(edgeStatus(edge))}</div>
    <dl>
      ${field("layer", "Breaks a rule", layerList(edge.layer))}
      ${noteFields(edge)}
      ${field("as", "As", edge.as ? `<code>${esc(edge.as)}</code>` : "")}
      ${field("kinds", "Kinds", kinds)}
      ${field("calls", `Calls (${calls.length})`, callList(calls))}
      ${field("via", "Via", via)}
    </dl>
    <div class="actions"><button class="btn" data-act="zoom-edge">Zoom into this connection</button></div>`;
  growAllNotes();
}

function linkList(links) {
  if (!links.length) return `<span class="empty">none</span>`;
  const items = links.map((l) => {
    const calls = l.calls.length ? ` <span class="count">${esc(callSummary(l.calls))}</span>` : "";
    return `<li><code>${esc(l.name)}</code> <span class="count">${esc(l.kind)}</span>${calls}</li>`;
  });
  return Lists.collapsedList(items);
}

function foldList(items, render) {
  if (!items.length) return "";
  return `<details><summary>${items.length}</summary><ul>${items.map(render).join("")}</ul></details>`;
}

function nodeInfoFields(info) {
  if (!info) return field("loading", "More", `<span class="empty">loading…</span>`);
  const member = (m) => `<li><code>${esc(m.name)}</code> <span class="count">${esc(m.kind)}${m.type ? ": " + esc(m.type) : ""}</span></li>`;
  const outside = (r) => `<li><code>${esc(r.to)}</code> <span class="count">${esc(r.kind)} ×${r.at.length}</span></li>`;
  return [
    field("base", "Base", info.base ? `<code>${esc(info.base)}</code>` : ""),
    field("namespace", "Namespace", esc(info.namespace)),
    field("out", `Out (${info.out.length})`, linkList(info.out)),
    field("in", `In (${info.in.length})`, linkList(info.in)),
    field("members", "Members", foldList(info.members, member)),
    field("external_refs", "Outside refs", foldList(info.external_refs, outside)),
  ].join("");
}

function warningList(warnings = []) {
  if (!warnings.length) return "";
  return `<ul class="warn-list">${warnings.map((w) => `<li><span class="tag warn" title="${esc(state.meta?.warning_help?.[w.kind] || "")}">${esc(w.text)}</span>${w.why ? ` ${esc(w.why)}` : ""}
    <button class="btn link" data-act="ignore-warning" data-kind="${esc(w.kind)}">Ignore this warning</button></li>`).join("")}</ul>`;
}

function ignoredList(node) {
  const ignored = node.ignored_warnings || [];
  if (!ignored.length) return "";
  const items = ignored.map((w) => `<li><span class="tag">${esc(w.text)}</span> <span class="muted">${esc(w.reason || "")}</span>
    ${w.reason !== "note" ? "" : `<button class="btn link" data-act="unignore-warning" data-kind="${esc(w.kind)}">Undo</button>`}</li>`).join("");
  return `<details><summary>${ignored.length}</summary><ul class="warn-list">${items}</ul></details>`;
}

/** Ignore (or stop ignoring) one kind of warning on a class; stored as its notes field. */
function setIgnored(node, kind, ignore) {
  const now = new Set(node.ignore_warnings || []);
  if (ignore) now.add(kind); else now.delete(kind);
  sendNote("/api/notes", "PUT", { key: `node:${node.id}`, fields: { ignore_warnings: [...now] } }, "warnings");
}

function badgeList(node) {
  const badges = boxBadges(node).map((b) => `<span class="badge-chip b-${b.look}">${esc(b.text)}</span>`);
  const flags = node.flags.filter((f) => !START_BADGES[f] && !["partial", HUB_FLAG].includes(f)).map((f) => `<span class="tag">${esc(f)}</span>`);
  return [...badges, ...flags].join("");
}

function sizeText(node) {
  if (!node.lines) return "";
  const members = node.members ? `, ${node.members} members` : "";
  return `${node.lines} lines${node.span ? "" : " (file)"}${members}`;
}

function testedBy(files = []) {
  if (!files.length) return "";
  return foldList(files, (f) => `<li><code>${esc(f)}</code></li>`);
}

function pinAction(node) {
  if (node.faded) return "";
  if (state.mode !== "edit") return `<span class="count">Switch to Edit layout to pin.</span>`;
  return `<button class="btn" data-act="pin">${isPinned(node.id) ? "Unpin" : "Pin"}</button>`;
}

function renderNode(node) {
  const info = state.nodeInfo.get(node.id);
  $("details").innerHTML = `
    <h3>${esc(nodeName(node.id))}</h3>
    <div class="sub">${esc(node.kind)}${node.faded ? " · outside the zoom" : ""}</div>
    <dl>
      ${noteFields(node)}
      ${field("warnings", "Warnings", warningList(node.warnings))}
      ${field("ignored", "Ignored warnings", ignoredList(node))}
      ${field("file", "File", `<code>${esc(node.file)}:${node.line}</code> <span class="count">${esc(sizeText(node))}</span>`)}
      ${field("level", "Level", String(node.level))}
      ${field("flags", startBadges(node).length ? "Starts as" : "Flags", badgeList(node))}
      ${field("tested_by", "Tested by", testedBy(node.tested_by))}
      ${nodeInfoFields(info)}
    </dl>
    <div class="actions"><button class="btn" data-act="zoom-node">Zoom into class</button>${pinAction(node)}</div>`;
  growAllNotes();
  if (!info) fetchNodeInfo(node);
}

async function fetchNodeInfo(node) {
  try {
    state.nodeInfo.set(node.id, await api(`/api/node/${encodeURIComponent(node.id)}`));
  } catch {
    return;
  }
  const shown = $("details").dataset.showing;
  if (shown === `node:${node.id}`) renderNode(node);
}

function renderHelp() {
  $("details").innerHTML = `<h3>Details</h3>
    <p class="muted">Hover over a class or a connection to see it here. Click a class or a
    connection to keep it here and edit its hint and description. Double-click a line or class
    to zoom. Drag with the middle button to pan. ${triangleIcon()} on a class opens it into its functions.
    <kbd>[</kbd> and <kbd>]</kbd> switch to the previous and next app.</p>
    <h3>Note fields</h3>${FieldHelp.helpList()}`;
}

function showDetails(item) {
  if (editingNote()) return;  // hover must not replace the box being typed in
  $("details").dataset.showing = item ? `${item.type}:${item.raw.id || item.raw.key}` : "";
  if (!item) renderHelp();
  else if (item.type === "node") renderNode(item.raw);
  else if (item.type === "fn") renderFunction(item.raw);
  else renderEdge(item.raw);
}

function renderSelected() {
  showDetails(state.selected);
}

function onDetailsClick(event) {
  const act = event.target.closest("[data-act]")?.dataset.act;
  const shown = $("details").dataset.showing;
  const item = shownItem(shown);
  if (!act || !item) return;
  if (act === "help") { event.target.closest(".help").classList.toggle("open"); return; }
  if (act === "accept") acceptNote(item, event.target.dataset.name);
  if (act === "still-true") stillTrueNote(item, event.target.dataset.name);
  if (act === "zoom-edge") zoomIntoEdge(item.raw);
  if (act === "zoom-node") zoomIntoNode(item.raw);
  if (act === "pin") togglePin(item.raw.id);
  if (act === "ignore-warning") setIgnored(item.raw, event.target.dataset.kind, true);
  if (act === "unignore-warning") setIgnored(item.raw, event.target.dataset.kind, false);
}

function shownItem(shown) {
  if (!shown) return null;
  const [type, ...rest] = shown.split(":");
  if (type === "fn") return fnById(rest.join(":"));
  const element = cy.getElementById(type === "node" ? rest.join(":") : `e:${rest.join(":")}`);
  return element.nonempty() ? { type, raw: element.data("raw") } : null;
}

// ---------- Stack & deps tab ----------

function stackChips(stack) {
  const chips = Object.entries(stack.languages || {}).map(([lang, n]) => `<span><b>${esc(lang)}</b> ${n}</span>`);
  for (const g of stack.godot || []) {
    const extra = [g.renderer, g.csharp && "C#", g.main_scene && `main ${g.main_scene}`].filter(Boolean);
    chips.push(`<span><b>Godot ${esc(g.version)}</b> ${esc(extra.join(", "))}</span>`);
  }
  for (const d of stack.dotnet || []) {
    chips.push(`<span><b>${esc(d.framework || "dotnet")}</b> ${esc(d.file)}</span>`);
  }
  return `<div class="chips">${chips.join("")}</div>`;
}

function usedBy(list = []) {
  if (!list.length) return `<span class="muted">not used in the scanned code</span>`;
  const head = list.slice(0, 4).map((id) => `<code>${esc(id)}</code>`).join(", ");
  if (list.length <= 4) return head;
  return `${head} <details><summary>+${list.length - 4} more</summary>${list.slice(4).map((id) => `<code>${esc(id)}</code>`).join(", ")}</details>`;
}

function depRow(dep) {
  return `<tr><td>${esc(dep.kind)}</td><td><b>${esc(dep.name)}</b><div class="src">${esc(dep.file || "")}</div></td>
    <td class="num">${esc(dep.version || "")}</td><td>${usedBy(dep.used_by)}</td><td class="num">${dep.refs || ""}</td></tr>`;
}

function renderStack(stack) {
  const unresolved = stack.unresolved || { refs: 0, top: [] };
  const top = unresolved.top.map(([name, n]) => `<span><code>${esc(name)}</code> ${n}</span>`).join("");
  $("stack").innerHTML = `
    <h2>Stack</h2>${stackChips(stack)}
    <h2>Dependencies</h2>
    <div class="table-wrap"><table>
      <thead><tr><th>Kind</th><th>Dependency</th><th>Version</th><th>Used by</th><th>Refs</th></tr></thead>
      <tbody>${(stack.deps || []).map(depRow).join("") || `<tr><td colspan="5" class="muted">No dependencies found</td></tr>`}</tbody>
    </table></div>
    <h2>Not mapped to a dependency <span class="count">${unresolved.refs} refs</span></h2>
    <div class="chips">${top}</div>`;
}

const TABS = { graph: "tabGraph", stack: "tabStack", tours: "tabTours" };
let shownTab = "graph";

async function showTab(which) {
  if (which === shownTab) return;
  if (shownTab === "tours") await TourView.leave();
  shownTab = which;
  for (const [name, id] of Object.entries(TABS)) {
    $(id).hidden = name !== which;
    $(`${id}Btn`).setAttribute("aria-selected", String(name === which));
  }
  if (which === "graph") { cy.resize(); return; }
  if (which === "tours") { await TourView.enter(); return; }
  if (!state.stackShown) {
    state.stackShown = true;
    try { renderStack(await api("/api/stack")); }
    catch (error) { $("stack").textContent = `Could not load the stack: ${error.message}`; }
  }
}

// ---------- controls ----------

let levelTimer = 0;
function onLevel() {
  state.level = Number($("level").value);
  state.crumbs = [];
  clearTimeout(levelTimer);
  levelTimer = setTimeout(loadView, 60);
}

function setView(view, load = true) {
  state.view = view;
  $("viewAll").setAttribute("aria-pressed", String(view === "all"));
  $("viewConcrete").setAttribute("aria-pressed", String(view === "concrete"));
  if (load) loadView();
}

function onShow() {
  state.show = { scene: $("showScene").checked, external: $("showExternal").checked, test: $("showTest").checked };
  loadView();
}

// ---------- status / tag filter (dims classes that do not match; the engine is not asked) ----------

function matchesFilter(node) {
  const [kind, value] = state.filter.split(/:(.*)/s);
  if (!kind) return true;
  return kind === "status" ? node.status === value : (node.tags || []).includes(value);
}

function applyFilter() {
  cy.batch(() => {
    cy.nodes().forEach((n) => n.toggleClass("dim", !matchesFilter(n.data("raw"))));
    cy.edges().forEach((e) => e.toggleClass("dim", e.source().hasClass("dim") && e.target().hasClass("dim")));
  });
}

function fillFilter(meta) {
  const options = [["", "All classes"],
    ...meta.statuses.map((s) => [`status:${s}`, `status: ${s}`]),
    ...meta.tags.map((t) => [`tag:${t}`, `#${t}`])];
  if (!options.some(([value]) => value === state.filter)) state.filter = "";
  $("filter").innerHTML = options.map(([value, label]) =>
    `<option value="${esc(value)}"${value === state.filter ? " selected" : ""}>${esc(label)}</option>`).join("");
}

async function refreshFilter() {
  try {
    state.meta = await api("/api/meta");
    fillFilter(state.meta);
  } catch { /* keep the old list */ }
}

function onFilter() {
  state.filter = $("filter").value;
  applyFilter();
}

function onKey(event) {
  if (state.mode !== "edit" || !(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "z") return;
  event.preventDefault();
  if (event.shiftKey) redo(); else undo();
}

function toggleSide() {
  const open = !$("app").classList.contains("side-open");
  $("app").classList.toggle("side-open", open);
  $("sideBody").hidden = !open;
  $("sideToggle").textContent = open ? "‹" : "›";
  $("sideToggle").setAttribute("aria-expanded", String(open));
  requestAnimationFrame(() => cy.resize());
}

function bindControls() {
  $("level").addEventListener("input", onLevel);
  $("viewAll").addEventListener("click", () => setView("all"));
  $("viewConcrete").addEventListener("click", () => setView("concrete"));
  $("showScene").addEventListener("change", onShow);
  $("showExternal").addEventListener("change", onShow);
  $("showTest").addEventListener("change", onShow);
  $("filter").addEventListener("change", onFilter);
  $("modeView").addEventListener("click", () => setMode("view"));
  $("modeEdit").addEventListener("click", () => setMode("edit"));
  $("autoBtn").addEventListener("click", autoLayout);
  $("autoMethod").value = savedMethod();
  $("autoMethod").addEventListener("change", onMethod);
  $("undoBtn").addEventListener("click", undo);
  $("redoBtn").addEventListener("click", redo);
  $("themeBtn").addEventListener("click", cycleTheme);
  $("sideToggle").addEventListener("click", toggleSide);
  $("tabGraphBtn").addEventListener("click", () => showTab("graph"));
  $("tabStackBtn").addEventListener("click", () => showTab("stack"));
  $("tabToursBtn").addEventListener("click", () => showTab("tours"));
  $("details").addEventListener("click", onDetailsClick);
  $("details").addEventListener("keydown", onNoteKey);
  $("details").addEventListener("input", onNoteInput);
  $("details").addEventListener("focusout", onNoteBlur);
  $("details").addEventListener("change", onNoteChange);
  $("crumbs").addEventListener("click", (event) => {
    const index = event.target.dataset?.crumb;
    if (index === undefined) return;
    if (index === "-1") { state.crumbs = []; state.selected = null; loadView(); } else goToCrumb(Number(index));
  });
  document.addEventListener("keydown", onKey);
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => cy.style(graphStyle()));
}

function select(item) {
  cy.elements(".sel").removeClass("sel");
  state.selected = item;
  if (item?.type !== "fn" && state.activeFn) { state.activeFn = null; scheduleFan(); }
  updateAutoTitle();
  if (item && item.type !== "fn") cy.getElementById(item.type === "node" ? item.raw.id : `e:${item.raw.key}`).addClass("sel");
  renderSelected();
  document.dispatchEvent(new CustomEvent("coder:select"));  // the tour editor offers "Add step from selection"
}

/** Hold the middle button and drag to pan, over anything (the fan layers included). */
function bindMiddlePan() {
  const canvas = $("canvas");
  let last = null;
  canvas.addEventListener("mousedown", (event) => {
    if (event.button !== 1) return;
    event.preventDefault();  // no browser autoscroll
    event.stopPropagation();  // Cytoscape and the boxes never see it
    last = { x: event.clientX, y: event.clientY };
  }, true);
  canvas.addEventListener("auxclick", (event) => { if (event.button === 1) event.preventDefault(); }, true);
  window.addEventListener("mousemove", (event) => {
    if (!last) return;
    cy.panBy({ x: event.clientX - last.x, y: event.clientY - last.y });
    last = { x: event.clientX, y: event.clientY };
  });
  window.addEventListener("mouseup", (event) => { if (event.button === 1) last = null; });
}

function bindGraph() {
  bindMiddlePan();
  cy.on("mouseover", "node, edge", (event) => {
    event.target.addClass("hover");
    showDetails({ type: event.target.isNode() ? "node" : "edge", raw: event.target.data("raw") });
  });
  cy.on("mouseout", "node, edge", (event) => {
    event.target.removeClass("hover");
    renderSelected();
  });
  cy.on("tap", "node", (event) => select({ type: "node", raw: event.target.data("raw") }));
  cy.on("tap", "edge", (event) => select({ type: "edge", raw: event.target.data("raw") }));
  cy.on("tap", (event) => { if (event.target === cy) select(null); });
  cy.on("dbltap", "node", (event) => { if (!event.target.data("raw").faded) zoomIntoNode(event.target.data("raw")); });
  cy.on("dbltap", "edge", (event) => zoomIntoEdge(event.target.data("raw")));
  cy.on("dragfree", "node", onDragFree);
  cy.on("dragfree", "node", routeEdges);
}

function edgeRaw(key) {
  const edge = cy.getElementById(`e:${key}`);
  return edge.nonempty() ? edge.data("raw") : null;
}

function selectEdgeByKey(key) {
  const raw = edgeRaw(key);
  if (raw) select({ type: "edge", raw });
}

// ---------- Copy for AI: what is on screen as slim text (/api/text, same text as `coderstanding view`) ----------

function selectionParam() {
  const sel = state.selected;
  if (!sel) return "";
  return sel.type === "edge" ? sel.raw.key : sel.raw.id;
}

let noteTimer = 0;
function flashNote(text) {
  $("copyOut").textContent = text;
  clearTimeout(noteTimer);
  noteTimer = setTimeout(() => { $("copyOut").textContent = ""; }, 4000);
}

async function copyText(commandOnly) {
  const params = viewParams();
  if (selectionParam()) params.set("select", selectionParam());
  let text;
  try {
    const response = await fetch(CoderApps.url(`/api/text?${params}`));
    text = await response.text();
    if (!response.ok) throw new Error(text);
  } catch (error) {
    flashNote(`Could not get the text: ${error.message}`);
    return;
  }
  if (commandOnly) text = text.split("\n")[0];
  const lines = text.split("\n").length;
  try {
    await navigator.clipboard.writeText(text);
    flashNote(`Copied, ${lines} line${lines === 1 ? "" : "s"}`);
  } catch {
    showCopyBox(text);
  }
}

/** Clipboard refused: show the text selected in a box to copy by hand. */
function showCopyBox(text) {
  $("copyText").value = text;
  $("copyBox").hidden = false;
  $("copyText").focus();
  $("copyText").select();
}

function bindCopy() {
  $("copyBtn").addEventListener("click", () => copyText(false));
  $("copyCmdBtn").addEventListener("click", () => copyText(true));
  $("copyClose").addEventListener("click", () => { $("copyBox").hidden = true; });
}

// ---------- start ----------

/** `?select=<id>` keeps a class in the details panel; `?zoom=<from>` zooms into it (links, checks). */
function selectFromUrl(params) {
  const fn = !params.get("zoom") && fnById(params.get("select") || "#");
  if (fn) { selectFn(fn); return; }
  const node = cy.getElementById(params.get("select") || params.get("zoom") || "");
  if (node.empty()) return;
  if (params.get("zoom")) zoomIntoNode(node.data("raw"));
  else select({ type: "node", raw: node.data("raw") });
}

function applyUrlOptions() {
  const params = new URLSearchParams(location.search);
  applyTheme(params.get("theme") || currentTheme());
  if (params.get("level")) $("level").value = params.get("level");
  state.level = Number($("level").value);
  if (params.get("view") === "concrete") setView("concrete", false);
  if (params.get("mode") === "edit") setMode("edit");
  if (params.get("tests") === "1") { $("showTest").checked = true; state.show.test = true; }
  if (params.get("filter")) state.filter = params.get("filter");
  if (CoderLayout.METHODS.includes(params.get("layout"))) $("autoMethod").value = params.get("layout");
  params.getAll("expand").forEach((id) => state.expanded.add(id));
  return params;
}

// ---------- switching apps: what an app keeps while another is shown ----------

CoderApps.getState = () => ({ level: state.level, view: state.view, crumbs: state.crumbs,
  camera: cy ? { zoom: cy.zoom(), pan: cy.pan() } : null });

function restoreAppState(back) {
  $("level").value = Math.min(back.level, state.meta.max_level);
  state.level = Number($("level").value);
  state.crumbs = back.crumbs || [];
  if (back.view === "concrete") setView("concrete", false);
}

async function init() {
  cy = cytoscape({ container: $("cy"), style: graphStyle(), minZoom: 0.05, maxZoom: 3,
    boxSelectionEnabled: false, autoungrabify: false });
  bindControls();
  bindGraph();
  bindFan();
  bindCopy();
  bindPanels(() => requestAnimationFrame(() => { cy.resize(); scheduleFan(); }));
  renderHelp();
  showOverlay("Loading…");
  try {
    if (!await CoderApps.start()) {
      showOverlay(CoderApps.emptyHtml(), true);
      return;
    }
    const [meta, layout] = await Promise.all([api("/api/meta"), api("/api/layout")]);
    state.meta = meta;
    layout.items.forEach((item) => state.saved.set(item.key, item));
    document.title = `${meta.repo} · Coderstanding`;
    $("repoName").textContent = meta.repo;
    $("scanned").textContent = `scanned ${meta.scannedAt}`;
    $("level").max = meta.max_level;
    const params = applyUrlOptions();
    const back = params.get("level") ? null : CoderApps.takeState();  // returning to an app
    if (back) restoreAppState(back);
    fillFilter(meta);
    await seedAutoSpots();
    await loadView();
    if (back?.camera) { cy.zoom(back.camera.zoom); cy.pan(back.camera.pan); }
    if (params.get("tab") === "stack") showTab("stack");
    if (params.get("tab") === "tours") await showTab("tours");
    else selectFromUrl(params);
  } catch (error) {
    showOverlay(esc(`Could not reach the server: ${error.message}`), true);
  }
}

init();
