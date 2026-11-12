// Function fan-out (DESIGN 12c): open classes show their functions, and the engine's fan-out
// links are drawn from and to those functions. Draws what /api/view returns ("expanded",
// "fanout"); no graph logic here. Loaded before app.js and uses its state, cy and helpers.
// Also draws the face of every class box (header band, toggle triangle, badges, warning mark)
// the way the approved mock-up does (docs/DESIGN.md 12c).
"use strict";

// Sizes from the mock-up: header 46, function row 24, field row 18.
const FAN = { head: 46, row: 24, fieldRow: 18, rowLine: 15, fieldLine: 14, groupRow: 26, pad: 8, maxWidth: 380, labelMax: 46,
  junction: 56, visibleRows: 20, visibleFields: 12, filterAt: 20, filterRow: 28, wheelRows: 3,
  bundleMin: 4,     // this many lines between one pair of classes share a trunk
  curve: 50, bulge: 34 };
const FACE = { left: 28, right: 14, badge: 7, name: "600 13.5px", sub: "11px", nameLine: 17, subLine: 14,
  wrapAt: 320,   // a box grows to fit its name up to this width, then the name wraps (never cut off)
  chip: "700 10px", chipPad: 10, chipGap: 6 };
// Box text (the toolbar's Box text switch, saved per app, `boxtext=` in the URL): "name" = the
// name and counts only; "short" = plus what it does, 2 lines at most; "full" = all of it.
const BOX_TEXTS = ["name", "short", "full"];
const DEFAULT_BOX_TEXT = "short";
// The described face (short/full): header band, description, quiet footer with importance dots.
const DESC = { head: 32, font: "12.5px", line: 16, padTop: 5, left: 12, shortLines: 2, minW: 210,
  footLine: 14, footPad: 10, dots: 30, ask: "Ask AI", none: "No description yet" };
const IMPORTANCE = [3, 6];  // used by this many classes or more: 2, 3 dots (a hub always 3)
const USED_BY_BADGE = 6;    // used by this many classes or more: a "used by N" badge (hubs have their own)
const SELF_CALLS = "self";  // in state.fanHidden: the self-call lines are switched off
const EXPAND_CAP = 30;  // "Expand all" opens at most this many classes
const NOT_EXPANDABLE = ["external", "domain"];
const MESSAGE_TYPE = "rpc";  // comm type of a message node (rpc_messages.RPC_TYPE)
// Who sends a message (`direction` on a remote-callable function row): shown after its name.
const DIRECTION_MARKS = { "client→server": "c→s", "server→client": "s→c", any: "⇄" };
const SVG_NS = "http://www.w3.org/2000/svg";
const MARKER_TONES = ["accent", "muted", "derived", "faded", "hl", "dim", "signal"];
const TRIANGLE = "M-4.5,-6.5 L7,0 L-4.5,6.5 Z";  // the expand toggle (13px): points right, turns down when open
// Start badges: the real mechanism that runs a class first (scanner flags; `entry` is generic).
const START_BADGES = { "godot-main": "Godot main", "godot-main-scene": "Godot main scene",
  autoload: "Godot autoload", "program-main": "Program main()", "script-main": "Script __main__", entry: "Entry point" };
const BADGE_LOOK = { autoload: "soft", "script-main": "quiet", entry: "quiet" };
const HUB_FLAG = "hub";  // promoted to level 1 for its many connections (`hub_degree`)
// Godot engine callbacks: entry points, so they stay with the listed functions though named `_x`.
const GODOT_CALLBACKS = new Set(["_ready", "_process", "_physics_process", "_input", "_unhandled_input",
  "_unhandled_key_input", "_gui_input", "_notification", "_init", "_enter_tree", "_exit_tree", "_draw",
  "_integrate_forces", "_get_configuration_warnings"]);

function svgEl(tag, attrs = {}, parent = null) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attrs)) el.setAttribute(name, value);
  if (parent) parent.appendChild(el);
  return el;
}

/** The toggle triangle as inline HTML, for hint texts. */
function triangleIcon() {
  return `<svg class="tri-icon" viewBox="-7 -7 15 14" aria-hidden="true"><path d="${TRIANGLE}"/></svg>`;
}

/** A message node (network messages handled by one class): an outside box that opens into its messages. */
function isMessageNode(node) {
  return node.comm?.type === MESSAGE_TYPE;
}

function isExpandable(node) {
  return (!NOT_EXPANDABLE.includes(node.kind) || isMessageNode(node)) && !node.faded;
}

function openParts(id) {
  return state.current?.expanded?.[id] || null;
}

/** A node's start badges [{flag, text, look}]; the generic `entry` only when nothing names how. */
function startBadges(node) {
  const named = node.flags.filter((f) => f !== "entry" && START_BADGES[f]);
  const flags = named.length ? named : node.flags.filter((f) => f === "entry");
  return flags.map((flag) => ({ flag, text: START_BADGES[flag], look: BADGE_LOOK[flag] || "strong" }));
}

/** Every badge on a box: how it starts, then "Hub · N connections" (informative, not a warning). */
function boxBadges(node) {
  const badges = startBadges(node);
  badges.unshift(...PlanView.badges(node));  // "Planned" / "Added manually" first
  if (node.flags.includes(HUB_FLAG)) {
    const n = node.hub_degree;
    badges.push({ flag: HUB_FLAG, text: n ? `Hub · ${n} connections` : "Hub", look: "hub" });
  }
  return badges;
}

/** The described face's header badge: "used by N" for a class many on the map use (a hub has
 *  its own badge), else "". */
function usedByBadge(node) {
  const used = node.used_by || 0;
  return !node.flags?.includes(HUB_FLAG) && used >= USED_BY_BADGE ? `used by ${used}` : "";
}

function usedBadgeWidth(text) {
  return text ? textWidth(text, `600 ${bodyFont("10.5px")}`) + 12 : 0;
}

/** The Box text setting in force ("name" | "short" | "full"). */
function boxTextMode() {
  return BOX_TEXTS.includes(state.boxText) ? state.boxText : DEFAULT_BOX_TEXT;
}

/** What a class or domain says it does, from the view engine's notes (human > AI > code comment):
 *  the one-line hint first, else the description; `full` prefers the longer description. */
function whatItDoes(item, full = false) {
  const text = full ? item?.description || item?.hint : item?.hint || item?.description;
  return text ? String(text).replace(/\s+/g, " ").trim() : "";
}

/** How much a class matters, 1-3 dots: by how many classes use it; a hub always 3. */
function importance(node) {
  if (node.flags?.includes(HUB_FLAG)) return 3;
  const used = node.used_by || 0;
  return 1 + IMPORTANCE.filter((n) => used >= n).length;
}

// ---------- the lists in an open box: functions, then folded groups (private, fields) ----------

/** Private by naming convention (`_name` in GDScript and Python). Godot engine callbacks and
 * Python `__dunder__` methods are entry points, not private. */
function isPrivateName(name) {
  return name.startsWith("_") && !GODOT_CALLBACKS.has(name) && !/^__.+__$/.test(name);
}

/** An open class's members as its box shows them: the functions listed, then the groups folded
 * into one row each ("▸ 4 private", "▸ 12 fields"); empty groups are left out. In a tour step
 * (`state.tourStep`) a box lists only the step's functions, the others fold into "▸ N more". */
function memberGroups(parts, id) {
  const only = state?.tourStep ? stepFunctions().get(id) : null;
  const listed = (f) => (only ? only.has(f.name) : !isPrivateName(f.name) || Boolean(f.direction));
  const groups = [
    only ? { kind: "more", fns: true, items: parts.functions.filter((f) => !listed(f)) }
      : { kind: "private", fns: true, items: parts.functions.filter((f) => !listed(f)) },
    { kind: "property", fns: false, items: parts.properties },
  ];
  return { functions: parts.functions.filter(listed), groups: groups.filter((g) => g.items.length) };
}

const stepFnCache = new WeakMap();

/** A tour step's functions per open class (DESIGN 7c): its focused functions and the other ends
 *  of their lines, as class id -> Set of names, for the view on screen. */
