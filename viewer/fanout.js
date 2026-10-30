// Function fan-out (DESIGN 12c): open classes show their functions, and the engine's fan-out
// links are drawn from and to those functions. Draws what /api/view returns ("expanded",
// "fanout"); no graph logic here. Loaded before app.js and uses its state, cy and helpers.
// Also draws the face of every class box (header band, toggle triangle, badges, warning mark)
// the way the approved mock-up does (docs/DESIGN.md 12c).
"use strict";

// Sizes from the mock-up: header 46, function row 24, field row 18.
const FAN = { head: 46, row: 24, fieldRow: 18, rowLine: 15, fieldLine: 14, fieldGap: 10, pad: 8, maxWidth: 380, labelMax: 46,
  junction: 56, visibleRows: 20, visibleFields: 12, filterAt: 20, filterRow: 28, wheelRows: 3,
  bundleMin: 4,     // this many lines between one pair of classes share a trunk
  quietLabels: 10, quietLinks: 16,  // a busier view shows names only on hover (counts stay)
  curve: 50, bulge: 34 };
const FACE = { left: 28, right: 14, badge: 7, name: "600 13.5px", sub: "11px", nameLine: 17, subLine: 14,
  wrapAt: 320,   // a box grows to fit its name up to this width, then the name wraps (never cut off)
  chip: "700 10px", chipPad: 10, chipGap: 6 };
const EXPAND_CAP = 30;  // "Expand all" opens at most this many classes
const NOT_EXPANDABLE = ["external", "domain"];
const SVG_NS = "http://www.w3.org/2000/svg";
const MARKER_TONES = ["accent", "muted", "derived", "faded", "hl", "dim", "signal"];
const TRIANGLE = "M-4.5,-6.5 L7,0 L-4.5,6.5 Z";  // the expand toggle (13px): points right, turns down when open
// Start badges: the real mechanism that runs a class first (scanner flags; `entry` is generic).
const START_BADGES = { "godot-main": "Godot main", "godot-main-scene": "Godot main scene",
  autoload: "Godot autoload", "program-main": "Program main()", "script-main": "Script __main__", entry: "Entry point" };
const BADGE_LOOK = { autoload: "soft", "script-main": "quiet", entry: "quiet" };
const HUB_FLAG = "hub";  // promoted to level 1 for its many connections (`hub_degree`)

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

function isExpandable(node) {
  return !NOT_EXPANDABLE.includes(node.kind) && !node.faded;
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
  if (node.flags.includes(HUB_FLAG)) {
    const n = node.hub_degree;
    badges.push({ flag: HUB_FLAG, text: n ? `Hub · ${n} connections` : "Hub", look: "hub" });
  }
  return badges;
}

// ---------- the lists in an open box: all functions and fields, scrolling when long ----------

function fnFilter(id) {
  return (state.fnFilter.get(id) || "").trim().toLowerCase();
}

/** The functions an open class lists (all of them, or those matching its filter) and its fields. */
function shownParts(id) {
  const parts = openParts(id);
  if (!parts) return null;
  const filter = fnFilter(id);
  const functions = filter ? parts.functions.filter((f) => f.name.toLowerCase().includes(filter)) : parts.functions;
  return { functions, fields: parts.fields, total: parts.functions.length, totalFields: parts.fields.length };
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

/** Geometry of the two lists of an open box: top, row size, visible rows, scroll offset. */
function listsOf(box) {
  const parts = shownParts(box.id);
  const fnRows = listRows(parts.total, FAN.visibleRows);
  const fnTop = box.y1 + box.head;
  const fields = listRows(parts.totalFields, FAN.visibleFields);
  const fieldTop = fnTop + fnRows * box.fnRow + FAN.fieldGap / 2;
  return {
    fn: { key: `${box.id}#fn`, items: parts.functions, top: fnTop, row: box.fnRow, rows: fnRows,
      offset: scrollOf(`${box.id}#fn`, parts.functions.length, fnRows) },
    field: { key: `${box.id}#field`, items: parts.fields, top: fieldTop, row: box.fieldRow, rows: fields,
      offset: scrollOf(`${box.id}#field`, parts.fields.length, fields) },
  };
}

// ---------- the class box: face drawn here, Cytoscape draws the frame ----------

function fieldText(f) {
  if (f.kind === "signal") return `signal ${f.name}`;
  return f.type ? `${f.name}: ${f.type}` : f.name;
}

function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/** Subtitle under the name: "N functions · M fields", then marks (status, tags, flags). */
function faceSub(node, marks) {
  const counts = node.functions !== undefined || node.fields !== undefined
    ? `${plural(node.functions || 0, "function")} · ${plural(node.fields || 0, "field")}` : "";
  return [counts, marks].filter(Boolean).join(" · ");
}

function monoFont(size) {
  return `${size} ${token("f-mono")}`;
}

function chipWidth(type) {
  return type ? textWidth(type, monoFont(FACE.chip)) + FACE.chipPad : 0;
}

/** The face's text: name and sub-line on as many lines as they need, the type chip, header height. */
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
  return { type, chipW, nameLines, subLines, headH, w: widest + FACE.left + FACE.right };
}

