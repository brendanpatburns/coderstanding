// Coderstanding viewer. Draws what the server's view engine returns; no graph logic here.
"use strict";

const CAP = 1500;  // most nodes the viewer draws at once
const KIND_ORDER = ["generalization", "realization", "composition", "association", "signal", "dependency", "loaded-by", "manual", "planned"];
const THEMES = ["auto", "light", "dark", "contrast", "extreme"];
// Auto layout, line bends and label spots live in layout.js (CoderLayout).
const ROUTE_CAP = 800;   // above this many lines, skip bending lines around boxes (speed)
const EDGE_FONT = 10.5;

const state = {
  meta: null, level: 1, view: "all", show: { external: true, comm: true, test: false, cards: true, planned: "show" },
  filter: "",            // "" | "status:<s>" | "tag:<t>": dims the classes that do not match
  track: "",             // the progress track whose rings the boxes show ("" = none; progress_view.js)
  crumbs: [],            // zoom path: [{label, zoom: {from, to, level, via}}]
  mode: "view",          // "view" | "edit"
  layout: "layered",     // Auto layout method (CoderLayout.METHODS)
  search: "",            // the search box (lowercase): classes whose name lacks it are dimmed
  saved: new Map(),      // key -> {key, x, y, pinned} from layout.json
  settled: new Map(),    // key -> {x, y}: a saved spot nudged so grown boxes don't overlap (not saved)
  auto: new Map(),       // key -> {x, y} automatic spots for this session (not saved)
  undo: [], redo: [],    // layout changes: [{key, before, after}]
  current: null,         // last /api/view response
  selected: null,        // {type: "node"|"edge", raw}
  nodeInfo: new Map(),   // id -> /api/node response
  expanded: new Set(),   // class ids opened into their functions (per view, not saved)
  activeFn: null,        // {cls, fn, ...} the hovered or selected function: its lines light up
  hotBundle: null,       // key of the fan-out line under the mouse: its names show
  fnFilter: new Map(),   // class id -> filter text for its function list
  listScroll: new Map(), // "<id>#fn" | "<id>#private" | "<id>#property" -> first shown row of a long list
  openGroups: new Set(), // "<id>#private" | "<id>#property": folded groups of open boxes opened (this session)
  edgeLabelRects: [],    // where class-line labels sit, so fan-out labels keep off them
  names: new Map(),      // id -> name shown on screen (with a path when two names clash)
  stackShown: false,
  litKind: null,         // the legend entry whose lines are lit (not saved)
  hiddenFileKinds: [],   // file kinds (`Data`) switched off in the types strip; saved per app
  hiddenTags: [],        // tag notes (`chat`) switched off in the types strip; saved per app
  onlyTags: [],          // tag notes: only classes with one of them (`?tag=`); saved per app
  hiddenKinds: [],       // line kinds (`signal`) switched off in the legend; saved per app
  hiddenTypes: [],       // file types (`.gd`) switched off in the types strip; saved per app
  fanHidden: [],         // "self" = Show › Self calls off (no lines inside an open class); saved per app
  domainView: [],        // "on" = Domains toggle (frames), "every" = every line between frames; saved per app
  boxText: "short",      // Box text: "name" | "short" | "full" (fanout.js BOX_TEXTS); saved per app
  lines: "explained",    // Lines: "explained" (quiet lines faint) | "all"; saved per app
  nearIds: new Set(),    // the hovered and selected classes: their quiet lines are drawn full
  domainFocus: null,     // {name, back}: just this domain (DESIGN 6b); back = the level to return to (0) or null

  focus: null,           // targets the view highlights (a tour step, DESIGN 7c): the server's `focus`
  focusSet: null,        // {nodes, lines} kept lit around the selection or tour step, the rest fades (applyFocus)
  focusAt: 0,            // when the focus set last changed (the faces' fade, fanout.js fadeIn)
  detailsOpen: false,    // the details panel's "Details" fold under "In plain words" is open (per browser)
  tourStep: false,       // the Tours tab shows a step: open boxes list its functions, focused ones lit (fanout.js)
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

/** id -> shown name: the API's title (nickname note, or a one-function file's function; the real name goes on the box's second line, nodeMarks),
 *  else the API's name, or for names shared by several shown nodes the file path
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
  nodes.filter((n) => n.title).forEach((n) => names.set(n.id, n.title));
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
    { selector: "node.comm", style: { shape: "cut-rectangle", "corner-radius": 0, "border-color": t.accent, "border-width": 1.8 } },
    { selector: "node.k-scene", style: { "background-color": t.scene, "border-color": t.accent, "corner-radius": 2 } },
    { selector: "node.warn", style: { "border-color": t.warn } },
    { selector: "node.pinned", style: { "border-width": 2.8 } },
    { selector: "node.faded", style: { "background-color": t.bg, "border-color": t.faded, color: t.faded } },
    { selector: "node.hover", style: { "border-color": t.accent, "border-width": 2.2 } },
    { selector: "node.sel", style: { "border-color": t.accent, "border-width": 2.4 } },
    { selector: "node.dim", style: { opacity: t.opDim, "border-style": t.dimLine } },
    { selector: "node.type-hit", style: { "border-color": t.accent, "border-width": 3 } },
    { selector: "edge.kind-hit", style: { "line-color": t.accent, "target-arrow-color": t.accent, width: 4, "z-index": 9 } },
    { selector: "edge.kind-dim", style: { opacity: t.opFaint, "line-style": t.dimLine } },
    { selector: "node.focus", style: { "border-color": t.hl, "border-width": 2.6 } },
    // Domain cards and frames (domain_view.js draws the cards; these give way to its drawing)
    { selector: "node.k-domain", style: { "background-opacity": 0, "border-opacity": 0 } },
    { selector: "node.dom-gone, edge.dom-hide", style: { display: "none" } },
    { selector: "node.dom-dim, edge.dom-dim", style: { opacity: t.opDim } },
    { selector: "node.foc-dim", style: { opacity: t.opDim, "border-style": t.dimLine } },  // outside the focus set (applyFocus)
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
    { selector: "edge.k-loaded-by", style: { "line-style": "dashed", "line-dash-pattern": [10, 3, 2, 3] } },
    { selector: "edge.k-signal", style: { "line-style": "dotted", width: 2.6 } },
    // Planned work (plan_view.js): a dotted line in the plan colour; added manually: solid, its own colour.
    { selector: "edge.k-planned", style: { "line-style": "dotted", width: 2.4, "line-color": t.plan, "target-arrow-color": t.plan, "source-arrow-color": t.plan } },
    { selector: "edge.k-manual", style: { width: 1.9, "line-color": t.manual, "target-arrow-color": t.manual, "source-arrow-color": t.manual } },
    { selector: ".plan-dim", style: { opacity: t.opDim } },  // Planned work: Only planned work
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
    // Quiet = no hint (Lines "Explained first", the view engine's flag): thin and faint until an end is near.
    { selector: "edge.quiet", style: { "text-opacity": 0, width: 1, opacity: t.opFaint, "line-style": t.dimLine } },
    { selector: "edge.quiet.near", style: { "text-opacity": 1, width: 1.6, opacity: 1 } },
    { selector: "edge.hover", style: { width: 3.2, "text-opacity": 1, "z-index": 10 } },
    { selector: "edge.sel", style: {
      "line-color": t.hl, "target-arrow-color": t.hl, "source-arrow-color": t.hl, color: t.hl } },
    { selector: "edge.sel.derived", style: { "line-color": t.surface, "line-outline-color": t.hl } },
    { selector: "edge.dim", style: { opacity: t.opDim, "line-style": t.dimLine } },
    { selector: "edge.focus", style: {
      "line-color": t.hl, "target-arrow-color": t.hl, "source-arrow-color": t.hl, color: t.hl, width: 2.6 } },
    { selector: "edge.focus.derived", style: { "line-color": t.surface, "line-outline-color": t.hl } },
    { selector: "edge.foc-on", style: { width: 2.6 } },  // in the focus set: drawn bolder, as in the mock-up
    { selector: "edge.foc-dim", style: { opacity: t.opFaint, "line-style": t.dimLine, "text-opacity": 0 } },  // outside the focus set (applyFocus)
  ];
}

function graphStyle() {
  const names = ["bg", "surface", "ink", "muted", "accent", "derived", "hl", "faded", "ext", "scene", "warn", "plan", "manual"];
  const t = Object.fromEntries(names.map((n) => [n, token(n)]));
  t.mono = token("f-mono");
  t.body = token("f-body");
  t.opDim = Number(token("op-dim"));
  t.opFaint = Number(token("op-faint"));
  t.dimLine = token("dim-line");
  return thickened([...nodeStyles(t), ...edgeStyles(t), ...fanStyles()], Number(token("line-boost")));
}

/** The theme's extra line thickness (`--line-boost`, 0 normally) added to every box border and line width. */
function thickened(styles, boost) {
  if (!boost) return styles;
  return styles.map(({ selector, style }) => {
    const out = { ...style };
    for (const key of ["border-width", "width"]) if (typeof out[key] === "number") out[key] += boost;
    return { selector, style: out };
  });
}

// ---------- engine output -> Cytoscape elements ----------

const HIDDEN_FLAGS = ["partial", "singleton", HUB_FLAG];  // singleton shows as `*`, hub as a badge

/** The name on a box: the shown name, `*` for a singleton (as in the mock-up). */
function boxName(node) {
  if (node.comm) return `⇄ ${state.names.get(node.id) || node.name}`;
  return `${state.names.get(node.id) || node.name}${node.flags.includes("singleton") ? "*" : ""}`;
}

/** The real name of a nicknamed class, flags without a badge, status and #tags, for the box's second line. */
function nodeMarks(node) {
  const flags = node.flags.filter((f) => !HIDDEN_FLAGS.includes(f) && !START_BADGES[f]);
  return [node.title && node.name, ...flags, node.status, ...(node.tags || []).map((t) => `#${t}`), cardBadge(node)].filter(Boolean).join(" · ");
}

function nodeElement(node) {
  const name = boxName(node);
  const marks = nodeMarks(node);
  const label = marks ? `${name}\n${marks}` : name;
  const lines = label.split("\n");
  const font = `500 12px ${token("f-mono")}`;
  const width = Math.max(...lines.map((line) => textWidth(line, font))) + 24;
  const base = { id: node.id, name, marks, label, type: fileType(node.file), w: width, h: lines.length * 15 + 14, raw: node };
  const data = node.kind === "domain" ? { ...base, ...domainCardData(node) } : fanNodeData(node, base);
  const classes = [`k-${node.kind}`, node.comm && "comm", node.faded && "faded", node.flags.includes("abstract") && "abstract",
    node.warnings?.length && "warn", state.saved.get(node.id)?.pinned && "pinned",
    isExpandable(node) && "expandable", data.open && "open", inFocus("nodes", node.id) && "focus"];
  return { group: "nodes", data, classes: classes.filter(Boolean).join(" ") };
}

function primaryKind(kinds) {
  return KIND_ORDER.find((k) => kinds.includes(k)) || kinds[0] || "dependency";
}

/** The kind a line's plain words come from (FieldHelp.kindWords): its main kind, or "derived" through hidden classes. */
function lineKind(edge) {
  return edge.direct ? primaryKind(edge.kinds) : "derived";
}

function callSummary(calls, max = 3) {
  const names = [...new Set(calls.map((c) => c.member))];  // one per member, not per caller
  const more = names.length > max ? ` +${names.length - max}` : "";
  return names.slice(0, max).join(", ") + more;
}

/** "§2" on a class or line that 2 specs or notes link (the server's `cards` count, Show menu). */
function cardBadge(item) {
  return item.cards ? `§${item.cards}` : "";
}

function edgeLabel(edge) {
  const text = (edge.layer?.length ? "⚠ " : "") + (edge.cards ? `${cardBadge(edge)} ` : "") + (edge.hint || callSummary(edge.calls));
  if (!edge.as) return text;
  return text ? `as ${edge.as}: ${text}` : `as ${edge.as}`;
}

function edgeElement(edge) {
  const classes = [`k-${primaryKind(edge.kinds)}`, !edge.direct && "derived", edge.faded && "faded", edge.quiet && "quiet",
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
  return [...view.nodes.map(nodeElement), ...edges];
}

// ---------- positions ----------

function spotOf(id) {
  return state.settled.get(id) || state.saved.get(id) || state.auto.get(id);
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

// ---------- auto layout method (Organic, Layered, Hub, Grid): the `layout` view option ----------

/** The chosen method, or Grid when the drawn graph is too big for a force layout. */
function layoutMethod() {
  return CoderLayout.pickMethod($("autoMethod").value, cy.nodes().length);
}

function onMethod() {
  setOption("layout", $("autoMethod").value);
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
  state.settled = CoderLayout.settleFixed(steps.at(-1).nodes, state.saved);  // boxes grew: no overlaps
  const fixed = new Map([...state.saved].map(([id, spot]) => [id, state.settled.get(id) || spot]));
  const spots = CoderLayout.layoutByLevels(steps, fixed, { method: $("autoMethod").value });
  for (const [id, spot] of spots) if (!state.saved.has(id)) state.auto.set(id, spot);
}

/** Box sizes changed for every class (Box text): lay the free classes out again, keep the view. */
async function reseedSpots() {
  state.auto.clear();
  await seedAutoSpots();
  await loadView();
}

/** Draw every box at its spot, or at `kept` (id -> closed spot, e.g. unsaved View-mode moves) where given. */
function placeNodes(kept = null) {
  const missing = cy.nodes().filter((n) => !spotOf(n.id()));
  if (missing.nonempty()) {
    const fresh = computeLayout(spotsOf(spotOf));
    missing.forEach((n) => state.auto.set(n.id(), fresh.get(n.id())));
  }
  cy.nodes().forEach((n) => n.position(grownSpot(n.id(), kept?.get(n.id()) || spotOf(n.id()))));
  placeDomains(Boolean(kept));  // domain frames or a domain focus place the boxes their own way
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

/** Lines that would run through other boxes (e.g. skipping a column) bend around them; lines
 *  bent into the same gap are spread apart so each stays readable. */
function bendEdges(edges) {
  const rects = new Map(cy.nodes(":visible").map((n) => [n.id(), rectOf(n)]));
  const routes = edges.map((e) => {
    const [a, b] = [e.source().position(), e.target().position()];
    const others = [...rects].filter(([id]) => id !== e.source().id() && id !== e.target().id()).map(([, r]) => r);
    return { a, b, bends: e.source() === e.target() ? [] : CoderLayout.routeAround(a, b, others) };
  });
  CoderLayout.nudgeBends(routes);
  edges.forEach((e, i) => {
    const { a, b, bends } = routes[i];
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
  const boxes = cy.nodes(":visible").map((n) => {
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
  const edges = cy.edges(":visible").filter((e) => !e.hasClass("fanned"));  // not lines domain frames draw
  if (edges.length <= ROUTE_CAP) bendEdges(edges);
  placeEdgeLabels(edges);
  scheduleFan();
}

function isPinned(id) {
  return Boolean(state.saved.get(id)?.pinned);
}

/** True while boxes stand where domain frames or a domain focus put them (never saved). */
function domainPlaced() {
  return dom.frames.length > 0 || Boolean(state.current?.domain);
}

function applyGrab() {
  if (dom.frames.length) { cy.nodes().ungrabify(); return; }  // dragging a box drags its frame
  cy.nodes().forEach((n) => (state.mode === "view" && isPinned(n.id()) ? n.ungrabify() : n.grabify()));
}

// ---------- layout edits (Edit mode), with undo ----------

async function applyChanges(changes, side) {
  const put = [];
  const remove = [];
  for (const change of changes) {
    const item = change[side];
    state.settled.delete(change.key);  // a placed spot wins over its nudge
    if (item) { state.saved.set(change.key, item); put.push(item); }
    else { state.saved.delete(change.key); remove.push(change.key); }
    const node = cy.getElementById(change.key);
    if (node.nonempty() && !domainPlaced()) {
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
  if (state.mode === "edit" && !domainPlaced()) commit([moveChange(event.target)]);
}

function togglePin(id) {
  const node = cy.getElementById(id);
  if (node.nonempty() && state.mode === "edit") commit([moveChange(node, !isPinned(id))]);
  renderSelected();
}

function autoLayout() {
  if (domainPlaced()) { flashNote("Domains place the boxes now: switch Domains off (or go back) to use Auto layout."); return; }
  const { method, fellBack } = layoutMethod();
  if (fellBack) flashNote(`${cy.nodes().length} classes: too many for ${$("autoMethod").value}, used Grid.`);
  const fresh = computeLayout(spotsOf((id) => (isPinned(id) ? spotOf(id) : null)), method);
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

/** Edit layout on or off (the `mode` view option's redraw). */
function setMode(mode) {
  state.mode = mode;
  renderOptionControls();
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
    external: state.show.external ? 1 : 0, comm: state.show.comm ? 1 : 0, test: state.show.test ? 1 : 0 });
  state.hiddenTypes.forEach((type) => params.append("hide", type));
  state.hiddenFileKinds.forEach((kind) => params.append("hide_file_kind", kind));
  state.hiddenTags.forEach((tag) => params.append("hide_tag", tag));
  state.onlyTags.forEach((tag) => params.append("tag", tag));
  state.hiddenKinds.forEach((kind) => params.append("hide_kind", kind));
  if (state.show.cards) params.set("badges", "1");  // spec/note counts on classes and lines
  if (state.show.planned === "hide") params.set("planned", "0");
  if (state.track) params.set("track", state.track);
  params.set("lines", state.lines);  // an older helper ignores it: every line equal
  if (state.domainFocus && !state.focus) params.set("domain", state.domainFocus.name);
  const zoom = currentZoom();
  if (zoom) {
    params.set("zoom_from", zoom.from);
    if (zoom.to) params.set("zoom_to", zoom.to);
    params.set("zoom_level", zoom.level);
    zoom.via.forEach((id) => params.append("via", id));
  }
  state.expanded.forEach((id) => params.append("expand", id));
  (state.focus || []).forEach((id) => params.append("focus", id));
  if (state.tourStep && state.focus) params.set("near", "1");  // a tour step opens what its functions call
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
  renderTypeStrip(view);
  renderLegend(view);
  ProgressView.afterLoad(view);
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
  state.nearIds = new Set();
  lightNear();  // the new lines: the selected class's faint ones come back
  updateAutoTitle();
  drawFan();
  updateOverview();
  PlanView.afterDraw(view);
}

const READABLE_ZOOM = 0.82;  // box names (13.5px) stay at least ~11px on screen

/** Fit the view, but never below a readable zoom: when the graph is bigger than the window,
 *  fit the biggest important part that still reads (all classes without outside code, else the
 *  app's start and hubs) and let the user pan; still too big: start at the app's start. */
function fitReadable() {
  const nodes = cy.nodes();
  if (nodes.empty()) return;
  if (dom.frames.length) { fitBox(framesBox(), false); return; }  // domain frames: all of them
  if (state.current?.domain) {  // domain focus: with the cards around it while that still reads, else its classes
    cy.fit(nodes, 40);
    if (cy.zoom() < READABLE_ZOOM / 3) cy.fit(nodes.filter((n) => !n.data("raw").faded), 40);
    if (cy.zoom() > 1) { cy.zoom(1); cy.center(nodes); }
    return;
  }
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

/** Frame the highlighted classes (the server's focus: a tour step, a spec) when it marked any;
 *  never below the readable zoom: then centre on the first focused class (a focused function's, if any). */
function fitFocus() {
  const focus = cy.nodes(".focus");
  if (focus.empty()) return;
  cy.fit(focus, 60);
  if (cy.zoom() > 1.2) { cy.zoom(1.2); cy.center(focus); }
  if (cy.zoom() < READABLE_ZOOM) {
    const fnClass = (state.current.focus.functions || [])[0]?.split("#")[0];
    const main = (fnClass && cy.getElementById(fnClass).nonempty()) ? cy.getElementById(fnClass) : focus[0];
    cy.zoom(READABLE_ZOOM);
    cy.center(main);
  }
  scheduleFan();
}

/** Select a target (CLI syntax: `Class`, `A>B`, `Class#function`; a class by id or name) when it is
 *  on screen; false when it is not. Wiki links and target chips use it. */
function selectInGraph(target) {
  const fn = target.includes("#") && fnById(target);
  if (fn) { selectFn(fn); return true; }
  const byName = (name) => {
    const node = cy.getElementById(name);
    return node.nonempty() ? node : cy.nodes().filter((n) => n.data("raw").name === name);
  };
  const [from, to] = target.split(/>|→/);
  const ids = (name) => byName(name).map((n) => n.id());
  const element = to ? cy.edges().filter((e) => ids(from).includes(e.data("raw").from) && ids(to).includes(e.data("raw").to))
    : byName(target);
  if (element.empty()) return false;
  select({ type: element[0].isNode() ? "node" : "edge", raw: element[0].data("raw") });
  return true;
}

/** Text with its [[wiki links]] (DESIGN 7b) as link spans (`data-target`), escaped. */
function wikiLinksHtml(text) {
  return esc(text).replace(/\[\[([^\]]+)\]\]/g, (_, target) =>
    `<span class="wl" data-target="${target}" role="link" tabindex="0" title="Select ${target}">${target}</span>`);
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
    applyFocus();
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
  applyFocus();
  renderSelected();
}

function updateCounts(view) {
  $("rulerCount").textContent = `${view.counts.shown} of ${view.counts.total} classes`;
  $("rulerCount").title = `${view.counts.shown} of ${view.counts.total} classes shown`;
  renderRuler(view);
  renderLinesCount(view);
}

/** "18 explained · 30 faint" under Explained first (so nothing feels hidden), else "48 lines". */
function renderLinesCount(view) {
  const all = view.edges.filter((e) => !e.faded).length;
  const faint = view.edges.filter((e) => !e.faded && e.quiet).length;
  const quiet = view.lines === "explained";  // an older helper sends no `lines`: all equal
  $("linesCount").textContent = quiet ? `${all - faint} explained · ${faint} faint` : `${all} line${all === 1 ? "" : "s"}, all at the same weight`;
  $("linesCount").title = quiet ? "Hover or select a box to bring its faint lines back" : "";
}

// ---------- Box text and Lines switches (view options) ----------

function renderFaceControls() {
  document.querySelectorAll("#boxTextCtl [data-boxtext]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.boxtext === boxTextMode())));
  document.querySelectorAll("#linesCtl [data-lines]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.lines === state.lines)));
}

function setBoxText(mode) {
  if (mode) setOption("boxText", mode);
}

function setLines(mode) {
  if (mode) setOption("lines", mode);
}

/** The hovered and selected classes bring their faint lines back (class lines and fan-out). */
function lightNear(hovered = null) {
  const near = new Set([hovered, selectedNodeId()].filter(Boolean));
  const same = near.size === state.nearIds.size && [...near].every((id) => state.nearIds.has(id));
  if (same) return;
  state.nearIds = near;
  cy.edges(".near").removeClass("near");
  near.forEach((id) => cy.getElementById(id).connectedEdges(".quiet").addClass("near"));
  if (state.current?.fanout?.some((l) => l.quiet)) scheduleFan();
}

// ---------- focus: the selection (or a tour step) stays lit, the rest fades; client side, no server call ----------

/** What stays lit (fanout.js `focusSet`): a tour step's targets (the server's focus), else the
 *  selected class (a function's class) or line; null when nothing is selected. */
function currentFocusSet() {
  const edges = cy.edges().map((e) => e.data("raw"));
  const focus = state.tourStep && state.current?.focus;
  if (focus) return focusSet(edges, focus.nodes || [], focus.edges || []);
  if (state.selected?.type === "edge") return focusSet(edges, [], [state.selected.raw.key]);
  const id = selectedNodeId();
  return id ? focusSet(edges, [id]) : null;
}

/** Fade everything the focus set leaves out: boxes and class lines here, faces and fan-out lines in fanout.js. */
function applyFocus() {
  const set = currentFocusSet();
  const key = (s) => (s ? `${[...s.nodes].sort()}|${[...s.lines].sort()}` : "");
  if (key(set) !== key(state.focusSet)) state.focusAt = performance.now();  // the faces fade only on a change
  state.focusSet = set;
  cy.batch(() => {
    cy.nodes().forEach((n) => n.toggleClass("foc-dim", Boolean(set) && !set.nodes.has(n.id())));
    cy.edges().forEach((e) => {
      const lit = Boolean(set) && set.lines.has(e.data("raw").key);
      e.toggleClass("foc-dim", Boolean(set) && !lit);
      e.toggleClass("foc-on", lit);
    });
  });
  scheduleFan();
}

/** Hover preview: with nothing selected the details panel shows what is under the pointer; with a
 *  selection, hover leaves the panel alone (a line still shows its sentence tooltip). */
function previewDetails(item) {
  if (!state.selected) showDetails(item);
}

/** The pointer left a class, line or function: back to the help text, unless something is selected. */
function endPreview() {
  if (!state.selected) renderSelected();
}

/** Esc clears the selection (graph tab; not while typing or in a dialog). */
function onSelectionKey(event) {
  if (event.key !== "Escape" || !state.selected || $("tabGraph").hidden) return;
  if (event.target.closest?.("input, textarea, select, dialog, [contenteditable]") || document.querySelector("dialog[open]")) return;
  select(null);
}

// ---------- depth ruler (DESIGN 5, 6): the only level control ----------

function levelName(level) {
  return state.current?.scale.names[level] || `Level ${level}`;
}

function levelText(level) {
  return `${level} · ${levelName(level)}`;
}

function renderRuler(view) {
  const zoom = currentZoom();
  const cols = Ruler.columns(view.scale, view.level, zoom ? zoom.level : null, state.meta.domains && !state.domainFocus);
  Ruler.render($("rulerCols"), cols);
}

/** Show a level (from the ruler or an arrow key); a zoom is left. */
function setLevel(level) {
  const first = state.meta.domains && !state.domainFocus ? 0 : 1;  // a domain focus shows classes
  level = Math.max(first, Math.min(level, state.current?.scale.count ?? state.meta.max_level));
  if (level === state.level && !state.crumbs.length) return;
  state.level = level;
  state.crumbs = [];
  clearTimeout(levelTimer);
  levelTimer = setTimeout(loadView, 60);
}

function onRulerKey(event) {
  if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
  if (event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
  if ($("tabGraph").hidden || event.target.closest?.("input, textarea, select, dialog, [contenteditable]")) return;
  event.preventDefault();
  setLevel((state.current?.level ?? state.level) + (event.key === "ArrowRight" ? 1 : -1));
}

// "Levels…": how many levels and what to call them (saved in the app's settings, never rescans)
function openLevels() {
  const scale = state.current.scale;
  $("levelsCount").value = scale.count;
  fillLevelNames(scale.names);
  $("levelsMsg").textContent = "";
  $("levelsDialog").showModal();
}

function fillLevelNames(names) {
  const count = Number($("levelsCount").value) || names.length - 1;
  $("levelsNames").innerHTML = Array.from({ length: count + 1 }, (_, level) =>
    `<label>${level} <input class="note-input" data-name="${level}" value="${esc(names[level] ?? `Level ${level}`)}"></label>`).join("");
}

function currentNameInputs() {
  return [...$("levelsNames").querySelectorAll("input")].map((input) => input.value.trim());
}

async function saveLevels(event, reset = false) {
  event.preventDefault();
  const body = reset ? { reset: true } : { count: Number($("levelsCount").value), names: currentNameInputs() };
  try {
    await api("/api/levels", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  } catch (error) {
    $("levelsMsg").textContent = error.message;
    return;
  }
  $("levelsDialog").close();
  state.meta = await api("/api/meta");
  state.level = Math.min(state.level, state.meta.max_level);
  loadView();
}

// ---------- zoom and breadcrumbs ----------

function zoomLevel() {
  const zoom = currentZoom();
  return zoom ? state.current.level : state.level;  // the level the server showed
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
  const root = levelText(state.level);
  if (state.domainFocus && !state.focus) {
    const parts = focusCrumbs(root);
    state.crumbs.forEach((crumb, i) => parts.push(i === state.crumbs.length - 1 ? `<span class="here">${esc(crumb.label)}</span>`
      : `<button data-crumb="${i}">${esc(crumb.label)}</button>`));
    nav.innerHTML = parts.join("<span>›</span>") + (state.crumbs.length ? "" : "<span>· Esc or the level goes back to the frames</span>");
    tintFocusCrumbs(nav);
    return;
  }
  if (!state.crumbs.length) {
    const frames = dom.frames.length ? " · Double-click a domain frame to show only it" : "";
    nav.innerHTML = `<span class="here">${esc(root)}</span><span>· Double-click a line or class to zoom${frames} · ${triangleIcon()} opens a class into its functions</span>`;
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
  { name: "nickname", label: "Nickname", tag: "input", nodeOnly: true, fnToo: true, placeholder: "A readable name for a badly named one (update_world)" },
  { name: "hint", label: "Hint", tag: "input", placeholder: "Short, one line (shown on the line or box)" },
  { name: "description", label: "Description", tag: "textarea", placeholder: "Longer, in your own words" },
  { name: "status", label: "Status", tag: "select", nodeOnly: true },
  { name: "tags", label: "Tags", tag: "input", nodeOnly: true, placeholder: "net, audio (comma separated)" },
  { name: "rules", label: "Rules", tag: "textarea", nodeOnly: true, fnToo: true, placeholder: "Never do X here… (one rule per line)" },
  { name: "loaded_how", label: "Loaded how", tag: "input", nodeOnly: true, hideEmpty: true, placeholder: "How the loader starts it: auto-loaded by the language registry" },
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

/** The "Loaded by" field: classes that load this one by name (a registry, a tool), from notes or an app rule. */
function loadedByHtml(node) {
  const links = (node.loaders || []).map((l) => {
    const fromNote = l.source === "note";
    const how = l.how ? ` <span class="count">${esc(l.how)}</span>` : "";
    const origin = fromNote ? "" : ` <span class="chip quiet" title="The app's loaded-by rule ${esc(l.rule)}">rule ${esc(l.rule)}</span>`;
    const remove = fromNote ? ` <button class="btn link" data-act="loaded-remove" data-id="${esc(l.from)}">Remove</button>` : "";
    return `<li><code>${esc(l.name)}</code>${how}${origin}${remove}</li>`;
  });
  const list = links.length ? `<ul class="warn-list">${links.join("")}</ul>` : "";
  const picker = node.faded ? "" : `<input class="loaded-pick" id="loadedPick" type="search" placeholder="Find the class that loads it…" aria-label="Find the class that loads this one" autocomplete="off">
    <ul class="spec-picks" id="loadedPicks" hidden></ul>`;
  return list + picker + noteStatus({ name: "loaded_by" }, node);
}

let loadedPickTimer = 0;
function findLoaders(query) {
  clearTimeout(loadedPickTimer);
  loadedPickTimer = setTimeout(async () => {
    const box = $("loadedPicks");
    if (!box) return;
    let found = [];
    try {
      found = query.trim() ? (await api(`/api/find?q=${encodeURIComponent(query)}`)).classes.slice(0, 8) : [];
    } catch { /* no results */ }
    box.innerHTML = found.map((c) => `<li><button type="button" data-act="loaded-add" data-id="${esc(c.id)}" title="${esc(c.file)}">${esc(c.name)} <span class="muted small">${esc(c.file)}</span></button></li>`).join("");
    box.hidden = !found.length;
  }, 180);
}

function setLoadedBy(node, ids) {
  sendNote("/api/notes", "PUT", { key: `node:${node.id}`, fields: { loaded_by: ids } }, "loaded_by");
}

function growTextarea(box) {
  box.style.height = "auto";
  box.style.height = `${box.scrollHeight}px`;
}

function growAllNotes() {
  $("details").querySelectorAll("textarea.note-input").forEach(growTextarea);
}

function editingNote() {
  const active = document.activeElement;
  return (active?.classList?.contains("note-input") || active?.id === "loadedPick") && $("details").contains(active);
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
  if (event.target.id === "loadedPick") findLoaders(event.target.value);
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
  $("details").innerHTML = edgePlainHtml(edge) + detailsFold(`
    <div class="sub">${esc(edgeStatus(edge))}</div>
    <dl>
      ${field("layer", "Breaks a rule", layerList(edge.layer))}
      ${noteFields(edge)}
      ${field("as", "As", edge.as ? `<code>${esc(edge.as)}</code>` : "")}
      ${field("kinds", "Kinds", kinds)}
      ${field("calls", `Calls (${calls.length})`, callList(calls))}
      ${field("via", "Via", via)}
      ${field("cards", "Specs & notes", SpecView.linkedHtml(edge.key))}
    </dl>
    <div class="actions"><button class="btn" data-act="zoom-edge">Zoom into this connection</button></div>`);
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

/** A message's text in the details: `name (client→server)`. */
function messageItem(message) {
  return `<li><code>${esc(message.name)}</code> <span class="count">${esc(message.direction)}</span></li>`;
}

/** A communications node's facts (a message node: its messages and who sends them), or a
 * class's network messages: the ones it sends and the remote-call (@rpc) ones it handles. */
function commFields(node) {
  const comm = node.comm;
  const items = (names) => `<ul>${names.map((n) => `<li><code>${esc(n)}</code></li>`).join("")}</ul>`;
  if (comm?.type === MESSAGE_TYPE) {
    const messages = comm.messages.map((name) => ({ name, direction: comm.directions?.[name] || "" }));
    return [field("comm_owner", "Handled by", `<code>${esc(comm.owner)}</code> <span class="count">${esc(comm.at.file)}:${comm.at.line}</span>`),
      field("comm_messages", `Messages (${messages.length})`, foldList(messages, messageItem))].join("");
  }
  if (comm) {
    return [field("comm_type", "Endpoint", `<code>${esc(comm.type)}</code>`),
      field("comm_role", "Role", esc(comm.role)),
      field("comm_owner", "Held by", `<code>${esc(comm.owner)}${comm.field ? "#" + esc(comm.field) : ""}</code> <span class="count">${esc(comm.at.file)}:${comm.at.line}</span>`),
      field("comm_messages", "Messages", comm.messages.length ? items(comm.messages) : "")].join("");
  }
  const sends = node.messages?.sends || [], handles = node.messages?.handles || [];
  return [field("sends", `Sends messages (${sends.length})`, sends.length ? foldList(sends, messageItem) : ""),
    field("rpcs", `Remote calls (@rpc, ${handles.length})`, handles.length ? foldList(handles, messageItem) : "")].join("");
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

/** "Open in editor" (VS Code) and "View on Forgejo" links for a line of a file; links only, nothing runs.
 * The helper's meta.source says where the repo is and, when it has a usable remote, its web page. */
function sourceLinks(path, line) {
  const source = state.meta?.source;
  if (!path || !source) return "";
  const web = CoderApps.webUrl(source.web, path, line);
  const link = (href, text) => `<a href="${esc(href)}" target="_blank" rel="noopener">${text} &#8599;</a>`;
  return `<div class="source-links">${link(CoderApps.editorUrl(source.root, path, line), "Open in editor")}${web ? link(web, "View on Forgejo") : ""}</div>`;
}

function renderNode(node) {
  const info = state.nodeInfo.get(node.id);
  $("details").innerHTML = PlanView.nodeHtml(node) + nodePlainHtml(node) + ProgressView.detailsHtml(node) + PlanView.planHtml(node) + detailsFold(`
    <div class="sub">${esc(node.kind)}${node.faded ? " · outside the zoom" : ""}</div>
    <dl>
      ${noteFields(node)}
      ${field("loaded_by", "Loaded by", loadedByHtml(node))}
      ${field("warnings", "Warnings", warningList(node.warnings))}
      ${field("ignored", "Ignored warnings", ignoredList(node))}
      ${field("file", "File", node.file ? `<code>${esc(node.file)}:${node.line}</code> <span class="count">${esc(sizeText(node))}</span>${sourceLinks(node.file, node.line)}` : "")}
      ${field("level", "Level", String(node.level))}
      ${commFields(node)}
      ${field("flags", startBadges(node).length ? "Starts as" : "Flags", badgeList(node))}
      ${field("tested_by", "Tested by", testedBy(node.tested_by))}
      ${field("cards", "Specs & notes", SpecView.linkedHtml(node.id))}
      ${nodeInfoFields(info)}
    </dl>
    <div class="actions"><button class="btn" data-act="zoom-node">Zoom into class</button>${pinAction(node)}${refButtons(node.id, node.description || node.hint)}</div>`);
  growAllNotes();
  if (!info) fetchNodeInfo(node);
}

// ---------- "In plain words" on top of the details panel; today's fields under the "Details" fold ----------

/** A class in plain words (field_help.js builds it): what it does, the lines on screen both ways, where it sits. */
function nodePlainHtml(node) {
  const edges = cy.edges().map((e) => e.data("raw"));
  const [usedBy, uses] = FieldHelp.plainLines(node.id, edges, { nameOf: nodeName, kindOf: lineKind, hintOf: whatItDoes });
  const fns = node.functions ? `${node.functions} function${node.functions === 1 ? "" : "s"}` : "";
  const name = levelName(node.level);
  const level = name.toLowerCase() === `level ${node.level}` ? name.toLowerCase() : `level ${node.level} ${name}`;
  const where = [node.domain, level, fns].filter(Boolean);
  return FieldHelp.classPlainHtml({ name: nodeName(node.id), where, warnings: node.warnings?.length || 0, what: whatItDoes(node), usedBy, uses });
}

/** A line in plain words: "A uses B: hint", the same sentence as its hover tooltip. */
function edgePlainHtml(edge) {
  return FieldHelp.linePlainHtml({ from: nodeName(edge.from), to: nodeName(edge.to), kind: lineKind(edge),
    hint: whatItDoes(edge), calls: callSummary(edge.calls), key: edge.key });
}

/** Today's details fields, folded under the plain words; open or closed as the user last left it. */
function detailsFold(inner) {
  return `<details class="details-fold"${state.detailsOpen ? " open" : ""}><summary>Details</summary>${inner}</details>`;
}

/** The fold remembers itself (the `details` view option, per browser); no redraw: it is open already. */
function onFoldToggle(event) {
  if (!event.target.classList?.contains("details-fold") || event.target.open === state.detailsOpen) return;  // drawn open: not a click
  state.detailsOpen = event.target.open;
  setOption("details", state.detailsOpen ? "open" : "closed");
  if (state.detailsOpen) growAllNotes();  // a closed fold measured its boxes at no height
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
    connection to keep it here in plain words (what it does, who uses it, what it uses) and fade
    everything not connected to it; its hint, description and other fields sit under Details.
    While something is selected, hovering leaves this panel alone; <kbd>Esc</kbd> or a click on
    empty canvas clears the selection. Double-click a line or class
    to zoom. Drag with the middle button to pan. ${triangleIcon()} on a class opens it into its functions;
    its private functions (<code>_name</code>, not Godot callbacks like <code>_ready</code>) and its fields
    fold into one row each ("▸ 12 fields"): click the row to show them. Show › Self calls hides the
    lines between functions of the same class.
    Show › Domains sorts the classes into one frame per domain: click a frame's header to fold it,
    drag it to move it (not saved), double-click it to show only that domain (Esc goes back).
    <kbd>[</kbd> and <kbd>]</kbd> switch to the previous and next app.</p>
    <h3>Note fields</h3>${FieldHelp.helpList()}
    <h3>Words</h3>${FieldHelp.wordsList()}`;
}

function showDetails(item) {
  if (editingNote() || $("details").dataset.fieldsForm || $("details").dataset.planForm) return;  // hover must not replace the box being typed in
  $("details").dataset.showing = item ? `${item.type}:${item.raw.id || item.raw.key}` : "";
  if (!item) renderHelp();
  else if (item.type === "node") renderNode(item.raw);
  else if (item.type === "fn") renderFunction(item.raw);
  else renderEdge(item.raw);
}

function renderSelected() {
  showDetails(state.selected);
}

async function onFieldRulesAct(act, button) {
  const post = (body) => api("/api/fields", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const details = $("details");
  if (act === "edit-fields") { details.dataset.fieldsForm = "1"; details.innerHTML = FieldHelp.editorHtml(); return; }
  if (act === "fields-cancel") { delete details.dataset.fieldsForm; renderHelp(); return; }
  try {
    if (act === "fields-reset") FieldHelp.setRules(await post({ reset: [button.dataset.field] }));
    else FieldHelp.setRules(await post(FieldHelp.readForm(details)));
  } catch (error) {
    const box = $("fieldsError");
    if (box) { box.textContent = error.message; box.hidden = false; }
    return;
  }
  if (act === "fields-reset") { details.innerHTML = FieldHelp.editorHtml(); return; }
  delete details.dataset.fieldsForm;
  renderHelp();
}

function onDetailsClick(event) {
  const button = event.target.closest("[data-act]");
  const act = button?.dataset.act;
  if (act && /^(edit-fields|fields-)/.test(act)) { onFieldRulesAct(act, button); return; }
  if (act?.startsWith("plan-")) { PlanView.act(act, button, shownItem($("details").dataset.showing)); return; }
  const shown = $("details").dataset.showing;
  const item = shownItem(shown);
  if (!act || !item) return;
  if (act === "help") { event.target.closest(".help").classList.toggle("open"); return; }
  if (act === "open-card") { const b = event.target.closest("[data-act]"); SpecView.open(b.dataset.id, b.dataset.folder); return; }
  if (act === "loaded-add") setLoadedBy(item.raw, [...new Set([...(item.raw.loaded_by || []), button.dataset.id])]);
  if (act === "loaded-remove") setLoadedBy(item.raw, (item.raw.loaded_by || []).filter((id) => id !== button.dataset.id));
  if (act === "accept") acceptNote(item, event.target.dataset.name);
  if (act === "still-true") stillTrueNote(item, event.target.dataset.name);
  if (act === "ask-class") askAi(describeRequest(item.raw), flashNote, showCopyBox);
  if (act === "ask-line") { const edge = edgeRaw(button.dataset.key); if (edge) askAi(describeRequest(edge), flashNote, showCopyBox); }
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
  const chips = (bucket) => bucket.top.map(([name, n]) => `<span><code>${esc(name)}</code> ${n}</span>`).join("");
  const outside = stack.outside || { refs: 0, top: [] };
  $("stack").innerHTML = `
    <h2>Stack</h2>${stackChips(stack)}
    <h2>Dependencies</h2>
    <div class="table-wrap"><table>
      <thead><tr><th>Kind</th><th>Dependency</th><th>Version</th><th>Used by</th><th>Refs</th></tr></thead>
      <tbody>${(stack.deps || []).map(depRow).join("") || `<tr><td colspan="5" class="muted">No dependencies found</td></tr>`}</tbody>
    </table></div>
    <h2>Not mapped to a dependency <span class="count">${unresolved.refs} refs</span></h2>
    <div class="chips">${chips(unresolved)}</div>
    <h2>Outside the repo <span class="count">${outside.refs} refs</span></h2>
    <p class="muted">Tool-host modules (bpy) and asset files: not warnings.</p>
    <div class="chips">${chips(outside)}</div>`;
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

function setView(view) {
  setOption("view", view);
}

// ---------- types strip: the code types in this view, each a toggle that hides its classes ----------

/** Reload after a filter changed (hidden types, file kinds, line kinds, Show switches): only the filtered
 *  classes come or go; the camera, open classes, selection and every other box stay. */
function reloadFiltered() {
  return loadView({ inPlace: true });
}

/** A list option (hidden types, file kinds, line kinds) with `item` switched: the new list. */
function toggled(list, item) {
  const hidden = new Set(list);
  if (!hidden.delete(item)) hidden.add(item);
  return [...hidden];
}

const toggleType = (type) => setOption("hiddenTypes", toggled(state.hiddenTypes, type));
const toggleFileKind = (kind) => setOption("hiddenFileKinds", toggled(state.hiddenFileKinds, kind));
const toggleTag = (tag) => setOption("hiddenTags", toggled(state.hiddenTags, tag));

// ---------- view options (view_options.js: the one table; URL > tour step > tour > remembered/preset > default) ----------

const VO = ViewOptions;
const viewOpts = {
  remembered: { preset: VO.DEFAULT_PRESET, changed: {} },  // this app's preset and the switches the user changed
  preset: null,  // ?preset= (this page only)
  url: {},       // the URL's options (this page only; a click on that switch drops it)
  tour: [],      // [tour values, step values] while a tour step is shown
};
const withFlag = (list, flag, on) => [...list.filter((f) => f !== flag), ...(on ? [flag] : [])];

/** Where each option lives in the page's state ([get, set]), and what to redraw when it changes. */
const OPTION_STATE = {
  view: [() => state.view, (v) => { state.view = v; }, "view"],
  external: [() => state.show.external, (v) => { state.show.external = v; }, "inPlace"],  // filters reload in place (reloadFiltered)
  comm: [() => state.show.comm, (v) => { state.show.comm = v; }, "inPlace"],
  tests: [() => state.show.test, (v) => { state.show.test = v; }, "inPlace"],
  cards: [() => state.show.cards, (v) => { state.show.cards = v; }, "inPlace"],
  planned: [() => state.show.planned, (v) => { state.show.planned = v; }, "inPlace"],
  track: [() => state.track, (v) => { state.track = v; }, "inPlace"],
  selfCalls: [() => !state.fanHidden.includes(SELF_CALLS), (v) => { state.fanHidden = v ? [] : [SELF_CALLS]; }, "fan"],
  domains: [() => state.domainView.includes("on"), (v) => { state.domainView = withFlag(state.domainView, "on", v); }, "domains"],
  between: [() => (state.domainView.includes("every") ? "every" : "bundled"), (v) => { state.domainView = withFlag(state.domainView, "every", v === "every"); }, "between"],
  filter: [() => state.filter, (v) => { state.filter = v; }, "filter"],
  layout: [() => state.layout, (v) => { state.layout = v; }, "layout"],
  mode: [() => state.mode, (v) => { state.mode = v; }, "mode"],
  boxText: [() => state.boxText, (v) => { state.boxText = v; }, "spots"],
  lines: [() => state.lines, (v) => { state.lines = v; }, "inPlace"],
  hiddenKinds: [() => state.hiddenKinds, (v) => { state.hiddenKinds = v; }, "inPlace"],
  hiddenTypes: [() => state.hiddenTypes, (v) => { state.hiddenTypes = v; }, "inPlace"],
  hiddenFileKinds: [() => state.hiddenFileKinds, (v) => { state.hiddenFileKinds = v; }, "inPlace"],
  hiddenTags: [() => state.hiddenTags, (v) => { state.hiddenTags = v; }, "inPlace"],
  onlyTags: [() => state.onlyTags, (v) => { state.onlyTags = v; }, "inPlace"],
  details: [() => (state.detailsOpen ? "open" : "closed"), (v) => { state.detailsOpen = v === "open"; }, "details"],
  overview: [() => state.overview !== CoderOverview.MAP, (v) => { state.overview = v ? "overview" : CoderOverview.MAP; }, "overview"],
};
/** The control each option sits in (the "set by" marks of More options). */
const OPTION_CONTROL = { view: "viewAll", external: "showExternal", comm: "showComm", tests: "showTest", cards: "showCards", planned: "planCtl", track: "trackCtl",
  selfCalls: "showSelfCalls", domains: "showDomains", between: "bundledBtn", filter: "filter", layout: "autoMethod", mode: "modeEdit",
  boxText: "boxTextCtl", lines: "linesCtl" };

function activePreset() {
  return viewOpts.preset || viewOpts.remembered.preset;
}

/** The user's own options: preset + remembered changes (no URL, no tour). */
function userOptions() {
  return VO.resolve(VO.presetValues(activePreset()), viewOpts.remembered.changed);
}

/** What the graph tab shows: the user's options under the URL's ("Use current graph settings"). */
function graphOptions() {
  return VO.resolve(userOptions(), viewOpts.url);
}

function effectiveOptions() {
  return VO.resolve(userOptions(), ...viewOpts.tour, viewOpts.url);
}

/** Put the effective options into the page's state and controls; redraw what changed (unless
 *  `redraw` is false: the caller draws). Returns the redraw kinds that changed. */
async function applyOptions({ redraw = true } = {}) {
  const next = effectiveOptions();
  const kinds = new Set();
  for (const [key, [get, set, kind]] of Object.entries(OPTION_STATE)) {
    if (JSON.stringify(get()) === JSON.stringify(next[key])) continue;
    set(next[key]);
    kinds.add(kind);
  }
  renderOptionControls();
  if (redraw) await redrawFor(kinds);
  return kinds;
}

async function redrawFor(kinds) {
  if (!cy || !state.current) return;
  if (kinds.has("mode")) setMode(state.mode);
  if (kinds.has("layout")) { updateAutoTitle(); autoLayout(); }
  if (kinds.has("details")) renderSelected();
  if (kinds.has("spots")) return reseedSpots();  // a new draw does the rest (frames, fan, filter, overview)
  if (kinds.has("view")) return loadView();
  if (kinds.has("inPlace")) return reloadFiltered();
  if (kinds.has("fan")) scheduleFan();
  if (kinds.has("filter")) applyFilter();
  if (kinds.has("domains")) domainsChanged();
  if (kinds.has("between")) betweenChanged();
  if (kinds.has("overview")) updateOverview();
}

/** A switch the user changed: remembered (relative to the preset; never for this-page-only ones). */
function setOption(key, value) {
  const opt = VO.option(key);
  const parsed = VO.parse(opt, value);
  if (parsed === undefined) return Promise.resolve();
  delete viewOpts.url[key];
  viewOpts.remembered = VO.remember({ ...viewOpts.remembered, preset: activePreset() }, key, parsed);
  viewOpts.preset = null;
  saveOptions();
  return applyOptions();
}

function saveOptions() {
  const changed = Object.fromEntries(Object.entries(viewOpts.remembered.changed).filter(([k]) => VO.option(k).scope !== "url"));
  VO.writeRemembered(localStorage, CoderApps.current, { preset: viewOpts.remembered.preset, changed });
}

/** A preset button: its values, no remembered changes; the URL's preset switches give way too. */
function choosePreset(name) {
  if (!VO.PRESETS[name]) return Promise.resolve();
  for (const key of VO.APP_KEYS) delete viewOpts.url[key];
  const keep = Object.fromEntries(Object.entries(viewOpts.remembered.changed).filter(([k]) => !VO.APP_KEYS.includes(k)));
  viewOpts.remembered = { preset: name, changed: keep };
  viewOpts.preset = null;
  saveOptions();
  return applyOptions();
}

/** A tour (and its step) shows with its own `view:` options; null = the user's own again.
 *  The caller draws. Returns the redraw kinds that changed. */
function useTourOptions(tourView, stepView) {
  viewOpts.tour = tourView || stepView ? [VO.fromStored(tourView, { tourOnly: true }), VO.fromStored(stepView, { tourOnly: true })] : [];
  return applyOptions({ redraw: false });
}

function loadOptions(params) {
  viewOpts.remembered = VO.readRemembered(localStorage, CoderApps.current);
  viewOpts.preset = VO.presetFromUrl(params);
  viewOpts.url = VO.fromUrl(params);
  delete viewOpts.url.level;  // the ruler's own (init clamps it)
}

/** Every option control shows the state; the preset buttons, Custom and the "set by" marks. */
function renderOptionControls() {
  setPressed("viewAll", state.view === "all");
  setPressed("viewConcrete", state.view === "concrete");
  $("showExternal").checked = state.show.external;
  $("showComm").checked = state.show.comm;
  $("showTest").checked = state.show.test;
  $("showCards").checked = state.show.cards;
  $("app").classList.toggle("editing", state.mode === "edit");
  setPressed("modeView", state.mode === "view");
  setPressed("modeEdit", state.mode === "edit");
  $("autoMethod").value = state.layout;
  if ($("filter").value !== state.filter && [...$("filter").options].some((o) => o.value === state.filter)) $("filter").value = state.filter;
  renderFaceControls();
  PlanView.renderControls();
  ProgressView.renderControls();
  renderDomainControls();
  renderFanControls();
  renderPresetControls();
}

function setPressed(id, on) {
  $(id).setAttribute("aria-pressed", String(on));
}

function renderPresetControls() {
  const preset = activePreset();
  const custom = !VO.isPreset(graphOptions(), preset);
  document.querySelectorAll("#presetCtl [data-preset]").forEach((b) => setPressed(b.id, !custom && b.dataset.preset === preset));
  $("presetCustom").hidden = !custom;
  $("presetNote").textContent = custom ? VO.CUSTOM_NOTE : VO.PRESETS[preset].note;
  $("presetNote").title = $("presetNote").textContent;
  const changed = VO.diff(graphOptions(), VO.resolve(VO.presetValues(preset)));
  const set = new Set(VO.presetSets(preset));
  document.querySelectorAll("#moreOptions .set-by").forEach((el) => el.remove());
  for (const [key, id] of Object.entries(OPTION_CONTROL)) {
    const mark = key in changed ? "changed" : set.has(key) ? VO.PRESETS[preset].label : "";
    if (!mark) continue;
    const at = $(id).closest("label.check") || $(id).closest(".ctl");
    const tag = document.createElement("span");
    tag.className = `set-by${key in changed ? " changed" : ""}`;
    tag.textContent = mark;
    tag.title = key in changed ? "You changed this switch" : `Set by the ${mark} preset`;
    at.append(tag);
  }
}

/** More options ▾: every other switch (open or closed remembered per browser; `?more=open`). */
const MORE_KEY = "coderstanding-more-options";
function showMore(open, save = true) {
  $("moreOptions").hidden = !open;
  $("moreBtn").setAttribute("aria-expanded", String(open));
  $("moreBtn").textContent = open ? "Fewer options ▴" : "More options ▾";
  if (save) try { localStorage.setItem(MORE_KEY, open ? "1" : "0"); } catch { /* private window */ }
  cy?.resize();  // before the next fit, which reads the new size
  requestAnimationFrame(() => { cy?.resize(); scheduleFan(); });
}

function savedMore(params) {
  if (params.has("more")) return params.get("more") === "open";
  try { return localStorage.getItem(MORE_KEY) === "1"; } catch { return false; }
}

// ---------- search box: dims the classes whose name does not match (with Highlight) ----------

function onSearch() {
  state.search = $("graphSearch").value.trim().toLowerCase();
  applyFilter();
}

function onSearchKey(event) {
  if (event.key === "Escape") { $("graphSearch").value = ""; onSearch(); return; }
  if (event.key !== "Enter" || !state.search) return;
  const hit = cy.nodes().filter((n) => matchesSearch(n.data("raw")))[0];
  if (hit) { select({ type: "node", raw: hit.data("raw") }); cy.animate({ center: { eles: hit } }, { duration: reducedMotion() ? 0 : 250 }); }
}

function matchesSearch(node) {
  if (!state.search) return true;
  return [node.name, node.id, nodeName(node.id)].some((text) => String(text || "").toLowerCase().includes(state.search));
}

/** Level 0 as the 30-second overview (overview.js) instead of the map: the view carries one, nothing
 *  is zoomed or focused, and the overview option (or ?overview=0) did not ask for the map. */
function updateOverview() {
  const view = state.current;
  const possible = Boolean(view?.overview) && view.level === 0 && !view.zoom && !state.focus && !state.domainFocus && !state.tourStep;
  const on = possible && state.overview !== CoderOverview.MAP;
  $("overview").hidden = !on;
  $("overviewBtn").hidden = !possible || on;
  if (on) CoderOverview.render(view.overview);
}

function chooseOverview(choice) {
  setOption("overview", choice === "overview");
}

/** Light the boxes of one type while its chip is hovered (`null` clears). */
function highlightType(type) {
  cy.nodes().removeClass("type-hit");
  if (type) cy.nodes().filter((n) => n.data("type") === type).addClass("type-hit");
}

/** The file kinds (Code, Data, ...) as outlined toggles ahead of the type chips; none when the view has one kind. */
function fileKindChips(view) {
  const hidden = new Set(view.hidden_file_kinds || []);
  const entries = Object.entries(view.file_kinds || {});
  if (entries.length < 2 && !hidden.size) return [];
  return entries.map(([kind, count]) => {
    const off = hidden.has(kind);
    const button = document.createElement("button");
    button.className = `kind-chip-btn${off ? " off" : ""}`;
    button.dataset.fileKind = kind;
    button.setAttribute("aria-pressed", String(!off));
    button.title = `${off ? "Hidden: click to show" : "Click to hide"} ${kind} files`;
    button.innerHTML = `<b>${esc(kind)}</b> ${count}`;
    return button;
  });
}

/** The tag notes (#chat) as toggles after the file kinds, like them; none when no shown class has a tag. */
function tagChips(view) {
  const hidden = new Set(view.hidden_tags || []);
  return Object.entries(view.tags || {}).map(([tag, count]) => {
    const off = hidden.has(tag);
    const button = document.createElement("button");
    button.className = `kind-chip-btn tag-chip-btn${off ? " off" : ""}`;
    button.dataset.tag = tag;
    button.setAttribute("aria-pressed", String(!off));
    button.title = `${off ? "Hidden: click to show" : "Click to hide"} classes tagged ${tag}`;
    button.innerHTML = `<b>#${esc(tag)}</b> ${count}`;
    return button;
  });
}

function renderTypeStrip(view) {
  const strip = $("typeStrip");
  const hidden = new Set(view.hidden_types || []);
  const theme = themeName();
  const entries = Object.entries(view.types || {});
  const kindChips = [...fileKindChips(view), ...tagChips(view)];
  strip.hidden = !entries.length && !kindChips.length;
  strip.replaceChildren(...kindChips, ...entries.map(([type, count]) => {
    const { bg, ink } = typeColors(type, theme);
    const off = hidden.has(type);
    const button = document.createElement("button");
    button.className = `type-chip-btn${off ? " off" : ""}`;
    button.dataset.type = type;
    button.setAttribute("aria-pressed", String(!off));
    button.title = `${off ? "Hidden: click to show" : "Click to hide"} ${type} classes`;
    button.style.setProperty("--chip-bg", bg);  // CSSOM, not a style attribute (the page's CSP)
    button.style.setProperty("--chip-ink", ink);
    button.innerHTML = `<b>${esc(type)}</b> ${count}`;
    return button;
  }));
}

/** Light the lines of one kind and fade the rest (`null` clears); "derived" = lines through hidden classes. */
function litKind(kind) {
  state.litKind = kind;
  const has = (e) => (kind === "derived" ? !e.data("raw").direct : e.data("raw").kinds.includes(kind));
  cy.edges().removeClass("kind-hit kind-dim");
  if (kind) {
    cy.edges().filter(has).addClass("kind-hit");
    cy.edges().filter((e) => !has(e)).addClass("kind-dim");
  }
}

/** The legend lists the line kinds in the view plus the hidden ones (plain words first, FieldHelp). */
function renderLegend(view) {
  const hidden = view.hidden_kinds || [];
  const present = new Set(view.edges.flatMap((e) => [...e.kinds, ...(e.direct ? [] : ["derived"])]));
  const entries = FieldHelp.legendEntries([...present], hidden);
  if (!entries.some((e) => e.kind === state.litKind)) state.litKind = null;
  $("legendKinds").innerHTML = entries.map((e) => `<span class="legend-entry${e.hidden ? " off" : ""}">`
    + `<button type="button" class="legend-kind" data-kind="${esc(e.kind)}" aria-pressed="${e.hidden ? "false" : String(state.litKind === e.kind)}" title="${esc(e.hidden ? "Hidden: click to show these lines" : e.tip || e.word)}">`
    + `<i class="lg lg-${esc(e.kind)}"></i>${esc(e.word)}${e.uml ? ` <small class="uml">${esc(e.uml)}</small>` : ""}</button>`
    + (e.kind === "derived" ? "" : `<button type="button" class="legend-hide" data-hide-kind="${esc(e.kind)}" aria-label="${e.hidden ? "Show" : "Hide"} ${esc(e.word)} lines" title="${e.hidden ? "Show" : "Hide"} these lines">${e.hidden ? "show" : "hide"}</button>`)
    + `</span>`).join("");
  litKind(state.litKind);
}

/** A legend kind's hide/show control: the `hiddenKinds` view option. */
function toggleKind(kind) {
  setOption("hiddenKinds", toggled(state.hiddenKinds, kind));
}

function bindLegend() {
  const legend = document.querySelector(".legend");
  legend.addEventListener("click", (event) => {
    const hide = event.target.closest("[data-hide-kind]");
    if (hide) return toggleKind(hide.dataset.hideKind);
    const entry = event.target.closest("[data-kind]");
    if (!entry) return;
    if (entry.closest(".off")) return toggleKind(entry.dataset.kind);  // nothing to light: bring the lines back
    litKind(state.litKind === entry.dataset.kind ? null : entry.dataset.kind);
    renderLegend(state.current);
  });
}

/** One sentence about a line next to the pointer (hover, or tap on a phone): "A uses B: hint". */
function showLineTip(event) {
  const raw = event.target.data("raw");
  const at = event.renderedPosition, box = cy.container().getBoundingClientRect();
  FieldHelp.showTip(FieldHelp.lineTipHtml({ from: nodeName(raw.from), to: nodeName(raw.to),
    kind: lineKind(raw), hint: raw.hint, calls: callSummary(raw.calls) }), box.left + at.x, box.top + at.y);
}

function bindTypeStrip() {
  const strip = $("typeStrip");
  strip.addEventListener("click", (event) => {
    const kindButton = event.target.closest("[data-file-kind]");
    if (kindButton) return toggleFileKind(kindButton.dataset.fileKind);
    const tagButton = event.target.closest("[data-tag]");
    if (tagButton) return toggleTag(tagButton.dataset.tag);
    const button = event.target.closest("[data-type]");
    if (button) toggleType(button.dataset.type);
  });
  strip.addEventListener("mouseover", (event) => highlightType(event.target.closest("[data-type]")?.dataset.type || null));
  strip.addEventListener("mouseleave", () => highlightType(null));
}

// ---------- status / tag filter (dims classes that do not match; the engine is not asked) ----------

function matchesFilter(node) {
  if (!matchesSearch(node)) return false;
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
  setOption("filter", $("filter").value);
}

function onKey(event) {
  onRulerKey(event);
  onSelectionKey(event);
  if (state.mode !== "edit" || !(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "z") return;
  event.preventDefault();
  if (event.shiftKey) redo(); else undo();
}

/** Open or close the specs sidebar (no argument: toggle). */
function toggleSide(force) {
  const open = typeof force === "boolean" ? force : !$("app").classList.contains("side-open");
  $("app").classList.toggle("side-open", open);
  $("sideBody").hidden = !open;
  $("sideToggle").textContent = open ? "‹" : "›";
  $("sideToggle").setAttribute("aria-expanded", String(open));
  requestAnimationFrame(() => cy.resize());
}

function bindControls() {
  bindTypeStrip();
  bindLegend();
  PlanView.bind();
  ProgressView.bind();
  bindDomains();
  $("rulerCols").addEventListener("click", (event) => {
    const column = event.target.closest("[data-level]");
    if (column && !column.disabled) setLevel(Number(column.dataset.level));
  });
  $("levelsBtn").addEventListener("click", openLevels);
  StartHere.bind();
  $("levelsCount").addEventListener("input", () => {
    const count = Math.max(1, Math.min(12, Number($("levelsCount").value) || 1));
    const names = currentNameInputs();
    fillLevelNames(Array.from({ length: count + 1 }, (_, i) => names[i] || `Level ${i}`));
  });
  $("levelsForm").addEventListener("submit", saveLevels);
  $("levelsReset").addEventListener("click", (event) => saveLevels(event, true));
  $("levelsCancel").addEventListener("click", () => $("levelsDialog").close());
  $("viewAll").addEventListener("click", () => setView("all"));
  $("viewConcrete").addEventListener("click", () => setView("concrete"));
  for (const [id, key] of [["showExternal", "external"], ["showComm", "comm"], ["showTest", "tests"], ["showCards", "cards"]]) {
    $(id).addEventListener("change", (event) => setOption(key, event.target.checked));
  }
  $("presetCtl").addEventListener("click", (event) => choosePreset(event.target.closest("[data-preset]")?.dataset.preset));
  $("moreBtn").addEventListener("click", () => showMore($("moreOptions").hidden));
  $("graphSearch").addEventListener("input", onSearch);
  $("graphSearch").addEventListener("keydown", onSearchKey);
  CoderOverview.bind(() => chooseOverview(CoderOverview.MAP), (request) => askAi(request, flashNote, showCopyBox));
  $("overviewBtn").addEventListener("click", () => chooseOverview("overview"));
  $("boxTextCtl").addEventListener("click", (event) => setBoxText(event.target.closest("[data-boxtext]")?.dataset.boxtext));
  $("linesCtl").addEventListener("click", (event) => setLines(event.target.closest("[data-lines]")?.dataset.lines));
  $("filter").addEventListener("change", onFilter);
  $("modeView").addEventListener("click", () => setOption("mode", "view"));
  $("modeEdit").addEventListener("click", () => setOption("mode", "edit"));
  $("autoBtn").addEventListener("click", autoLayout);
  $("autoMethod").addEventListener("change", onMethod);
  $("undoBtn").addEventListener("click", undo);
  $("redoBtn").addEventListener("click", redo);
  $("themeBtn").addEventListener("click", cycleTheme);
  $("sideToggle").addEventListener("click", () => toggleSide());
  $("tabGraphBtn").addEventListener("click", () => showTab("graph"));
  $("tabStackBtn").addEventListener("click", () => showTab("stack"));
  $("tabToursBtn").addEventListener("click", () => showTab("tours"));
  $("details").addEventListener("click", onDetailsClick);
  $("details").addEventListener("keydown", onNoteKey);
  $("details").addEventListener("input", onNoteInput);
  $("details").addEventListener("focusout", onNoteBlur);
  $("details").addEventListener("change", onNoteChange);
  $("details").addEventListener("toggle", onFoldToggle, true);  // toggle does not bubble
  $("crumbs").addEventListener("click", (event) => {
    const index = event.target.dataset?.crumb;
    if (index === undefined) return;
    if (index === "domain-out") leaveDomainFocus();
    else if (index === "-1") { state.crumbs = []; state.selected = null; loadView(); } else goToCrumb(Number(index));
  });
  document.addEventListener("keydown", onKey);
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => cy.style(graphStyle()));
}

function select(item) {
  cy.elements(".sel").removeClass("sel");
  const before = selectedNodeId();
  state.selected = item;
  if (item?.type !== "fn" && state.activeFn) { state.activeFn = null; scheduleFan(); }
  else if (selectedNodeId() !== before) scheduleFan();  // the selected box moves to the top layer
  updateAutoTitle();
  if (item && item.type !== "fn") cy.getElementById(item.type === "node" ? item.raw.id : `e:${item.raw.key}`).addClass("sel");
  lightNear();
  applyFocus();
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
  cy.on("mouseover", "edge", showLineTip);
  cy.on("mouseout tapstart pan zoom", () => FieldHelp.hideTip());
  cy.on("tap", "edge", showLineTip);
  cy.on("mouseover", "node", (event) => lightNear(event.target.id()));
  cy.on("mouseout", "node", () => lightNear());
  cy.on("mouseover", "node, edge", (event) => {
    event.target.addClass("hover");
    previewDetails({ type: event.target.isNode() ? "node" : "edge", raw: event.target.data("raw") });
  });
  cy.on("mouseout", "node, edge", (event) => {
    event.target.removeClass("hover");
    endPreview();
  });
  cy.on("tap", "node", (event) => select({ type: "node", raw: event.target.data("raw") }));
  cy.on("tap", "edge", (event) => select({ type: "edge", raw: event.target.data("raw") }));
  cy.on("tap", (event) => { if (event.target === cy) select(null); });
  cy.on("dbltap", "node", (event) => {
    const raw = event.target.data("raw");
    if (raw.kind === "domain") enterDomainFocus(raw.name);  // a domain card: just that domain
    else if (!raw.faded) zoomIntoNode(raw);
  });
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

/** Ask AI, offline: copy a request for the user's AI chat to the clipboard (never calls an AI);
 *  `show(message)` tells the user, `blocked(request)` shows the request when copying is refused.
 *  The one helper for every Ask AI button (box faces, tour steps, Start here). */
async function askAi(request, show, blocked = show) {
  try {
    await navigator.clipboard.writeText(request);
    show("Copied a request for the AI: paste it in your AI chat");
  } catch { blocked(request); }
}

// ---------- Copy text | Copy location: the item's own text, or a location ref (refs.js) as text + link ----------

/** The copy buttons for a class (`Cls`) or function (`Cls#fn`) target; `text` = its description. */
function refButtons(target, text = "") {
  return CoderRefs.copyButtons(CoderRefs.formatRef(CoderApps.current, target), text);
}

/** The ref lines' clipboard contents (text + HTML link), for this app on this page. */
function refParts(refs) {
  return CoderRefs.clipboardParts(CoderApps.current, refs, location.origin + location.pathname);
}

/** Put `{text, html?}` on the clipboard: both flavours when there is HTML (rich editors paste the
 *  link, chats the text), plain text otherwise. The one clipboard path of Copy text / Copy location. */
async function writeClipboard({ text, html }, done, blocked = showCopyBox) {
  try {
    if (!html) throw new Error("plain");
    const blob = (type, body) => new Blob([body], { type });
    await navigator.clipboard.write([new ClipboardItem({ "text/plain": blob("text/plain", text), "text/html": blob("text/html", html) })]);
    done();
    return;
  } catch { /* plain text, no ClipboardItem or refused: plain text next */ }
  try {
    await navigator.clipboard.writeText(text);
    done();
  } catch { blocked(text); }
}

/** Copy location ref lines. `show(message)` tells the user. */
function copyRefs(refs, show = flashNote, blocked = showCopyBox) {
  return writeClipboard(refParts(refs), () => show(refs.length > 1 ? "Copied locations" : "Copied location"), blocked);
}

/** Copy an item's own text, no location. */
function copyPlain(text, show = flashNote, blocked = showCopyBox) {
  return writeClipboard({ text }, () => show("Copied text"), blocked);
}

/** A copy button (refs.js `copyButtons`) was clicked anywhere: copy and say so on the button. */
function onCopyButtonClick(event) {
  const button = event.target.closest("[data-copy-ref], [data-copy-text]");
  if (!button) return;
  const shown = button.innerHTML;  // icon (or icon + label): put back after the message
  const show = (message) => {
    button.textContent = message;
    setTimeout(() => { button.innerHTML = shown; }, 1500);
  };
  if (button.dataset.copyRef != null) copyRefs([button.dataset.copyRef], show);
  else copyPlain(button.dataset.copyText, show);
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
  document.addEventListener("click", onCopyButtonClick);
  for (const id of ["details", "specTree", "tourPanel"]) CopyMenu.bind($(id));
}

// ---------- ?ref= links: /api/ref says where a ref points; its `view` becomes the usual URL options ----------

/** The banner under the header: why a pasted ref found nothing, or what lines a ref points at. */
function showRefNote(html) {
  $("refNoteText").innerHTML = html;
  $("refNote").hidden = false;
}

function showRefMiss(ref, error) {
  const names = error.data?.did_you_mean || [];
  showRefNote(esc(`No match for ${ref}: ${error.message}${names.length ? `. Did you mean: ${names.join(", ")}?` : ""}`));
}

/** After a `?ref=` opened: the answer's warning, and for `?file=<path>&hl=A-B` (lines or a doc
 *  section, no code pane) which lines, with the source links. */
function showRefAnswer(found, params) {
  const file = params.get("file"), hl = params.get("hl");
  const where = file ? `Referenced: <code>${esc(file)}${hl ? `:${esc(hl)}` : ""}</code> ${sourceLinks(file, Number(hl?.split("-")[0]) || 0)}` : "";
  if (found?.warning || where) showRefNote(`${found?.warning ? `${esc(found.warning)} ` : ""}${where}`);
}

/** Resolve `?ref=` (once, at start): rewrite the address to the view options the answer names
 *  (tab, tour, step, select, specs, spec, ...) so the existing URL-option code opens it.
 *  Returns the answer, or null (no ref, or a miss: the banner says so). */
async function openRefFromUrl() {
  const params = new URLSearchParams(location.search);
  const ref = params.get("ref");
  if (!ref) return null;
  params.delete("ref");
  let found = null;
  try {
    found = await api(`/api/ref?ref=${encodeURIComponent(ref)}`);
    for (const [key, value] of Object.entries(found.view || {})) {
      params.delete(key);
      [].concat(value).forEach((one) => params.append(key, one === true ? "1" : String(one)));
    }
  } catch (error) {
    showRefMiss(ref, error);
  }
  history.replaceState(null, "", `${location.pathname}?${params}${location.hash}`);
  return found;
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
  state.level = VO.fromUrl(params).level ?? VO.option("level").def;  // init clamps it to the app's levels
  loadOptions(params);  // every view switch: URL > remembered/preset > default (not saved from the URL)
  showMore(savedMore(params), false);
  if (params.get("domain")) state.domainFocus = { name: params.get("domain"), back: null };
  params.getAll("expand").forEach((id) => state.expanded.add(id));
  return params;
}

// ---------- switching apps: what an app keeps while another is shown ----------

CoderApps.getState = () => ({ level: state.level, view: state.view, crumbs: state.crumbs,
  camera: cy ? { zoom: cy.zoom(), pan: cy.pan() } : null });

function restoreAppState(back) {
  state.level = Math.min(back.level, state.meta.max_level);
  state.crumbs = back.crumbs || [];
}

async function init() {
  cy = cytoscape({ container: $("cy"), style: graphStyle(), minZoom: 0.05, maxZoom: 3,
    boxSelectionEnabled: false, autoungrabify: false });
  bindControls();
  bindGraph();
  bindFan();
  bindCopy();
  $("refNoteClose").addEventListener("click", () => { $("refNote").hidden = true; });
  bindPanels(() => requestAnimationFrame(() => { cy.resize(); scheduleFan(); }));
  renderHelp();
  showOverlay("Loading…");
  CoderApps.onLost = (html) => showOverlay(html, true);  // the helper went away: how to start it
  try {
    if (!await CoderApps.start()) {
      showOverlay(CoderApps.emptyHtml(), true);
      return;
    }
    const foundRef = await openRefFromUrl();
    const [meta, layout, fieldRules] = await Promise.all([api("/api/meta"), api("/api/layout"), api("/api/fields")]);
    FieldHelp.setRules(fieldRules);
    FieldHelp.watchWords(["#details", ".controls", "#ruler"]);
    state.meta = meta;
    layout.items.forEach((item) => state.saved.set(item.key, item));
    document.title = `${meta.repo} · Coderstanding`;
    $("repoName").textContent = meta.repo;
    $("scanned").textContent = `scanned ${meta.scannedAt}`;
    state.level = Math.max(meta.domains ? 0 : 1, Math.min(state.level, meta.max_level));
    const params = applyUrlOptions();
    await applyOptions({ redraw: false });
    if (state.domainFocus) state.level = Math.max(1, state.level);
    const back = params.get("level") ? null : CoderApps.takeState();  // returning to an app
    if (back) restoreAppState(back);
    fillFilter(meta);
    await seedAutoSpots();
    await loadView();
    if (back?.camera) { cy.zoom(back.camera.zoom); cy.pan(back.camera.pan); }
    await SpecView.start(params);
    if (params.get("tab") === "stack") showTab("stack");
    if (params.get("tab") === "tours") await showTab("tours");
    else selectFromUrl(params);
    showRefAnswer(foundRef, params);
  } catch (error) {
    showOverlay(esc(`Could not reach the server: ${error.message}`), true);
  }
}

init();