function stepFunctions(view = state.current) {
  if (!view) return new Map();
  if (stepFnCache.has(view)) return stepFnCache.get(view);
  const focused = new Set(view.focus?.functions || []);
  const only = new Map();
  const add = (cls, fn) => {
    if (!fn || !view.expanded?.[cls]) return;
    if (!only.has(cls)) only.set(cls, new Set());
    only.get(cls).add(fn);
  };
  focused.forEach((id) => add(id.slice(0, id.lastIndexOf("#")), id.slice(id.lastIndexOf("#") + 1)));
  for (const link of view.fanout || []) {
    if (focused.has(`${link.from}#${link.from_fn}`) || focused.has(`${link.to}#${link.to_fn}`)) {
      add(link.from, link.from_fn);
      add(link.to, link.to_fn);
    }
  }
  stepFnCache.set(view, only);
  return only;
}

/** A long function list gets a filter box; a tour step's trimmed box needs none. */
function hasFilter(id) {
  return !(state?.tourStep && stepFunctions().has(id)) && openParts(id).functions.length > FAN.filterAt;
}

function fnFilter(id) {
  return (state.fnFilter.get(id) || "").trim().toLowerCase();
}

/** The members an open class shows: its filter narrows the functions and the private group
 * (totals stay, so the box keeps its size while typing). */
function shownParts(id) {
  const parts = openParts(id);
  if (!parts) return null;
  const filter = fnFilter(id);
  const match = (f) => !filter || [f.name, f.nickname].some((name) => name?.toLowerCase().includes(filter));
  const { functions, groups } = memberGroups(parts, id);
  return { functions: functions.filter(match), total: functions.length, filtered: Boolean(filter),
    groups: groups.map((g) => ({ ...g, total: g.items.length, names: new Set(g.items.map((f) => f.name)),
      items: g.fns ? g.items.filter(match) : g.items })) };
}

function groupOpen(key) {
  return state.openGroups.has(key);
}

/** Rows a list shows at once: all, or `cap` with the rest scrolled. */
function listRows(total, cap) {
  return Math.min(total, cap);
}

function scrollOf(key, items, cap) {
  const max = Math.max(0, items - cap);
  const at = Math.min(max, Math.max(0, state.listScroll.get(key) || 0));
  return at;
}

function listAt(key, items, top, row, rows) {
  return { key, items, top, row, rows, offset: scrollOf(key, items.length, rows) };
}

/**
 * The rows of an open box top to bottom from `top` (the end of its header): the function list,
 * then each group's row and, when open, its list. Lists scroll past their cap. `height` = all of it.
 * `rows` = {fnRow, fieldRow}.
 */
function sectionsOf(id, rows, top = 0) {
  const parts = shownParts(id);
  const fnRows = listRows(parts.total, FAN.visibleRows);
  const fn = listAt(`${id}#fn`, parts.functions, top, rows.fnRow, fnRows);
  let y = top + fnRows * rows.fnRow;
  const groups = parts.groups.map((g) => {
    const key = `${id}#${g.kind}`;
    const open = groupOpen(key);
    const row = g.fns ? rows.fnRow : rows.fieldRow;
    const shown = open ? listRows(g.total, g.fns ? FAN.visibleRows : FAN.visibleFields) : 0;
    const group = { ...g, key, open, top: y, filtered: parts.filtered, list: open ? listAt(key, g.items, y + FAN.groupRow, row, shown) : null };
    y += FAN.groupRow + shown * row;
    return group;
  });
  return { fn, groups, height: y - top + FAN.pad };
}

/** sectionsOf for a drawn box, in graph coordinates. */
function sectionsAt(box) {
  return sectionsOf(box.id, box, box.y1 + box.head);
}

// ---------- the class box: face drawn here, Cytoscape draws the frame ----------

/** A function row's text: `name()`, a message `name c→s`; a nickname first, the real name after it. */
function fnText(f) {
  const real = f.nickname ? ` · ${f.name}` : "";
  const name = f.nickname || f.name;
  return (f.direction ? `${name} ${DIRECTION_MARKS[f.direction] || f.direction}${real}` : `${name}()${real}`) + PlanView.fnMark(f);
}

function fieldText(f) {
  if (f.kind === "signal") return `signal ${f.name}`;
  return f.type ? `${f.name}: ${f.type}` : f.name;
}

function plural(n, word, many = `${word}s`) {
  return `${n} ${n === 1 ? word : many}`;
}

/** Subtitle under the name: "N functions · M properties", then marks (status, tags, flags). */
function faceSub(node, marks) {
  if (node.made) return [PlanView.footText(node), marks].filter(Boolean).join(" · ");
  const counts = node.functions !== undefined || node.properties !== undefined
    ? `${plural(node.functions || 0, "function")} · ${plural(node.properties || 0, "property", "properties")}` : "";
  return [counts, marks].filter(Boolean).join(" · ");
}

function monoFont(size) {
  return `${size} ${token("f-mono")}`;
}

function bodyFont(size) {
  return `${size} ${token("f-body")}`;
}

function chipWidth(type) {
  return type ? textWidth(type, monoFont(FACE.chip)) + FACE.chipPad : 0;
}

/** The face's text: name and sub-line on as many lines as they need, the type chip, header height
 *  (Box text "name": the face is only its header; `faceH` = header height). */
function faceLayout(node, base, sub) {
  const nameFont = monoFont(FACE.name);
  const subFont = monoFont(FACE.sub);
  const type = fileType(node.file);
  const chipW = chipWidth(type);
  const subGap = chipW ? chipW + FACE.chipGap : 0;
  const textMax = FACE.wrapAt - FACE.left - FACE.right;
  const nameLines = wrapName(base.name, (t) => textWidth(t, nameFont), textMax);
  const subLines = sub ? wrapSub(sub, (t) => textWidth(t, subFont), textMax - subGap) : [];
  const widest = Math.max(...nameLines.map((t) => textWidth(t, nameFont)),
    ...subLines.map((t) => textWidth(t, subFont) + subGap), chipW);
  const subRows = Math.max(subLines.length, type ? 1 : 0);
  const headH = FAN.head + (nameLines.length - 1) * FACE.nameLine + Math.max(0, subRows - 1) * FACE.subLine;
  return { type, chipW, nameLines, subLines, headH, faceH: headH, w: widest + FACE.left + FACE.right };
}

/** The quiet footer's text: functions (and fields when it has any), marks (status, tags, flags),
 *  and in "full" the domain. */
function footText(node, marks, mode) {
  if (node.made) return [PlanView.footText(node), marks].filter(Boolean).join(" · ");  // a planned box has no code to count
  const counts = node.functions !== undefined ? [plural(node.functions || 0, "function"), node.properties && plural(node.properties, "property", "properties")] : [];
  return [...counts, marks, mode === "full" && node.domain].filter(Boolean).join(" · ");
}

/**
 * The described face (Box text "short" / "full"): name lines with the type chip after the last
 * one (its own row when it does not fit), what the class does (2 lines in "short"), or "No
 * description yet" with Ask AI, then the footer. The box grows to fit, up to FACE.wrapAt wide.
 */
function describedLayout(node, base, mode) {
  const nameFont = monoFont(FACE.name), descFont = bodyFont(DESC.font), footFont = monoFont(FACE.sub);
  const width = (font) => (t) => textWidth(t, font);
  const type = fileType(node.file);
  const chipW = chipWidth(type);
  const badge = usedByBadge(node);
  const badgeW = badge ? usedBadgeWidth(badge) + FACE.chipGap : 0;  // on the right of the first name line
  const textMax = FACE.wrapAt - FACE.left - FACE.right - badgeW;
  const nameLines = wrapName(base.name, width(nameFont), textMax);
  const lastW = textWidth(nameLines.at(-1), nameFont);
  const chipInline = !chipW || lastW + FACE.chipGap + chipW <= textMax;
  const nameW = Math.max(...nameLines.map(width(nameFont)), chipInline ? lastW + (chipW && FACE.chipGap + chipW) : chipW);
  const desc = whatItDoes(node, mode === "full");
  const foot = footText(node, base.marks, mode);
  const descSide = DESC.left + FACE.right;
  const wanted = Math.max(DESC.minW, (desc ? textWidth(desc, descFont) : askWidth()) + descSide,
    textWidth(foot, footFont) + DESC.dots + descSide);
  const w = Math.max(nameW + badgeW + FACE.left + FACE.right, Math.min(FACE.wrapAt, wanted));
  const wrapped = desc ? wrapText(desc, width(descFont), w - descSide) : [];
  const descLines = mode === "short" ? clampLines(wrapped, DESC.shortLines, width(descFont), w - descSide) : wrapped;
  const footLines = foot ? wrapSub(foot, width(footFont), w - descSide - DESC.dots) : [""];
  const headH = DESC.head + (nameLines.length - 1) * FACE.nameLine + (chipInline ? 0 : FACE.subLine + 2);
  const descH = DESC.padTop + Math.max(1, descLines.length) * DESC.line;
  const faceH = headH + descH + footLines.length * DESC.footLine + DESC.footPad;
  return { type, chipW, chipInline, badge, nameLines, subLines: [], descLines, footLines, headH, faceH, w };
}