/** Width of the open rows: the longest function or field. */
function rowsWidth(id) {
  const parts = openParts(id);
  return Math.max(0, ...parts.functions.map((f) => textWidth(`${f.name}()`, monoFont("12.5px")) + 36),
    ...parts.fields.map((f) => textWidth(fieldText(f), monoFont("11.5px")) + 32));
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
  const fnLines = Object.fromEntries(parts.functions.map((f) => [f.name, wrap(`${f.name}()`, fnFont, boxW - 44)]));
  const fieldLines = Object.fromEntries(parts.fields.map((f) => [fieldText(f), wrap(fieldText(f), fldFont, boxW - 26)]));
  const most = (lines) => Math.max(1, ...Object.values(lines).map((l) => l.length));
  return { fnLines, fieldLines, fnRow: FAN.row + (most(fnLines) - 1) * FAN.rowLine,
    fieldRow: FAN.fieldRow + (most(fieldLines) - 1) * FAN.fieldLine };
}

function rowsHeight(id, rows) {
  const parts = openParts(id);
  const fields = parts.fields.length ? FAN.fieldGap + listRows(parts.fields.length, FAN.visibleFields) * rows.fieldRow : 0;
  return listRows(parts.functions.length, FAN.visibleRows) * rows.fnRow + fields + FAN.pad;
}

/**
 * Cytoscape data for a class box. Expandable boxes draw their own face here, so the Cytoscape
 * label is empty; `base` = {name, marks, label, w, h} as a plain box would draw it.
 */
function fanNodeData(node, base) {
  if (!isExpandable(node)) return base;
  const sub = faceSub(node, base.marks);
  const top = boxBadges(node).length ? FACE.badge : 0;
  const { w, ...face } = faceLayout(node, base, sub);
  const closedH = top + face.headH;
  const data = { ...base, ...face, label: "", sub, top, w, h: closedH, head: closedH };
  if (!openParts(node.id)) return data;
  const head = closedH + (openParts(node.id).functions.length > FAN.filterAt ? FAN.filterRow : 0);
  const openW = Math.max(w, Math.min(FAN.maxWidth, rowsWidth(node.id)));
  const rows = rowLayout(node.id, openW);
  const h = head + rowsHeight(node.id, rows);
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

function rectOf(node) {
  const { x, y } = node.position();
  const w = node.data("w") / 2;
  const h = node.data("h") / 2;
  return { x1: x - w, x2: x + w, y1: y - h, y2: y + h };
}

function fanStyles() {
  return [
    { selector: "node.open", style: { "z-index": 5 } },
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
    head: node.data("head") || h, headH: node.data("headH") || FAN.head, fnRow: node.data("fnRow"), fieldRow: node.data("fieldRow"),
    fnLines: node.data("fnLines"), fieldLines: node.data("fieldLines"), top: node.data("top") || 0, open: Boolean(node.data("open")) };
}

/** Where a link leaves or reaches a class: its function row, the list edge when that row is
 * scrolled out of view (or filtered out), else the header. */
function anchorOf(box, fn, side) {
  const x = side === "right" ? box.x2 : box.x1;
  const header = box.y1 + box.top + box.headH / 2;
  if (!fn || !box.open || !openParts(box.id)?.functions.some((f) => f.name === fn)) return { x, y: header };
  const list = listsOf(box).fn;
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
  return [link.from, link.to, primaryKind(link.kinds), link.direct, link.faded].join("\n");
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

function isHot(link) {
  const fn = state.activeFn;
  if (!fn) return null;
  return (link.from === fn.cls && link.from_fn === fn.fn) || (link.to === fn.cls && link.to_fn === fn.fn);
}

/** Hot state of some links: true if any is hot, false if a function is active and none is. */
function hotOf(links) {
  if (!state.activeFn) return null;
  return links.some((l) => isHot(l));
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
  for (const id of ["fanBack", "fanFront"]) $(id).querySelector("g.world")?.setAttribute("transform", transform);
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
    const drawn = new Set(cy.nodes().map((n) => n.id()));
    const links = (view.fanout || []).filter((l) => drawn.has(l.from) && drawn.has(l.to));
    const wires = svgEl("g", { class: "wires" }, front.world);
    const bundles = bundlesOf(links);
    const quiet = links.length > FAN.quietLinks || bundles.filter((b) => wireLabel(b.links)).length > FAN.quietLabels;
    const labels = bundles.map((bundle) => drawBundle(back.world, wires, bundle, quiet)).filter(Boolean);
    // Open boxes are drawn last, over any closed box they cover (nothing is moved aside).
    cy.nodes().not(".open").forEach((n) => drawFace(front.world, n));
    cy.nodes(".open").forEach((n) => drawFace(front.world, n));
    drawLabels(front.world, spreadAlong(labels));
  }
  syncFanViewport();
  restoreFilter(focus);
}

/** One path, with the hollow outline for lines through hidden classes. */
function drawPath(layer, d, bundle, tone, hot, markers) {
  const link = bundle.links[0];
  const classes = ["wire", `k-${bundle.kind}`, `t-${tone}`, link.internal && "internal", hot === true && "hot",
    hot === false && "dim"].filter(Boolean).join(" ");
  if (!link.direct) svgEl("path", { d, class: `${classes} outline` }, layer);
  svgEl("path", { d, class: classes + (link.direct ? "" : " hollow"), ...markers }, layer);
}

/** Draws a bundle's branches and trunk; returns its label item (or null when it has none). */
function drawBundle(back, front, bundle, quiet) {
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
  return labelItem(bundle, shape, hot, quiet);
}

/** What a bundle's label says: all names in a calm view (like the mock-up) or when focused,
 * the active function's names when hot, else in a busy view only a quiet call count. */
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
    if (raw) showDetails({ type: "edge", raw });
    scheduleFan();
  });
  hit.addEventListener("mouseleave", () => { state.hotBundle = null; renderSelected(); scheduleFan(); });
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
  const group = svgEl("g", { class: `face k-${raw.kind}${node.hasClass("dim") ? " dim" : ""}` }, layer);
  drawBadges(group, box, boxBadges(raw));
  drawWarnMark(group, box, raw.warnings || []);
  if (!isExpandable(raw)) return;
  const open = box.open;
  const top = box.y1 + box.top;
  svgEl("rect", { x: box.x1 + 1, y: top + 1, width: box.w - 2, height: box.headH - 2, rx: 6, class: "cls-head" }, group);
  drawTypeEdge(group, box, raw);
  drawFaceText(group, box, node, top);
  drawToggle(group, box, raw, open, top + 16.5);  // centred on the name line
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
  const lists = listsOf(box);
  const fnList = svgEl("g", { class: "fn-list" }, group);
  drawListBack(fnList, box, lists.fn);
  lists.fn.items.slice(lists.fn.offset, lists.fn.offset + lists.fn.rows).forEach((f, i) => {
    drawFnRow(fnList, box, raw, f, lists.fn.top + i * box.fnRow);
  });
  drawScroll(fnList, box, lists.fn, offLines(raw.id, lists.fn));
  if (openParts(raw.id).functions.length > FAN.filterAt) drawFilter(group, box, raw.id);
  if (!lists.field.items.length) return;
  const sepY = lists.field.top - FAN.fieldGap / 2 + 4;
  svgEl("line", { x1: box.x1 + 10, x2: box.x2 - 10, y1: sepY, y2: sepY, class: "fld-sep" }, group);
  const fields = svgEl("g", { class: "field-list" }, group);
  const fieldList = { ...lists.field, top: sepY };
  drawListBack(fields, box, fieldList);
  lists.field.items.slice(fieldList.offset, fieldList.offset + fieldList.rows).forEach((f, i) => {
    box.fieldLines[fieldText(f)].forEach((line, n) => {
      svgEl("text", { x: box.x1 + 16, y: sepY + 16 + i * box.fieldRow + n * FAN.fieldLine, class: "fld" }, fields).textContent = line;
    });
  });
  drawScroll(fields, box, fieldList, null);
}