/** Width of "No description yet" plus its Ask AI button. */
function askWidth() {
  return textWidth(DESC.none, `italic ${bodyFont(DESC.font)}`) + 8 + askButtonWidth();
}

function askButtonWidth() {
  return textWidth(DESC.ask, `600 ${bodyFont("11px")}`) + 14;
}

/** Width of the open rows: the longest function or field. */
function rowsWidth(id) {
  const parts = openParts(id);
  return Math.max(0, ...parts.functions.map((f) => textWidth(fnText(f), monoFont("12.5px")) + 36),
    ...parts.properties.map((f) => textWidth(fieldText(f), monoFont("11.5px")) + 32));
}

/**
 * The open rows wrapped to the box width. Every row of a list is as tall as its longest wrapped
 * row (rows stay uniform so scrolling and line anchors keep their arithmetic).
 */
function rowLayout(id, boxW) {
  const parts = openParts(id);
  const fnFont = monoFont("12.5px");
  const fldFont = monoFont("11.5px");
  const wrap = (text, font, width) => wrapName(text, (t) => textWidth(t, font), width);
  const fnLines = Object.fromEntries(parts.functions.map((f) => [f.name, wrap(fnText(f), fnFont, boxW - 44)]));
  const fieldLines = Object.fromEntries(parts.properties.map((f) => [fieldText(f), wrap(fieldText(f), fldFont, boxW - 26)]));
  const most = (lines) => Math.max(1, ...Object.values(lines).map((l) => l.length));
  return { fnLines, fieldLines, fnRow: FAN.row + (most(fnLines) - 1) * FAN.rowLine,
    fieldRow: FAN.fieldRow + (most(fieldLines) - 1) * FAN.fieldLine };
}

/**
 * Cytoscape data for a class box. Expandable boxes draw their own face here, so the Cytoscape
 * label is empty; `base` = {name, marks, label, w, h} as a plain box would draw it.
 */
function fanNodeData(node, base) {
  if (!isExpandable(node)) return base;
  const mode = boxTextMode();
  const sub = faceSub(node, base.marks);
  const top = boxBadges(node).length ? FACE.badge : 0;
  const { w, ...face } = mode === "name" ? faceLayout(node, base, sub) : describedLayout(node, base, mode);
  const closedH = top + face.faceH;
  const data = { ...base, ...face, label: "", sub, top, w, h: closedH, head: closedH, face: closedH };
  if (!openParts(node.id)) return data;
  const head = closedH + (hasFilter(node.id) ? FAN.filterRow : 0);
  const openW = Math.max(w, Math.min(FAN.maxWidth, rowsWidth(node.id)));
  const rows = rowLayout(node.id, openW);
  const h = head + sectionsOf(node.id, rows).height;
  return { ...data, ...rows, w: openW, h, head, grow: (h - closedH) / 2, widen: (openW - w) / 2, open: true };
}

/** Where a class is drawn: an open box grows right and down from its closed box's top-left
 * corner, so opening or closing a class never moves its header or any other box. */
function grownSpot(id, spot) {
  const node = cy?.getElementById(id);
  return spot && { x: spot.x + (node?.data("widen") || 0), y: spot.y + (node?.data("grow") || 0) };
}

/** The spot to save for a drawn class: its position without the open-box offsets. */
function closedSpot(node) {
  const { x, y } = node.position();
  return { x: x - (node.data("widen") || 0), y: y - (node.data("grow") || 0) };
}

/** A drawn class's closed box size, and how far its open box reaches past it (half the extra
 * width and height): layouts that must not move when a class opens use the closed size. */
function closedSize(node) {
  const widen = node.data("widen") || 0, grow = node.data("grow") || 0;
  return { w: node.data("w") - 2 * widen, h: node.data("h") - 2 * grow, widen, grow };
}

function rectOf(node) {
  const { x, y } = node.position();
  const w = node.data("w") / 2;
  const h = node.data("h") / 2;
  return { x1: x - w, x2: x + w, y1: y - h, y2: y + h };
}

function fanStyles() {
  return [
    { selector: "edge.fanned", style: { display: "none" } },
  ];
}

// ---------- geometry ----------

function boxOf(id) {
  const node = cy.getElementById(id);
  if (node.empty()) return null;
  const { x, y } = node.position();
  const w = node.data("w");
  const h = node.data("h");
  return { id, x1: x - w / 2, x2: x + w / 2, y1: y - h / 2, y2: y + h / 2, cx: x, cy: y, w, h,
    head: node.data("head") || h, headH: node.data("headH") || FAN.head, fnRow: node.data("fnRow"), fieldRow: node.data("fieldRow"), face: node.data("face") || h,
    fnLines: node.data("fnLines"), fieldLines: node.data("fieldLines"), top: node.data("top") || 0, open: Boolean(node.data("open")) };
}

/** Where a link leaves or reaches a class: its function row, the list edge when that row is
 * scrolled out of view (or filtered out), the private group's row while it is folded, else the header. */
function anchorOf(box, fn, side) {
  const x = side === "right" ? box.x2 : box.x1;
  const header = box.y1 + box.top + box.headH / 2;
  if (!fn || !box.open || !openParts(box.id)?.functions.some((f) => f.name === fn)) return { x, y: header };
  const { fn: list, groups } = sectionsAt(box);
  const group = groups.find((g) => g.fns && g.names.has(fn));
  if (group && !group.open) return { x, y: group.top + FAN.groupRow / 2 + 2 };
  return rowAnchor(group ? group.list : list, fn, x);
}

/** A function's row in a list, or the list's top or bottom edge when it is out of view. */
function rowAnchor(list, fn, x) {
  const index = list.items.findIndex((f) => f.name === fn) - list.offset;
  if (index < 0) return { x, y: list.top, off: "up" };
  if (index >= list.rows) return { x, y: list.top + list.rows * list.row, off: "down" };
  return { x, y: list.top + index * list.row + list.row / 2 };
}

function bezierPoint(p0, p1, p2, p3, t) {
  const u = 1 - t;
  const f = (a, b, c, d) => u * u * u * a + 3 * u * u * t * b + 3 * u * t * t * c + t * t * t * d;
  return { x: f(p0.x, p1.x, p2.x, p3.x), y: f(p0.y, p1.y, p2.y, p3.y) };
}

/** A cubic with horizontal tangents from p to q (control points `reach` out): its SVG path
 * and points along it (for label spots). */
function curve(p, q, reach, endReach = reach) {
  const c1 = { x: p.x + reach, y: p.y };
  const c2 = { x: q.x - endReach, y: q.y };
  const points = Array.from({ length: 17 }, (_, i) => bezierPoint(p, c1, c2, q, i / 16));
  return { d: `M${p.x},${p.y} C${c1.x},${c1.y} ${c2.x},${c2.y} ${q.x},${q.y}`, points };
}

function meanY(points) {
  return points.reduce((sum, p) => sum + p.y, 0) / points.length;
}

// ---------- bundles: one line per function pair; many lines between two classes share a trunk ----------

function lookKey(link) {
  return [link.from, link.to, primaryKind(link.kinds), link.direct, link.faded, Boolean(link.quiet)].join("\n");
}

/** A quiet link (the view engine's `quiet`: no hint, Lines "Explained first") is drawn faint
 *  until one of its classes is hovered or selected (`state.nearIds`, app.js). */
function isFaint(link) {
  const near = state.nearIds || new Set();
  return Boolean(link.quiet) && !near.has(link.from) && !near.has(link.to);
}

/** Fan-out links grouped into what is drawn as one: a trunk for busy class pairs, else one per link. */
function bundlesOf(links) {
  const pairs = new Map();
  for (const link of links) {
    const key = link.internal ? `internal\n${link.key}` : lookKey(link);
    if (!pairs.has(key)) pairs.set(key, []);
    pairs.get(key).push(link);
  }
  const bundles = [];
  for (const [key, group] of pairs) {
    const one = (l) => ({ key: `${key}\n${l.key}`, kind: primaryKind(l.kinds), links: [l], internal: l.internal });
    if (group.length >= FAN.bundleMin && !group[0].internal) bundles.push({ key, kind: primaryKind(group[0].kinds), links: group });
    else bundles.push(...group.map(one));
  }
  return bundles;
}

/** The ends of a bundle: start points by calling function, end points by called function. */
function endsOf(bundle, box, side, end) {
  const points = new Map();
  for (const link of bundle.links) {
    const fn = end === "from" ? link.from_fn : link.to_fn;
    if (!points.has(fn || "")) points.set(fn || "", anchorOf(box, fn, side));
  }
  return points;
}

/** Calls inside one open class: a dashed arc out on the box's right side and back. */
function internalShape(a, link) {
  const p = anchorOf(a, link.from_fn, "right");
  const q = anchorOf(a, link.to_fn, "right");
  const line = curve(p, { x: q.x + 2, y: q.y }, FAN.bulge, -FAN.bulge);
  return { starts: new Map(), ends: new Map(), trunk: line, dir: 1, labelAt: { x: p.x + FAN.bulge * 0.8, y: (p.y + q.y) / 2 } };
}

/** Branches from each start into one trunk, which splits again near the target's functions.
 * A single link is the mock-up's curve: dx = max(50, half the gap). */
function bundleShape(bundle) {
  const first = bundle.links[0];
  const a = boxOf(first.from);
  const b = boxOf(first.to);
  if (!a || !b) return null;
  if (bundle.internal) return internalShape(a, first);
  const dir = b.cx >= a.cx ? 1 : -1;
  const starts = endsOf(bundle, a, dir > 0 ? "right" : "left", "from");
  const ends = endsOf(bundle, b, dir > 0 ? "left" : "right", "to");
  const p = [...starts.values()][0];
  const q = [...ends.values()][0];
  const reach = Math.min(FAN.junction, Math.abs(q.x - p.x) / 3);
  const j1 = starts.size > 1 ? { x: p.x + dir * reach, y: meanY([...starts.values()]) } : p;
  const j2 = ends.size > 1 ? { x: q.x - dir * reach, y: meanY([...ends.values()]) } : { x: q.x - 2 * dir, y: q.y };
  const trunk = curve(j1, j2, dir * Math.max(FAN.curve, Math.abs(j2.x - j1.x) / 2));
  const branch = (from, to) => curve(from, to, (to.x - from.x) / 2);
  return { starts, ends, trunk, j1, j2, branch, dir };
}

// ---------- link look ----------

/** Label text for some links: called names (or the count when the called functions are rows
 * already), an `as` prefix, the signal name, and the call count. */
function wireLabel(links) {
  const as = [...new Set(links.filter((l) => l.as && !l.internal).map((l) => l.as))];
  const names = [...new Set(links.flatMap((l) => l.members.map((m) => m.name)))];
  const count = links.reduce((sum, l) => sum + l.count, 0);
  const onRows = links.every((l) => l.to_fn && l.members.every((m) => m.name === l.to_fn));
  const text = onRows ? (count > 1 ? `×${count}` : "") : names.join(", ") + (count > names.length ? ` ×${count}` : "");
  const full = `${as.length === 1 ? `as ${as[0]}: ` : ""}${text}`.trim();
  return full.length > FAN.labelMax ? `${full.slice(0, FAN.labelMax - 1)}…` : full;
}

/** The functions whose lines are lit ("cls#fn"): the active (hovered or selected) one; in a tour
 *  step all its focused functions while none or one of them is active. Null: nothing is lit. */
function hotFns() {
  const fn = state.activeFn;
  const active = fn && `${fn.cls}#${fn.fn}`;
  const lit = state.tourStep ? state.current?.focus?.functions || [] : [];
  if (active && !lit.includes(active)) return new Set([active]);
  return lit.length ? new Set(lit) : null;
}

/** The focus set, the one rule for what stays lit around a selection or a tour step (everything else
 *  fades): the `classIds`, every line touching them and the classes at their other end, plus the
 *  `lineKeys` lines and both their ends. `edges` are view lines ({key, from, to}). */
function focusSet(edges, classIds = [], lineKeys = []) {
  const seeds = new Set(classIds);
  const nodes = new Set(classIds);
  const lines = new Set(lineKeys);
  for (const e of edges) {
    if (!seeds.has(e.from) && !seeds.has(e.to) && !lines.has(e.key)) continue;
    lines.add(e.key);
    nodes.add(e.from);
    nodes.add(e.to);
  }
  return { nodes, lines };
}

/** True when the focus set (app.js `applyFocus`) leaves every one of these fan-out links out. */
function outOfFocus(links, set = state.focusSet) {
  return Boolean(set) && !links.some((l) => (l.edge ? set.lines.has(l.edge) : set.nodes.has(l.from)));
}

/** A box drawn faded: the highlight filter, a domain hover, or outside the focus set. */
function isDimmed(node) {
  return node.hasClass("dim") || node.hasClass("dom-dim") || node.hasClass("foc-dim");
}

function isHot(link, hot = hotFns()) {
  if (!hot) return null;
  return hot.has(`${link.from}#${link.from_fn}`) || hot.has(`${link.to}#${link.to_fn}`);
}

/** Hot state of some links: true if any is hot, false if functions are lit and none is (or, with no
 *  function lit, when the focus set leaves them all out). */
function hotOf(links) {
  const hot = hotFns();
  if (!hot) return outOfFocus(links) ? false : null;
  return links.some((l) => isHot(l, hot));
}

/** The line under the mouse, or the selected class line, shows all its names. */
function isFocused(bundle) {
  const sel = state.selected?.type === "edge" ? state.selected.raw.key : null;
  return state.hotBundle === bundle.key || bundle.links.some((l) => l.edge && l.edge === sel);
}

/** A line without a function behind it (a field's type, a base): drawn like the mock's field line. */
function isFieldLine(link) {
  return !link.from_fn && !link.members.length;
}

function wireTone(link, kind, hot) {
  if (hot) return "hl";
  if (hot === false) return "dim";
  if (link.faded) return "faded";
  if (!link.direct) return "derived";
  if (kind === "signal") return "signal";
  return ["generalization", "realization"].includes(kind) || isFieldLine(link) ? "muted" : "accent";
}

function wireMarkers(kind, tone, { start = true, end = true } = {}) {
  const head = ["generalization", "realization"].includes(kind) ? "tri" : "arrow";
  const markers = {};
  if (end) markers["marker-end"] = `url(#fan-${head}-${tone})`;
  if (start && kind === "composition") markers["marker-start"] = `url(#fan-dia-${["hl", "dim"].includes(tone) ? tone : "muted"})`;
  return markers;
}

function drawMarkers(defs) {
  const shapes = { arrow: ["M1,1.5 L11,6 L1,10.5 z", 9], tri: ["M1,1.5 L11,6 L1,10.5 z", 10],
    dia: ["M1,6 L6,2.5 L11,6 L6,9.5 z", 15] };
  for (const tone of MARKER_TONES) {
    for (const [shape, [d, size]] of Object.entries(shapes)) {
      const marker = svgEl("marker", { id: `fan-${shape}-${tone}`, viewBox: "0 0 12 12", refX: shape === "dia" ? 1 : 11,
        refY: 6, markerWidth: size, markerHeight: size, markerUnits: "userSpaceOnUse", orient: "auto-start-reverse" }, defs);
      svgEl("path", { d, class: `fan-mark ${shape} t-${tone}` }, marker);
    }
  }
}

// ---------- drawing (two SVG layers: wires under the classes; faces, rows and labels over them) ----------

let fanFrame = 0;
function scheduleFan() {
  if (fanFrame) return;
  fanFrame = requestAnimationFrame(() => { fanFrame = 0; drawFan(); });
}

function syncFanViewport() {
  if (!cy) return;
  const { x, y } = cy.pan();
  const transform = `translate(${x},${y}) scale(${cy.zoom()})`;
  for (const id of ["domBack", "fanBack", "domFront", "fanFront"]) $(id).querySelector("g.world")?.setAttribute("transform", transform);
}

function resetLayer(id) {
  const svg = $(id);
  svg.textContent = "";
  const defs = svgEl("defs", {}, svg);
  return { defs, world: svgEl("g", { class: "world" }, svg) };
}

/** The filter box being typed in, so a redraw can give it back its focus and caret. */
function focusedFilter() {
  const input = document.activeElement;
  return input?.classList?.contains("fn-filter") ? { id: input.dataset.id, caret: input.selectionStart } : null;
}