function drawFnRow(layer, box, raw, f, y) {
  const active = state.activeFn?.cls === raw.id && state.activeFn?.fn === f.name;
  const row = svgEl("g", { class: `fn${active ? " active" : ""}` }, layer);
  const hit = svgEl("rect", { x: box.x1 + 1, y, width: box.w - 2, height: box.fnRow, class: "fn-row",
    tabindex: 0, role: "button", "aria-label": `${raw.name}.${f.name}` }, row);
  const lines = box.fnLines[f.name];
  lines.forEach((line, n) => {
    const text = svgEl("text", { x: box.x1 + 16, y: y + 16 + n * FAN.rowLine, class: "fn-name" }, row);
    const last = n === lines.length - 1 && line.endsWith("()");
    text.textContent = last ? line.slice(0, -2) : line;
    if (last) svgEl("tspan", { class: "paren" }, text).textContent = "()";
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
  for (const l of state.current?.fanout || []) {
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
  const fo = svgEl("foreignObject", { x: box.x1 + 10, y: box.y1 + box.top + box.headH - 2, width: box.w - 20, height: 24 }, group);
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
  showDetails(item || state.selected);
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
  $("details").innerHTML = `
    <h3>${esc(nodeName(fn.cls))}#${esc(fn.fn)}()</h3>
    <div class="sub">function${fn.line ? ` · line ${fn.line}` : ""}</div>
    <dl>
      ${noteFields(fn)}
      ${field("calls", `Calls (${links.out.length})`, fnCallList(links.out, "out"))}
      ${field("called_by", `Called by (${links.in.length})`, fnCallList(links.in, "in"))}
    </dl>`;
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
  const ids = (state.current?.nodes || []).filter(isExpandable).map((n) => n.id);
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
  $("fanFront").addEventListener("wheel", forwardWheel, { passive: false });
  cy.on("viewport", syncFanViewport);
  cy.on("position", "node", scheduleFan);
}