function restoreFilter(focus) {
  if (!focus) return;
  const input = [...$("fanFront").querySelectorAll("input.fn-filter")].find((i) => i.dataset.id === focus.id);
  if (!input) return;
  input.focus();
  input.setSelectionRange(focus.caret, focus.caret);
}

function drawFan() {
  const focus = focusedFilter();
  const back = resetLayer("fanBack");
  const front = resetLayer("fanFront");
  drawMarkers(back.defs);
  const view = state.current;
  if (view && cy.nodes().nonempty()) {
    const drawn = new Set(cy.nodes(":visible").map((n) => n.id()));  // not boxes inside a folded domain card
    const links = shownLinks(view).filter((l) => drawn.has(l.from) && drawn.has(l.to));
    const wires = svgEl("g", { class: "wires" }, front.world);
    const labels = bundlesOf(links).map((bundle) => drawBundle(back.world, wires, bundle)).filter(Boolean);
    stackedNodes().forEach((n) => drawFace(front.world, n));
    drawLabels(front.world, spreadAlong(labels));
  }
  drawDomains();
  syncFanViewport();
  restoreFilter(focus);
}

/** The fan-out links drawn: all, less the self calls (one function calling another in the same
 * open class) while Show › Self calls is off; hidden ones take no label space either. */
function shownLinks(view) {
  const links = view?.fanout || [];
  return selfCallsShown() ? links : links.filter((l) => !l.internal);
}

function selfCallsShown() {
  return !state.fanHidden.includes(SELF_CALLS);
}

/** Show › Self calls and its legend entry follow the saved choice. */
function renderFanControls() {
  const shown = selfCallsShown();
  $("showSelfCalls").checked = shown;
  const entry = document.querySelector(".legend [data-fan]");
  entry.classList.toggle("off", !shown);
  entry.setAttribute("aria-pressed", String(shown));
  entry.title = `${shown ? "Click to hide" : "Hidden: click to show"} calls inside a class`;
}

function setSelfCalls(shown) {
  setOption("selfCalls", shown);
}

/** The id of the node the selection belongs to (a function belongs to its class), or null. */
function selectedNodeId() {
  const sel = state.selected;
  if (sel?.type === "node") return sel.raw.id;
  return sel?.type === "fn" ? sel.raw.cls : null;
}

/** Draw order of the box faces, the one place that decides z-order: open boxes first (under),
 * then the rest, the selected box last (on top). Each face is one SVG group with its own
 * background, so two boxes never interleave. */
function stackedNodes() {
  const selected = selectedNodeId();
  const rank = (n) => (n.id() === selected ? 2 : n.hasClass("open") ? 0 : 1);
  return cy.nodes(":visible").toArray().sort((a, b) => rank(a) - rank(b));
}

/** One path, with the hollow outline for lines through hidden classes. */
function drawPath(layer, d, bundle, tone, hot, markers) {
  const link = bundle.links[0];
  const classes = ["wire", `k-${bundle.kind}`, `t-${tone}`, link.internal && "internal", hot === true && "hot",
    hot === false && "dim", hot !== true && isFaint(link) && "faint"].filter(Boolean).join(" ");
  if (!link.direct) svgEl("path", { d, class: `${classes} outline` }, layer);
  svgEl("path", { d, class: classes + (link.direct ? "" : " hollow"), ...markers }, layer);
}

/** Draws a bundle's branches and trunk; returns its label item (or null when it has none). */
function drawBundle(back, front, bundle) {
  const shape = bundleShape(bundle);
  if (!shape) return null;
  const link = bundle.links[0];
  const hot = hotOf(bundle.links);
  const tone = (h) => wireTone(link, bundle.kind, h);
  const linksAt = (end, key) => bundle.links.filter((l) => ((end === "from" ? l.from_fn : l.to_fn) || "") === key);
  const fanIn = shape.starts.size > 1;
  const fanOut = shape.ends.size > 1;
  for (const [fn, p] of fanIn ? shape.starts : []) {
    const h = hotOf(linksAt("from", fn));
    drawPath(back, shape.branch(p, shape.j1).d, bundle, tone(h), h, wireMarkers(bundle.kind, tone(h), { end: false }));
  }
  drawPath(back, shape.trunk.d, bundle, tone(hot), hot, wireMarkers(bundle.kind, tone(hot), { start: !fanIn, end: !fanOut }));
  for (const [fn, q] of fanOut ? shape.ends : []) {
    const h = hotOf(linksAt("to", fn));
    const end = { x: q.x - 2 * shape.dir, y: q.y };
    drawPath(back, shape.branch(shape.j2, end).d, bundle, tone(h), h, wireMarkers(bundle.kind, tone(h), { start: false }));
  }
  drawWireHit(front, shape.trunk.d, bundle);
  return labelItem(bundle, shape, hot, isFaint(link));
}

/** What a bundle's label says: all names (like the mock-up) or when focused, the active
 * function's names when hot, else on a faint (quiet) line only a call count. */
function labelItem(bundle, shape, hot, quiet) {
  const focused = isFocused(bundle);
  const hotLinks = hot ? bundle.links.filter((l) => isHot(l)) : [];
  const count = bundle.links.reduce((sum, l) => sum + l.count, 0);
  let text;
  if (focused || !quiet) text = wireLabel(bundle.links);
  else if (hotLinks.length) text = wireLabel(hotLinks);
  else text = count > 1 ? `×${count}` : "";
  if (!text) return null;
  const loud = focused || hotLinks.length > 0;
  const w = textWidth(text, monoFont("11px")) + 6;
  const path = bundle.internal ? [shape.labelAt, { x: shape.labelAt.x + 1, y: shape.labelAt.y }] : shape.trunk.points;
  return { id: bundle.key, text, w, h: 14, path: lifted(path), required: loud || !quiet, loud, hot, focused,
    anchor: bundle.internal ? "start" : "middle", end: endKey(bundle), field: isFieldLine(bundle.links[0]), bundle };
}

/** Labels sit 6px above their line, as in the mock-up. */
function lifted(points) {
  return points.map((p) => ({ x: p.x, y: p.y - 10 }));
}

function endKey(bundle) {
  const l = bundle.links[0];
  return bundle.links.every((m) => m.to_fn === l.to_fn) ? `${l.to}#${l.to_fn || ""}` : `${l.to}#*`;
}

/** Labels of lines that end at one place start spread along their curves (t = 0.5 ± 0.2 each). */
function spreadAlong(items) {
  const byEnd = new Map();
  for (const item of items) {
    if (!byEnd.has(item.end)) byEnd.set(item.end, []);
    byEnd.get(item.end).push(item);
  }
  for (const group of byEnd.values()) {
    group.forEach((item, i) => {
      const t = item.field ? 0.72 : Math.min(0.9, Math.max(0.1, 0.5 + (i - (group.length - 1) / 2) * 0.2));
      item.along = [t, t - 0.08, t + 0.08, 0.5, 0.35, 0.65, 0.22, 0.78];
    });
  }
  return items;
}

/** An invisible wide stroke over the trunk: hover shows the line's names, click selects it. */
function drawWireHit(layer, d, bundle) {
  const edge = bundle.links[0].edge;
  const hit = svgEl("path", { d, class: "wire-hit" }, layer);
  hit.addEventListener("mouseenter", () => {
    state.hotBundle = bundle.key;
    const raw = edge && edgeRaw(edge);
    if (raw) previewDetails({ type: "edge", raw });
    scheduleFan();
  });
  hit.addEventListener("mouseleave", () => { state.hotBundle = null; endPreview(); scheduleFan(); });
  if (!edge) return;
  hit.addEventListener("click", () => selectEdgeByKey(edge));
  hit.addEventListener("dblclick", () => { const e = edgeRaw(edge); if (e) zoomIntoEdge(e); });
}

/** Box headers and the class-line labels, which fan-out labels must stay off. Open boxes
 * count whole (their rows); a closed box counts with its badges above it. */
function labelObstacles() {
  const pad = 2;
  const boxes = cy.nodes().map((n) => {
    const r = rectOf(n);
    return { x1: r.x1 - pad, x2: r.x2 + pad, y1: r.y1 - pad - (n.data("top") ? FACE.badge : 0), y2: r.y2 + pad };
  });
  return [...boxes, ...(state.edgeLabelRects || [])];
}

/** Loud labels (hovered or selected) first, so they get the spots nearest their line. */
function drawLabels(layer, items) {
  const ordered = [...items.filter((i) => i.loud), ...items.filter((i) => !i.loud)];
  const spots = CoderLayout.placeLabels(ordered, labelObstacles());
  for (const item of ordered) {
    const spot = spots.get(item.id);
    if (!spot) continue;
    const x = item.anchor === "start" ? spot.x - item.w / 2 + 3 : spot.x;
    const mood = item.hot === true || item.focused ? " hot" : item.hot === false ? " dim" : "";
    const label = svgEl("text", { x, y: spot.y + 4, "text-anchor": item.anchor, class: `wlabel${mood}` }, layer);
    label.textContent = item.text;
    if (item.text !== wireLabel(item.bundle.links)) svgEl("title", {}, label).textContent = wireLabel(item.bundle.links);
    const edge = item.bundle.links[0].edge;
    if (!edge) continue;
    label.classList.add("clickable");
    label.addEventListener("click", () => selectEdgeByKey(edge));
    label.addEventListener("dblclick", () => { const e = edgeRaw(edge); if (e) zoomIntoEdge(e); });
  }
}

// ---------- box faces: header band, toggle, name, badges, warning mark; rows when open ----------

let lastToggled = null;  // {id, wasOpen}: its triangle turns from the old way on the next draw

function drawFace(layer, node) {
  const raw = node.data("raw");
  const box = boxOf(node.id());
  const dim = isDimmed(node);
  const group = svgEl("g", { class: `face k-${raw.kind}${dim ? " dim" : ""}${node.id() === selectedNodeId() ? " sel" : ""}${PlanView.faceClass(raw)}` }, layer);
  if (dim && node.hasClass("foc-dim")) fadeIn(group);
  const alpha = node.scratch("_domAlpha");  // shrinking into a folded domain card
  if (alpha !== undefined) group.setAttribute("opacity", alpha);
  const open = box.open;
  const top = box.y1 + box.top;
  if (isExpandable(raw)) {  // opaque card, so whatever is below this box never shows through it
    svgEl("rect", { x: box.x1, y: top, width: box.w, height: box.y2 - top, rx: 7, class: "box-bg" }, group);
    if (raw.made === "manual") svgEl("rect", { x: box.x1 + 3, y: top + 3, width: box.w - 6, height: box.y2 - top - 6, rx: 5, class: "box-inner" }, group);  // double outline
  }
  drawBadges(group, box, boxBadges(raw));
  drawWarnMark(group, box, raw.warnings || []);
  ProgressView.drawRing(group, box, raw);
  if (!isExpandable(raw)) return;
  svgEl("rect", { x: box.x1 + 1, y: top + 1, width: box.w - 2, height: box.headH - 2, rx: 6, class: "cls-head" }, group);
  drawTypeEdge(group, box, raw);
  if (node.data("descLines")) drawDescribedText(group, box, node, top);
  else drawFaceText(group, box, node, top);
  if (!raw.made || raw.functions) drawToggle(group, box, raw, open, top + 16.5);  // centred on the name line; a planned box without functions has none
  if (open) drawRows(group, box, raw);
}

/** The coloured top edge (reads when zoomed far out) in the class's type colour. */
function drawTypeEdge(group, box, raw) {
  const type = fileType(raw.file);
  if (!type) return;
  const { bg } = typeColors(type, themeName());
  svgEl("line", { x1: box.x1 + 8, x2: box.x2 - 8, y1: box.y1 + box.top + 1.5, y2: box.y1 + box.top + 1.5,
    stroke: bg, "stroke-width": 3, "stroke-linecap": "round", class: "type-edge" }, group);
}

/** Name lines, then the sub-line rows with the type chip in front. */
function drawFaceText(group, box, node, top) {
  const nameLines = node.data("nameLines");
  nameLines.forEach((line, i) => {
    svgEl("text", { x: box.x1 + FACE.left, y: top + 21 + i * FACE.nameLine, class: "cls-name" }, group).textContent = line;
  });
  const subTop = top + 37 + (nameLines.length - 1) * FACE.nameLine;
  const type = node.data("type");
  const gap = type ? node.data("chipW") + FACE.chipGap : 0;
  if (type) drawChip(group, box.x1 + FACE.left, subTop - 11, type, node.data("chipW"));
  node.data("subLines").forEach((line, i) => {
    svgEl("text", { x: box.x1 + FACE.left + gap, y: subTop + i * FACE.subLine, class: "cls-sub" }, group).textContent = line;
  });
}

/** The described face's text: name lines and chip, what it does (or Ask AI), the footer. */
function drawDescribedText(group, box, node, top) {
  const raw = node.data("raw");
  const nameLines = node.data("nameLines");
  nameLines.forEach((line, i) => {
    svgEl("text", { x: box.x1 + FACE.left, y: top + 21 + i * FACE.nameLine, class: "cls-name" }, group).textContent = line;
  });
  const type = node.data("type");
  if (type) {
    const lastY = top + 21 + (nameLines.length - 1) * FACE.nameLine;
    const lastW = textWidth(nameLines.at(-1), monoFont(FACE.name));
    const inline = node.data("chipInline");
    drawChip(group, box.x1 + FACE.left + (inline ? lastW + FACE.chipGap : 0), (inline ? lastY : lastY + FACE.nameLine) - 11, type, node.data("chipW"));
  }
  const badge = node.data("badge");
  if (badge) {
    const w = usedBadgeWidth(badge);
    const pill = svgEl("g", { class: "badge b-used" }, group);
    svgEl("rect", { x: box.x2 - FACE.right + 4 - w, y: top + 9, width: w, height: 15, rx: 4 }, pill);
    svgEl("text", { x: box.x2 - FACE.right + 4 - w / 2, y: top + 20, "text-anchor": "middle" }, pill).textContent = badge;
  }
  const x = box.x1 + DESC.left;
  const descTop = top + box.headH + DESC.padTop;
  const lines = node.data("descLines");
  if (lines.length) {
    const text = svgEl("text", { class: "cls-desc" }, group);
    lines.forEach((line, i) => { svgEl("tspan", { x, y: descTop + 12 + i * DESC.line }, text).textContent = line; });
    svgEl("title", {}, text).textContent = whatItDoes(raw, true);
  } else {
    drawAskAi(group, raw, x, descTop);
  }
  const footTop = descTop + Math.max(1, lines.length) * DESC.line + 2;
  drawDots(group, x, footTop + 6, importance(raw), raw.used_by || 0);
  node.data("footLines").forEach((line, i) => {
    svgEl("text", { x: x + DESC.dots, y: footTop + 10 + i * DESC.footLine, class: "cls-sub" }, group).textContent = line;
  });
}

/** "No description yet" and an Ask AI button: copies a request for the AI (no AI is called). */
function drawAskAi(group, raw, x, y) {
  svgEl("text", { x, y: y + 12, class: "cls-desc none" }, group).textContent = DESC.none;
  const bx = x + textWidth(DESC.none, `italic ${bodyFont(DESC.font)}`) + 8;
  const button = svgEl("g", { class: "ask-btn", tabindex: 0, role: "button", "aria-label": `Ask AI to describe ${raw.name}` }, group);
  svgEl("rect", { x: bx, y: y + 1, width: askButtonWidth(), height: 15, rx: 4 }, button);
  svgEl("text", { x: bx + askButtonWidth() / 2, y: y + 12, "text-anchor": "middle" }, button).textContent = DESC.ask;
  svgEl("title", {}, button).textContent = "Copy a request for the AI to write what this class does";
  const ask = (event) => { event.stopPropagation(); askAi(describeRequest(raw), flashNote, showCopyBox); };
  button.addEventListener("click", ask);
  button.addEventListener("dblclick", (event) => event.stopPropagation());
  button.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); ask(event); } });
}

/** A face that just left the focus set fades out over FOCUS_FADE_MS (none under reduced motion, CSS):
 *  faces are redrawn often, so a negative delay carries the fade on across redraws. */
const FOCUS_FADE_MS = 200;
function fadeIn(group) {
  const age = performance.now() - (state.focusAt || 0);
  if (age >= FOCUS_FADE_MS) return;
  group.classList.add("foc-fade");
  group.style.animationDelay = `${-Math.round(age)}ms`;
}

/** The request Ask AI copies for a class without a description, or for a line (`from`/`to`) without a hint. */
function describeRequest(raw) {
  const app = CoderApps.current;
  if (raw.from) {
    const target = `${raw.from}>${raw.to}`;
    return `Write what the line ${nodeName(raw.from)} → ${nodeName(raw.to)} in ${app} is for: see its calls with \`coderstanding zoom ${app} ${raw.from} ${raw.to}\`, then save one plain sentence for a newcomer with \`coderstanding set ${app} ${target} hint="..."\`.`;
  }
  return `Write what ${raw.name} in ${app} is for: read it with \`coderstanding outline ${app} ${raw.id}\`, then save one plain sentence for a newcomer with \`coderstanding set ${app} ${raw.id} hint="..."\`.`;
}

/** The importance dots: `on` of 3 lit. */
function drawDots(group, x, y, on, used) {
  const dots = svgEl("g", { class: "imp-dots" }, group);
  for (let i = 0; i < 3; i++) svgEl("circle", { cx: x + 3 + i * 8, cy: y, r: 3, class: i < on ? "on" : "" }, dots);
  svgEl("title", {}, dots).textContent = `How much it matters: used by ${used} ${used === 1 ? "class" : "classes"}`;
}

function drawChip(group, x, y, type, width) {
  const { bg, ink } = typeColors(type, themeName());
  svgEl("rect", { x, y, width, height: 14, rx: 4, fill: bg, class: "type-chip" }, group);
  svgEl("text", { x: x + width / 2, y: y + 10.5, "text-anchor": "middle", fill: ink, class: "type-chip-text" }, group).textContent = type;
}

function drawToggle(group, box, raw, open, y) {
  const x = box.x1 + 16.5;  // left edge 12px in, as in the mock-up; the name starts at 28
  const was = lastToggled?.id === raw.id ? lastToggled.wasOpen : open;
  const tri = svgEl("g", { transform: `translate(${x},${y})` }, group);
  const path = svgEl("path", { d: TRIANGLE, class: `fan-tri${was ? " open" : ""}` }, tri);
  if (was !== open) { lastToggled = null; requestAnimationFrame(() => requestAnimationFrame(() => path.classList.toggle("open", open))); }
  const hit = svgEl("rect", { x: x - 12, y: y - 12, width: 24, height: 24, rx: 5, class: "fan-hit", tabindex: 0,
    role: "button", "aria-expanded": String(open), "aria-label": `${open ? "Collapse" : "Expand"} ${raw.name}` }, group);
  hit.addEventListener("click", (event) => { event.stopPropagation(); toggleExpand(raw.id); });
  hit.addEventListener("dblclick", (event) => event.stopPropagation());
  hit.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggleExpand(raw.id); }
  });
}

/** Start badges as pills on the box's top edge. */
function drawBadges(group, box, badges) {
  let x = box.x1 + 10;
  const font = `600 10.5px ${token("f-body")}`;
  for (const badge of badges) {
    const w = textWidth(badge.text, font) + 12;
    const pill = svgEl("g", { class: `badge b-${badge.look}` }, group);
    svgEl("rect", { x, y: box.y1 - 8, width: w, height: 16, rx: 8 }, pill);
    svgEl("text", { x: x + w / 2, y: box.y1 + 3.8, "text-anchor": "middle" }, pill).textContent = badge.text;
    x += w + 4;
  }
}

/** Warnings: a small warn-coloured round mark with "!" (or the count) on the top right corner. */
function drawWarnMark(group, box, warnings) {
  if (!warnings.length) return;
  const text = warnings.length > 1 ? String(warnings.length) : "!";
  const w = Math.max(16, text.length * 7 + 9);
  const mark = svgEl("g", { class: "warn-mark" }, group);
  svgEl("rect", { x: box.x2 - w + 4, y: box.y1 - 7, width: w, height: 16, rx: 8 }, mark);
  svgEl("text", { x: box.x2 - w / 2 + 4, y: box.y1 + 5, "text-anchor": "middle" }, mark).textContent = text;
  svgEl("title", {}, mark).textContent = warnings.map((x) => x.text).join("\n");
}

function drawRows(group, box, raw) {
  const { fn, groups } = sectionsAt(box);
  drawFnList(group, box, raw, fn);
  if (hasFilter(raw.id)) drawFilter(group, box, raw.id);
  groups.forEach((g) => drawGroup(group, box, raw, g));
}

function shownItems(list) {
  return list.items.slice(list.offset, list.offset + list.rows);
}

function drawFnList(layer, box, raw, list) {
  const fnList = svgEl("g", { class: "fn-list" }, layer);
  drawListBack(fnList, box, list);
  shownItems(list).forEach((f, i) => drawFnRow(fnList, box, raw, f, list.top + i * list.row));
  drawScroll(fnList, box, list, offLines(raw.id, list));
}

function drawFieldList(layer, box, list) {
  const fields = svgEl("g", { class: "field-list" }, layer);
  drawListBack(fields, box, list);
  shownItems(list).forEach((f, i) => box.fieldLines[fieldText(f)].forEach((line, n) => {
    svgEl("text", { x: box.x1 + 16, y: list.top + 13 + i * list.row + n * FAN.fieldLine, class: "fld" }, fields).textContent = line;
  }));
  drawScroll(fields, box, list, null);
}

function groupLabel(g) {
  const text = g.kind === "property" ? plural(g.total, "property", "properties") : `${g.total} ${g.kind}`;
  return g.fns && g.filtered ? `${text} · ${g.items.length} matching` : text;
}

/** A folded group: a separator, its row (triangle + "12 fields", click or Enter opens it), then its list when open. */
function drawGroup(layer, box, raw, g) {
  const y = g.top + 4;
  svgEl("line", { x1: box.x1 + 10, x2: box.x2 - 10, y1: y, y2: y, class: "fld-sep" }, layer);
  const row = svgEl("g", { class: `grp${g.open ? " open" : ""}` }, layer);
  const hit = svgEl("rect", { x: box.x1 + 1, y: y + 1, width: box.w - 2, height: FAN.groupRow - 6, class: "grp-row",
    tabindex: 0, role: "button", "aria-expanded": String(g.open), "data-group": g.key,
    "aria-label": `${g.open ? "Fold" : "Show"} ${raw.name} ${{ property: "properties", private: "private functions", more: "other functions" }[g.kind]}` }, row);
  const tri = svgEl("g", { transform: `translate(${box.x1 + 18},${y + 11}) scale(0.75)` }, row);
  svgEl("path", { d: TRIANGLE, class: `fan-tri${g.open ? " open" : ""}` }, tri);
  svgEl("text", { x: box.x1 + 28, y: y + 15, class: "grp-label" }, row).textContent = groupLabel(g);
  hit.addEventListener("click", (event) => { event.stopPropagation(); toggleGroup(g.key); });
  hit.addEventListener("dblclick", (event) => event.stopPropagation());
  hit.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggleGroup(g.key); }
  });
  if (!g.open) return;
  if (g.fns) drawFnList(layer, box, raw, g.list);
  else drawFieldList(layer, box, g.list);
}

/** Open or fold a group of an open box (kept for the session); only that box changes size. */
function toggleGroup(key) {
  if (!state.openGroups.delete(key)) state.openGroups.add(key);
  loadView({ inPlace: true });
}

function drawFnRow(layer, box, raw, f, y) {
  const active = state.activeFn?.cls === raw.id && state.activeFn?.fn === f.name;
  const lit = state.tourStep && state.current?.focus?.functions?.includes(`${raw.id}#${f.name}`);
  const row = svgEl("g", { class: `fn${active ? " active" : ""}${lit ? " lit" : ""}${f.made ? " planned" : f.stub ? " stub" : ""}` }, layer);
  const hit = svgEl("rect", { x: box.x1 + 1, y, width: box.w - 2, height: box.fnRow, class: "fn-row",
    tabindex: 0, role: "button", "aria-label": `${raw.name}.${f.name}` }, row);
  const lines = box.fnLines[f.name];
  lines.forEach((line, n) => {
    const text = svgEl("text", { x: box.x1 + 16, y: y + 16 + n * FAN.rowLine, class: "fn-name" }, row);
    const mark = n === lines.length - 1 ? PlanView.fnMark(f) : "";
    const body = mark && line.endsWith(mark) ? line.slice(0, -mark.length) : line;
    const last = n === lines.length - 1 && body.endsWith("()");
    text.textContent = last ? body.slice(0, -2) : body;
    if (last) svgEl("tspan", { class: "paren" }, text).textContent = "()";
    if (mark && body !== line) svgEl("tspan", { class: "fn-mark" }, text).textContent = mark;
  });
  const item = fnItem(raw.id, f);
  hit.addEventListener("mouseenter", () => hoverFn(item));
  hit.addEventListener("focus", () => hoverFn(item));
  hit.addEventListener("mouseleave", () => hoverFn(null));
  hit.addEventListener("click", (event) => { event.stopPropagation(); selectFn(item); });
}

/** A see-through back behind a long list: the wheel over it scrolls the list. */
function drawListBack(layer, box, list) {
  if (list.items.length <= list.rows) return;
  svgEl("rect", { x: box.x1 + 1, y: list.top, width: box.w - 2, height: list.rows * list.row, class: "list-back" }, layer);
  layer.addEventListener("wheel", (event) => {
    event.preventDefault();
    event.stopPropagation();
    const max = list.items.length - list.rows;
    state.listScroll.set(list.key, Math.min(max, Math.max(0, list.offset + Math.sign(event.deltaY) * FAN.wheelRows)));
    scheduleFan();
  }, { passive: false });
}

/** Lines that attach to functions scrolled out of view, above and below. */
function offLines(id, list) {
  const names = list.items.map((f) => f.name);
  const counts = { up: 0, down: 0 };
  for (const l of shownLinks(state.current)) {
    for (const [cls, fn] of [[l.from, l.from_fn], [l.to, l.to_fn]]) {
      const index = cls === id && fn ? names.indexOf(fn) : -1;
      if (index < 0) continue;
      if (index < list.offset) counts.up++;
      else if (index >= list.offset + list.rows) counts.down++;
    }
  }
  return counts;
}

/** A thin scrollbar and "↑ N / ↓ N more lines" marks for a scrolled list. */
function drawScroll(layer, box, list, off) {
  if (list.items.length <= list.rows) return;
  const height = list.rows * list.row;
  const thumb = Math.max(16, height * (list.rows / list.items.length));
  const at = (height - thumb) * (list.offset / (list.items.length - list.rows));
  svgEl("rect", { x: box.x2 - 5, y: list.top + 2, width: 3, height: height - 4, rx: 1.5, class: "scroll-track" }, layer);
  svgEl("rect", { x: box.x2 - 5, y: list.top + 2 + at * ((height - 4) / height), width: 3, height: thumb - 4, rx: 1.5,
    class: "scroll-thumb" }, layer);
  if (!off) return;
  if (off.up) svgEl("text", { x: box.x2 - 10, y: list.top + 11, "text-anchor": "end", class: "off-lines" }, layer)
    .textContent = `↑ ${off.up} more line${off.up === 1 ? "" : "s"}`;
  if (off.down) svgEl("text", { x: box.x2 - 10, y: list.top + height - 4, "text-anchor": "end", class: "off-lines" }, layer)
    .textContent = `↓ ${off.down} more line${off.down === 1 ? "" : "s"}`;
}

/** A filter box under the header of a class with many functions. */
function drawFilter(group, box, id) {
  const fo = svgEl("foreignObject", { x: box.x1 + 10, y: box.y1 + box.face - 2, width: box.w - 20, height: 24 }, group);
  const input = document.createElement("input");
  input.className = "fn-filter";
  input.type = "search";
  input.placeholder = `Filter ${openParts(id).functions.length} functions`;
  input.setAttribute("aria-label", "Filter functions");
  input.dataset.id = id;
  input.value = state.fnFilter.get(id) || "";
  input.addEventListener("input", () => {
    state.fnFilter.set(id, input.value);
    state.listScroll.delete(`${id}#fn`);
    state.listScroll.delete(`${id}#private`);
    scheduleFan();
  });
  input.addEventListener("keydown", (event) => event.stopPropagation());
  fo.appendChild(input);
}

// ---------- functions: hover, select, details ----------

function fnItem(cls, row) {
  return { type: "fn", raw: { ...row, id: `${cls}#${row.name}`, cls, fn: row.name } };
}

/** The current view's function item for `cls#fn`, or null. */
function fnById(id) {
  const at = id.lastIndexOf("#");
  const cls = id.slice(0, at);
  const row = openParts(cls)?.functions.find((f) => f.name === id.slice(at + 1));
  return row ? fnItem(cls, row) : null;
}

function hoverFn(item) {
  if (editingNote()) return;
  const sel = state.selected?.type === "fn" ? state.selected.raw : null;
  state.activeFn = item ? item.raw : sel;
  scheduleFan();
  if (item) previewDetails(item); else endPreview();
}

function selectFn(item) {
  select(item);
  state.activeFn = item.raw;
  scheduleFan();
}

function fnLinks(fn) {
  const links = state.current?.fanout || [];
  return {
    out: links.filter((l) => l.from === fn.cls && l.from_fn === fn.fn),
    in: links.filter((l) => l.to === fn.cls && l.to_fn === fn.fn && !(l.internal && l.from_fn === fn.fn)),
  };
}

function locationText(link) {
  if (!link.at.length) return "";
  const shown = link.at.slice(0, 2).map((a) => `${a.file}:${a.line}`).join(", ");
  return ` <span class="count">${esc(shown)}${link.at.length > 2 ? ` +${link.at.length - 2}` : ""}</span>`;
}

function endName(id, fn) {
  return fn ? `${nodeName(id)}#${fn}` : nodeName(id);
}

function fnCallList(links, side) {
  if (!links.length) return `<span class="empty">nothing in view</span>`;
  return `<ul>${links.map((l) => {
    const far = side === "out" ? nodeName(l.to) : endName(l.from, l.from_fn);
    const what = side === "out" ? l.members.map((m) => `${far}.${m.name}`).join(", ") : far;
    const mark = l.direct ? "" : ` <span class="count">via hidden</span>`;
    return `<li><code>${esc(what)}</code> <span class="count">×${l.count}</span>${mark}${locationText(l)}</li>`;
  }).join("")}</ul>`;
}

function renderFunction(fn) {
  const links = fnLinks(fn);
  const cls = cy.getElementById(fn.cls).data("raw");
  $("details").innerHTML = `
    ${PlanView.functionHtml(fn, cls)}<h3>${esc(nodeName(fn.cls))}#${esc(fn.fn)}()</h3>
    <div class="sub">function${fn.line ? ` · line ${fn.line}` : ""}</div>
    <dl>
      ${noteFields(fn)}
      ${field("file", "File", cls?.file && fn.line ? `<code>${esc(cls.file)}:${fn.line}</code>${sourceLinks(cls.file, fn.line)}` : "")}
      ${field("calls", `Calls (${links.out.length})`, fnCallList(links.out, "out"))}
      ${field("called_by", `Called by (${links.in.length})`, fnCallList(links.in, "in"))}
    </dl>
    <div class="actions">${refButtons(CoderRefs.target.member(fn.cls, fn.fn), fn.description || fn.hint)}</div>`;
  growAllNotes();
}

// ---------- expanding ----------

function toggleExpand(id) {
  lastToggled = { id, wasOpen: state.expanded.has(id) };
  if (state.expanded.has(id)) state.expanded.delete(id);
  else state.expanded.add(id);
  if (state.activeFn?.cls === id) state.activeFn = null;
  loadView({ inPlace: true });
}

function expandAll() {
  const ids = (state.current?.nodes || []).filter((n) => isExpandable(n) && !isMessageNode(n)).map((n) => n.id);
  ids.slice(0, EXPAND_CAP).forEach((id) => state.expanded.add(id));
  if (ids.length > EXPAND_CAP) flashNote(`Opened ${EXPAND_CAP} of ${ids.length} classes (the cap for Expand all).`);
  loadView({ inPlace: true });
}

function collapseAll() {
  state.expanded.clear();
  state.activeFn = null;
  loadView({ inPlace: true });
}

/** Keep only the classes the engine opened (still shown at this level). */
function keepExpanded(view) {
  state.expanded = new Set(Object.keys(view.expanded || {}));
}

/** Rows sit over the graph: pass the wheel on so zooming still works over an open class. */
function forwardWheel(event) {
  event.preventDefault();
  $("cy").querySelector("canvas:last-of-type")?.dispatchEvent(new WheelEvent("wheel", event));
}

function bindFan() {
  $("expandAllBtn").addEventListener("click", expandAll);
  $("collapseAllBtn").addEventListener("click", collapseAll);
  $("showSelfCalls").addEventListener("change", (event) => setSelfCalls(event.target.checked));
  document.querySelector(".legend [data-fan]").addEventListener("click", () => setSelfCalls(!selfCallsShown()));
  $("fanFront").addEventListener("wheel", forwardWheel, { passive: false });
  cy.on("viewport", syncFanViewport);
  cy.on("position", "node", scheduleFan);
}
