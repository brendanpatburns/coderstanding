// Coderstanding auto layout: a small layered (Sugiyama-style) layout. Placement only, no graph logic.
//
// - Bands, stacked top to bottom: one per level (level 1 at the top), so a low detail level stays
//   compact while every class keeps one spot at every level. Scenes join the band of the script
//   they hold. Outside code gets its own grid band at the right of the top band.
// - Inside a band: columns by call depth (callers left, callees right; cycles broken), rows
//   ordered by barycenter sweeps to cut crossings, tall columns folded into side-by-side columns.
// - Fixed spots (saved or pinned) never move; free classes are placed around them.
//
// Input nodes: {id, w, h, level, kind}; edges: {from, to, kinds}. Output: Map id -> {x, y}.
(function () {
"use strict";

const LAYOUT = {
  columnGap: 150,     // room for a call-name label between columns
  rowGap: 52,         // room between stacked boxes (and their labels)
  bandGap: 120,
  minRows: 6,         // a band column holds at least this many rows before it folds
  rowsPerRoot: 0.9,   // ...or this times the square root of the band's size (wider than tall)
  sweeps: 8,          // crossing-minimisation passes
  sweepLimit: 10000,  // above this many nodes, skip the sweeps (stay fast on huge graphs)
  nearRatio: 0.25,
  toolRows: 8,        // tools column height before it folds (further left)
  outsideGap: 40,     // outside code sits this far right of its user    // fewer free than this share of fixed: place each next to its neighbours
  defaultBox: { w: 120, h: 30 },
};

// ---------- small helpers ----------

function groupBy(items, keyOf) {
  const groups = new Map();
  for (const item of items) {
    const key = keyOf(item);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  }
  return groups;
}

function mean(values) {
  return values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
}

/** Edges whose ends are both in `ids`, without self loops. */
function edgesWithin(edges, ids) {
  return edges.filter((e) => e.from !== e.to && ids.has(e.from) && ids.has(e.to));
}

/** One edge per direction between two nodes (the same line can come from several levels). */
function uniqueEdges(edges) {
  const seen = new Map();
  for (const e of edges) if (!seen.has(`${e.from}\n${e.to}`)) seen.set(`${e.from}\n${e.to}`, e);
  return [...seen.values()];
}

/** id -> [neighbour ids], both directions. */
function neighbourMap(nodes, edges) {
  const map = new Map(nodes.map((n) => [n.id, []]));
  for (const e of edges) {
    map.get(e.from)?.push(e.to);
    map.get(e.to)?.push(e.from);
  }
  return map;
}

// ---------- bands ----------

/** Scenes sit in the band of the lowest-level class they hold, so they stay near their script. */
function bandOf(node, byId, edges) {
  if (node.kind === "external") return "external";
  if (node.kind !== "scene") return node.level;
  const held = edges.filter((e) => e.from === node.id && (e.kinds || []).includes("composition"))
    .map((e) => byId.get(e.to)).filter((n) => n && n.kind !== "external");
  return Math.min(node.level, ...held.map((n) => n.level));
}

// ---------- layering (columns) ----------

/** Depth-first order and the edges that close a cycle (dropped for layering). */
function depthFirst(nodes, edges) {
  const out = groupBy(edges, (e) => e.from);
  const state = new Map();  // id -> 1 on the stack, 2 done
  const order = [];
  const back = new Set();
  for (const root of nodes) {
    if (state.has(root.id)) continue;
    const stack = [[root.id, 0]];
    state.set(root.id, 1);
    order.push(root.id);
    while (stack.length) {
      const top = stack[stack.length - 1];
      const next = (out.get(top[0]) || [])[top[1]++];
      if (!next) { state.set(top[0], 2); stack.pop(); continue; }
      if (state.get(next.to) === 1) { back.add(next); continue; }
      if (state.has(next.to)) continue;
      state.set(next.to, 1);
      order.push(next.to);
      stack.push([next.to, 0]);
    }
  }
  return { order, back };
}

/**
 * Breadth-first layers: a class sits one column right of its nearest caller. Far more compact
 * than longest-path layers on hub-heavy code (one main class calling everything). Classes only
 * reachable inside a cycle start a new search from the first of them in depth-first order.
 */
function layersOf(nodes, edges) {
  const { order, back } = depthFirst(nodes, edges);
  const out = groupBy(edges, (e) => e.from);
  const hasCaller = new Set(edges.filter((e) => !back.has(e)).map((e) => e.to));
  const layer = new Map();
  const search = (roots) => {
    const queue = roots.filter((id) => !layer.has(id));
    queue.forEach((id) => layer.set(id, 0));
    for (let i = 0; i < queue.length; i++) {
      for (const e of out.get(queue[i]) || []) {
        if (layer.has(e.to)) continue;
        layer.set(e.to, layer.get(queue[i]) + 1);
        queue.push(e.to);
      }
    }
  };
  search(order.filter((id) => !hasCaller.has(id)));
  order.forEach((id) => search([id]));
  return { layer, order };
}

/** A band's columns, layered by the lines inside the band only, in depth-first order. */
function columnsOf(bandIds, graph) {
  const inBand = new Set(bandIds);
  const nodes = bandIds.map((id) => graph.byId.get(id));
  const starts = nodes.filter(isStart);  // the app's start leads, so cycles never put it mid-band
  const { layer, order } = layersOf([...starts, ...nodes.filter((n) => !isStart(n))], edgesWithin(graph.edges, inBand));
  const byLayer = groupBy(order, (id) => layer.get(id));
  return [...byLayer.keys()].sort((a, b) => a - b).map((key) => byLayer.get(key));
}

// ---------- ordering (rows) ----------

/** Barycenter sweeps, left to right then back: each node moves toward its neighbours' rows. */
function orderColumns(columns, neighbours, sweeps) {
  const column = new Map();
  const rank = new Map();
  const setRanks = (col) => col.forEach((id, i) => rank.set(id, (i + 0.5) / col.length));
  columns.forEach((col, c) => { col.forEach((id) => column.set(id, c)); setRanks(col); });
  const sortColumn = (c, isSide) => {
    const key = new Map(columns[c].map((id) => {
      const ranks = neighbours.get(id).filter((m) => column.has(m) && isSide(column.get(m)))
        .map((m) => rank.get(m));
      return [id, ranks.length ? mean(ranks) : rank.get(id)];
    }));
    columns[c].sort((a, b) => key.get(a) - key.get(b));
    setRanks(columns[c]);
  };
  for (let pass = 0; pass < sweeps; pass++) {
    if (pass % 2 === 0) for (let c = 1; c < columns.length; c++) sortColumn(c, (m) => m < c);
    else for (let c = columns.length - 2; c >= 0; c--) sortColumn(c, (m) => m > c);
  }
  return columns;
}

/** Fold a tall column into side-by-side columns, row by row, so the order still reads top to bottom. */
function foldColumn(col, maxRows) {
  const count = Math.ceil(col.length / maxRows);
  const folded = Array.from({ length: count }, () => []);
  col.forEach((id, i) => folded[i % count].push(id));
  return folded;
}

// ---------- coordinates ----------

/** Place columns left to right from `left`, each centred on `middle`; returns the band's box. */
function placeColumns(columns, byId, left, middle, spots) {
  const rowStep = Math.max(...columns.flat().map((id) => byId.get(id).h)) + LAYOUT.rowGap;
  let x = left;
  let top = middle;
  let bottom = middle;
  for (const col of columns) {
    const width = Math.max(...col.map((id) => byId.get(id).w));
    col.forEach((id, row) => {
      const y = middle + (row - (col.length - 1) / 2) * rowStep;
      spots.set(id, { x: x + width / 2, y });
    });
    top = Math.min(top, middle - (col.length / 2) * rowStep);
    bottom = Math.max(bottom, middle + (col.length / 2) * rowStep);
    x += width + LAYOUT.columnGap;
  }
  return { left, right: x - LAYOUT.columnGap, top, bottom };
}

/** Classes whose only lines go to outside code move to the last column, next to it. */
function nearTail(columns, graph, tail) {
  if (columns.length < 2) return columns;
  const last = columns.length - 1;
  const inBand = new Set(columns.flat());
  const onlyTail = (id) => {
    const others = graph.neighbours.get(id);
    return others.some((m) => tail.has(m)) && others.every((m) => tail.has(m) || !inBand.has(m));
  };
  const moving = columns.slice(0, last).flatMap((col) => col.filter(onlyTail));
  const moved = columns.map((col, c) => (c === last ? [...col, ...moving] : col.filter((id) => !onlyTail(id))));
  return moved.filter((col) => col.length);
}

/** A band's columns by layer, ordered to cut crossings. `tail` (outside code) joins the
 *  ordering as one last column, so outside code lines up with its users; returns its order. */
function orderBand(bandIds, graph, tail = []) {
  const sweeps = graph.nodes.length > LAYOUT.sweepLimit ? 0 : LAYOUT.sweeps;
  const columns = tail.length ? nearTail(columnsOf(bandIds, graph), graph, new Set(tail)) : columnsOf(bandIds, graph);
  if (tail.length) columns.push([...tail].sort());
  orderColumns(columns, graph.neighbours, sweeps);
  return { columns: tail.length ? columns.slice(0, -1) : columns, tail: tail.length ? columns.at(-1) : [] };
}

/** Place a band's columns, tall ones folded side by side. */
function layoutBand(columns, graph, left, middle, spots) {
  const size = columns.reduce((sum, col) => sum + col.length, 0);
  const maxRows = Math.max(LAYOUT.minRows, Math.ceil(Math.sqrt(size) * LAYOUT.rowsPerRoot));
  return placeColumns(columns.flatMap((col) => foldColumn(col, maxRows)), graph.byId, left, middle, spots);
}

/** Outside code as a compact grid, in the order given, reading top to bottom. */
function layoutExternals(ids, graph, left, middle, spots) {
  const rows = Math.max(Math.min(ids.length, LAYOUT.minRows), Math.ceil(Math.sqrt(ids.length * 2)));
  return placeColumns(foldColumn(ids, rows), graph.byId, left, middle, spots);
}

// ---------- the layered layout ----------

function graphOf(nodes, edges) {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const inner = uniqueEdges(edgesWithin(edges, new Set(byId.keys())));
  return { nodes, byId, edges: inner, neighbours: neighbourMap(nodes, inner) };
}

/** Layered spots for every node in `nodes` (ignores fixed spots). */
function layered(nodes, edges) {
  const graph = graphOf(nodes, edges);
  const bands = groupBy(nodes, (n) => (isTool(n) ? "tools" : bandOf(n, graph.byId, graph.edges)));
  const idsOf = (key) => (bands.get(key) || []).map((n) => n.id);
  const levels = [...bands.keys()].filter((k) => k !== "external" && k !== "tools").sort((a, b) => a - b);
  const spots = new Map();
  const external = idsOf("external");
  let top = 0;
  levels.forEach((level, i) => {
    const ids = idsOf(level);
    const { columns, tail } = orderBand(ids, graph, i === 0 ? external : []);
    const box = stackBand(ids, top, (left) => layoutBand(columns, graph, left, 0, spots), 0, spots);
    const bottom = i === 0 ? Math.max(box.bottom, stackExternals(tail, box.right)) : box.bottom;
    top = bottom + LAYOUT.bandGap;
  });
  if (!levels.length) stackExternals(external.sort(), -LAYOUT.bandGap);
  stackTools(idsOf("tools").sort((a, b) => graph.byId.get(a).level - graph.byId.get(b).level || (a < b ? -1 : 1)));
  return spots;

  /** Tools (scripts run by hand) in a loose column left of every band. */
  function stackTools(ids) {
    if (!ids.length) return;
    const columns = foldColumn(ids, LAYOUT.toolRows);
    const width = columns.reduce((sum, col) => sum + Math.max(...col.map((id) => graph.byId.get(id).w)) + LAYOUT.columnGap, 0);
    stackBand(ids, 0, (left) => placeColumns(columns, graph.byId, left, 0, spots), -width, spots);
  }

  function stackExternals(ids, right) {
    const place = (left) => layoutExternals(ids, graph, left, 0, spots);
    return stackBand(ids, 0, place, right + LAYOUT.bandGap, spots).bottom;
  }
}

/** Lay out one band with `place`, then move it so its top edge is at `top`; returns its box. */
function stackBand(ids, top, place, left, spots) {
  if (!ids.length) return { left, right: left, top, bottom: top };
  const box = place(left);
  const dy = top - box.top;
  ids.forEach((id) => { spots.get(id).y += dy; });
  return { ...box, top, bottom: box.bottom + dy };
}

// ---------- fixed spots ----------

function boxOf(node, spot) {
  const { w, h } = node || LAYOUT.defaultBox;
  return { x1: spot.x - w / 2, x2: spot.x + w / 2, y1: spot.y - h / 2, y2: spot.y + h / 2 };
}

/** Boxes already taken, bucketed by a coarse grid so overlap checks stay cheap. */
class Occupied {
  constructor(cell = 200) { this.cell = cell; this.buckets = new Map(); }
  cells(box) {
    const keys = [];
    for (let cx = Math.floor(box.x1 / this.cell); cx <= Math.floor(box.x2 / this.cell); cx++) {
      for (let cy = Math.floor(box.y1 / this.cell); cy <= Math.floor(box.y2 / this.cell); cy++) {
        keys.push(`${cx},${cy}`);
      }
    }
    return keys;
  }
  add(box) {
    for (const key of this.cells(box)) {
      if (!this.buckets.has(key)) this.buckets.set(key, []);
      this.buckets.get(key).push(box);
    }
  }
  hits(box, margin) {
    return this.cells(box).some((key) => (this.buckets.get(key) || []).some((b) => rectsOverlap(box, b, margin)));
  }
  /** How many taken boxes `box` touches (a box in several cells counts once). */
  count(box, margin) {
    const seen = new Set();
    for (const key of this.cells(box)) {
      for (const b of this.buckets.get(key) || []) if (rectsOverlap(box, b, margin)) seen.add(b);
    }
    return seen.size;
  }
}

/** Move `spot` up or down in growing steps until its box is clear of everything taken. */
function clearSpot(node, spot, taken) {
  const step = (node || LAYOUT.defaultBox).h + LAYOUT.rowGap / 2;
  for (let k = 0; k < 400; k++) {
    const dy = (k % 2 ? -1 : 1) * Math.ceil(k / 2) * step;
    const tryAt = { x: spot.x, y: spot.y + dy };
    if (!taken.hits(boxOf(node, tryAt), LAYOUT.rowGap / 3)) return tryAt;
  }
  return spot;
}

/** A spot next to a new class's placed neighbours: right of its callers, left of its callees. */
function nearSpot(node, edges, fixed, fallback) {
  const callers = edges.filter((e) => e.to === node.id && fixed.has(e.from)).map((e) => fixed.get(e.from));
  const callees = edges.filter((e) => e.from === node.id && fixed.has(e.to)).map((e) => fixed.get(e.to));
  const all = [...callers, ...callees];
  if (!all.length) return fallback;
  const shift = (callers.length >= callees.length ? 1 : -1) * (node.w + LAYOUT.columnGap);
  return { x: mean(all.map((s) => s.x)) + shift, y: mean(all.map((s) => s.y)) };
}

/** Below everything fixed, for new classes with no placed neighbours. */
function belowFixed(fixed) {
  const ys = [...fixed.values()].map((s) => s.y);
  const xs = [...fixed.values()].map((s) => s.x);
  return { x: Math.min(...xs), y: Math.max(...ys) + LAYOUT.bandGap };
}

/**
 * Spots for every node: fixed ones as given, the rest laid out around them.
 * `fixed` maps id -> {x, y} (saved or pinned spots, or spots already in use).
 */
function layout(nodes, edges, fixed = new Map(), options = {}) {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const users = usersOf(nodes, edges, byId);
  const beside = nodes.filter((n) => !fixed.has(n.id) && users.has(n.id));  // outside code with users
  const inner = nodes.filter((n) => !users.has(n.id) || fixed.has(n.id));
  const free = inner.filter((n) => !fixed.has(n.id));
  const taken = new Occupied();
  for (const [id, spot] of fixed) taken.add(boxOf(byId.get(id), spot));
  const near = options.near !== false && fixed.size > 0 && free.length <= fixed.size * LAYOUT.nearRatio;
  const proposed = near || !free.length ? null : propose(options.method || METHODS[0], inner, free, edges, fixed, options);
  const spots = new Map(fixed);
  const fallback = fixed.size ? belowFixed(fixed) : { x: 0, y: 0 };
  for (const node of free) {
    const wanted = near ? nearSpot(node, edges, spots, fallback) : proposed.get(node.id);
    const spot = fixed.size ? clearSpot(node, wanted, taken) : wanted;
    taken.add(boxOf(node, spot));
    spots.set(node.id, spot);
  }
  for (const node of beside.sort((a, b) => (a.id < b.id ? -1 : 1))) {
    const spot = clearSpot(node, besideUser(node, users.get(node.id), spots), taken);
    taken.add(boxOf(node, spot));
    spots.set(node.id, spot);
  }
  return spots;
}

/** Outside code id -> its users (classes with a line to it), lowest level first. */
function usersOf(nodes, edges, byId) {
  const users = new Map();
  for (const e of edges) {
    for (const [ext, user] of [[e.to, e.from], [e.from, e.to]]) {
      const a = byId.get(ext);
      const b = byId.get(user);
      if (a?.kind !== "external" || !b || b.kind === "external") continue;
      if (!users.has(ext)) users.set(ext, new Set());
      users.get(ext).add(b);
    }
  }
  for (const [id, set] of users) {
    users.set(id, [...set].sort((a, b) => (a.level ?? 0) - (b.level ?? 0) || (a.id < b.id ? -1 : 1)));
  }
  return users;
}

/** Right beside the first user (lowest level), so outside code reads as part of its user. */
function besideUser(node, users, spots) {
  const user = users[0];
  const s = spots.get(user.id);
  return { x: s.x + user.w / 2 + LAYOUT.outsideGap + node.w / 2, y: s.y };
}

/**
 * Spots built up level by level, so every class keeps one spot at every level and the low
 * levels (the app's start, hubs, outside code by its users) are laid out on their own instead
 * of inheriting a crowded corner of the full view. `steps` = [{nodes, edges}] per detail level,
 * lowest first (each a drawn view); classes placed by an earlier step stay fixed in later ones.
 */
function layoutByLevels(steps, fixed = new Map(), options = {}) {
  const method = pickMethod(options.method, steps.at(-1)?.nodes.length || 0).method;
  if (method === "layered" || method === "grid") {  // already one band per level
    const nodes = [...new Map(steps.flatMap((step) => step.nodes).map((n) => [n.id, n])).values()];
    return layout(nodes, steps.flatMap((step) => step.edges), fixed, { ...options, method, near: false });
  }
  const spots = new Map(fixed);
  for (const [i, step] of steps.entries()) {
    const last = i === steps.length - 1;
    const linked = new Set(step.edges.flatMap((e) => [e.from, e.to]));
    const nodes = step.nodes.filter((n) => last || n.kind !== "external" || linked.has(n.id));  // wait for its user
    const edges = step.edges;
    if (!nodes.some((n) => !spots.has(n.id))) continue;
    const kept = new Map(nodes.filter((n) => spots.has(n.id)).map((n) => [n.id, spots.get(n.id)]));
    const { method } = pickMethod(options.method, nodes.length);
    const placed = layout(nodes, edges, kept, { ...options, method, near: false });
    for (const [id, spot] of placed) if (!spots.has(id)) spots.set(id, spot);
  }
  return spots;
}

// ---------- layout methods: organic (force), layered, hub (radial), grid ----------

const METHODS = ["layered", "organic", "hub", "grid"];  // the first is the default
// Distances scale with box size: `sizeUnit` is a typical box's half-width plus height, and
// boxes keep at least `minGap` between them (room for a line and its label).
const FORCE = { slowAbove: 1500, iterations: 320, budget: 4e7, edgeLength: 260, outsideLength: 70,
  repulsion: 60000, reach: 900, spring: 0.03, outsideSpring: 0.2, gravity: 0.008, leftPull: 0.05, leftGap: 300,
  startPull: 0.02, maxStep: 60, overlapSweeps: 80, gap: 60, sizeUnit: 140, minGap: { x: 80, y: 60 } };
const HUB = { ringGap: 230, gap: 40 };
const GRID_GAP = { x: 40, y: 36 };
const START_FLAGS = ["godot-main", "program-main", "godot-main-scene", "entry"];

/** The method that will run: force layouts get slow on big graphs, so they fall back to grid. */
function pickMethod(requested, nodeCount) {
  const method = METHODS.includes(requested) ? requested : METHODS[0];
  if (["organic", "hub"].includes(method) && nodeCount > FORCE.slowAbove) return { method: "grid", fellBack: true };
  return { method, fellBack: false };
}

function propose(method, nodes, free, edges, fixed, options) {
  if (method === "organic") return organic(nodes, edges, fixed);
  if (method === "hub") return hub(nodes, edges, fixed, options.center, options.origin);
  if (method === "grid") return grid(free);
  return layered(free, edges);
}

/** Standalone tools (scripts run by hand) sit in a loose column on the far left. */
function isTool(node) {
  return (node.flags || []).includes("script-main");
}

/** A small seeded number in [-0.5, 0.5) from a string, so ties break the same way every time. */
function jitter(text, salt) {
  let h = 2166136261 ^ salt;
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 16777619);
  return ((h >>> 0) % 1000) / 1000 - 0.5;
}

/** Gap between two boxes along the line of their centres (negative when they overlap). */
function boxGap(a, b, dx, dy, dist) {
  const ux = Math.abs(dx) / dist;
  const uy = Math.abs(dy) / dist;
  const reach = (n) => Math.min(n.w / 2 / (ux || 1e-9), n.h / 2 / (uy || 1e-9));
  return dist - reach(a) - reach(b);
}

/**
 * Organic: a force layout (springs on lines, boxes push apart by the gap between them) started
 * from the layered spots, so the same graph always gives the same picture. Lines to outside
 * code are short and stiff (outside code sits by its users); tools are pulled to the far left;
 * fixed spots never move.
 */
function organic(nodes, edges, fixed) {
  const graph = graphOf(nodes, edges);
  const start = layered(nodes, edges);
  const pos = new Map(nodes.map((n) => [n.id, fixed.get(n.id) || startSpot(n, graph, fixed, start)]));
  const steps = Math.max(40, Math.min(FORCE.iterations, Math.floor(FORCE.budget / Math.max(1, nodes.length ** 2))));
  for (let step = 0; step < steps; step++) {
    const heat = FORCE.maxStep * (1 - step / steps) + 1;
    const force = new Map(nodes.map((n) => [n.id, { x: 0, y: 0 }]));
    repel(nodes, pos, force);
    pull(graph, pos, force);
    centre(nodes, pos, force);
    for (const n of nodes) {
      if (fixed.has(n.id)) continue;
      const f = force.get(n.id);
      const len = Math.hypot(f.x, f.y) || 1;
      const move = Math.min(heat, len);
      const p = pos.get(n.id);
      pos.set(n.id, { x: p.x + (f.x / len) * move, y: p.y + (f.y / len) * move });
    }
  }
  separate(nodes, pos, fixed, FORCE.minGap);
  return pos;
}

/** Where a free class starts: by its fixed neighbours if it has any, else its layered spot. */
function startSpot(node, graph, fixed, start) {
  const placed = graph.neighbours.get(node.id).filter((m) => fixed.has(m)).map((m) => fixed.get(m));
  if (!placed.length) {
    const s = start.get(node.id);
    return { x: s.x * 1.15 + jitter(node.id, 1), y: s.y * 1.15 + jitter(node.id, 2) };
  }
  const reach = node.kind === "external" ? FORCE.outsideLength + node.w : FORCE.edgeLength;
  return { x: mean(placed.map((s) => s.x)) + jitter(node.id, 1) * reach * 2,
    y: mean(placed.map((s) => s.y)) + jitter(node.id, 2) * reach * 2 };
}

/** How much room a box asks for, relative to a typical box. */
function sizeOf(node) {
  return (node.w / 2 + node.h) / FORCE.sizeUnit;
}

function repel(nodes, pos, force) {
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const a = nodes[i];
      const b = nodes[j];
      const p = pos.get(a.id);
      const q = pos.get(b.id);
      const dx = p.x - q.x;
      const dy = p.y - q.y;
      const dist = Math.hypot(dx, dy) || 0.01;
      const gap = Math.max(4, boxGap(a, b, dx, dy, dist));
      if (gap > FORCE.reach) continue;
      const push = FORCE.repulsion * sizeOf(a) * sizeOf(b) / (gap * gap) * (gap < FORCE.gap ? 3 : 1);
      const fa = force.get(a.id);
      const fb = force.get(b.id);
      fa.x += (dx / dist) * push; fa.y += (dy / dist) * push;
      fb.x -= (dx / dist) * push; fb.y -= (dy / dist) * push;
    }
  }
}

function pull(graph, pos, force) {
  for (const e of graph.edges) {
    const a = graph.byId.get(e.from);
    const b = graph.byId.get(e.to);
    const outside = a.kind === "external" || b.kind === "external";
    const p = pos.get(a.id);
    const q = pos.get(b.id);
    const dx = q.x - p.x;
    const dy = q.y - p.y;
    const dist = Math.hypot(dx, dy) || 0.01;
    const want = (outside ? FORCE.outsideLength : FORCE.edgeLength) + (e.label || 0) + (a.w + b.w) / 4;
    const k = (outside ? FORCE.outsideSpring : FORCE.spring) * (dist - want);
    const fa = force.get(a.id);
    const fb = force.get(b.id);
    fa.x += (dx / dist) * k; fa.y += (dy / dist) * k;
    fb.x -= (dx / dist) * k; fb.y -= (dy / dist) * k;
  }
}

function isStart(node) {
  return !isTool(node) && START_FLAGS.some((flag) => (node.flags || []).includes(flag));
}

/** A weak pull to the middle keeps loose parts near; the app's start leans left of centre and
 *  tools are pulled left of everything. */
function centre(nodes, pos, force) {
  const placed = nodes.filter((n) => !isTool(n)).map((n) => pos.get(n.id));
  if (!placed.length) return;
  const mid = { x: mean(placed.map((p) => p.x)), y: mean(placed.map((p) => p.y)) };
  const xs = placed.map((p) => p.x);
  const minX = Math.min(...xs);
  const leftOfCentre = mid.x - (mid.x - minX) * 0.35;
  for (const n of nodes) {
    const p = pos.get(n.id);
    const f = force.get(n.id);
    f.y += (mid.y - p.y) * FORCE.gravity * 1.6;  // a little wider than tall
    if (isTool(n)) f.x += (minX - FORCE.leftGap - p.x) * FORCE.leftPull;
    else if (isStart(n)) f.x += (leftOfCentre - p.x) * FORCE.startPull;
    else f.x += (mid.x - p.x) * FORCE.gravity;
  }
}

/** Push overlapping boxes apart (fixed ones stay) until they keep `gap` ({x, y}) between them. */
function separate(nodes, pos, fixed, gap) {
  for (let sweep = 0; sweep < FORCE.overlapSweeps; sweep++) {
    let moved = false;
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i];
        const b = nodes[j];
        const p = pos.get(a.id);
        const q = pos.get(b.id);
        const ox = (a.w + b.w) / 2 + gap.x - Math.abs(p.x - q.x);
        const oy = (a.h + b.h) / 2 + gap.y - Math.abs(p.y - q.y);
        if (ox <= 0 || oy <= 0) continue;
        moved = true;
        const alongX = ox < oy;
        const sign = (alongX ? p.x - q.x : p.y - q.y) >= 0 ? 1 : -1;
        const d = (alongX ? ox : oy) * sign;
        const share = fixed.has(a.id) ? 0 : fixed.has(b.id) ? 1 : 0.5;
        if (!fixed.has(a.id)) pos.set(a.id, alongX ? { x: p.x + d * share, y: p.y } : { x: p.x, y: p.y + d * share });
        if (!fixed.has(b.id)) pos.set(b.id, alongX ? { x: q.x - d * (1 - share), y: q.y } : { x: q.x, y: q.y - d * (1 - share) });
      }
    }
    if (!moved) return;
  }
}

/** The class a hub layout centres on: the given one, else the app's start, else the busiest. */
function hubCentre(nodes, edges, wanted) {
  if (wanted && nodes.some((n) => n.id === wanted)) return wanted;
  for (const flag of START_FLAGS) {
    const found = nodes.find((n) => (n.flags || []).includes(flag) && !isTool(n));
    if (found) return found.id;
  }
  const degree = new Map(nodes.map((n) => [n.id, 0]));
  edges.forEach((e) => { degree.set(e.from, (degree.get(e.from) || 0) + 1); degree.set(e.to, (degree.get(e.to) || 0) + 1); });
  return [...degree].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1))[0]?.[0];
}

/** Hub: rings by line distance from the centre class (which stays at `at[id]` when given, e.g.
 *  where it is drawn now); each ring ordered by its parents' angles. */
function hub(nodes, edges, fixed, wanted, at) {
  const graph = graphOf(nodes, edges);
  const middle = hubCentre(nodes, graph.edges, wanted);
  const ring = new Map([[middle, 0]]);
  const queue = [middle];
  for (let i = 0; i < queue.length; i++) {
    for (const m of [...graph.neighbours.get(queue[i])].sort()) {
      if (!ring.has(m)) { ring.set(m, ring.get(queue[i]) + 1); queue.push(m); }
    }
  }
  const last = Math.max(0, ...ring.values()) + 1;
  nodes.forEach((n) => { if (!ring.has(n.id)) ring.set(n.id, last); });
  const origin = fixed.get(middle) || at?.[middle] || { x: 0, y: 0 };
  const angle = new Map([[middle, 0]]);
  const spots = new Map([[middle, origin]]);
  let radius = 0;
  const rings = groupBy([...ring.keys()].filter((id) => id !== middle), (id) => ring.get(id));
  for (const k of [...rings.keys()].sort((a, b) => a - b)) {
    const ids = rings.get(k);
    const parentAngle = (id) => mean(graph.neighbours.get(id).filter((m) => angle.has(m) && ring.get(m) < k).map((m) => angle.get(m))) ?? 0;
    ids.sort((a, b) => parentAngle(a) - parentAngle(b) || (a < b ? -1 : 1));
    const around = ids.reduce((sum, id) => sum + graph.byId.get(id).w + HUB.gap, 0);
    radius = Math.max(radius + HUB.ringGap, around / (2 * Math.PI));
    ids.forEach((id, i) => {
      const a = (i / ids.length) * 2 * Math.PI - Math.PI / 2;
      angle.set(id, a);
      spots.set(id, { x: origin.x + Math.cos(a) * radius * 1.25, y: origin.y + Math.sin(a) * radius });
    });
  }
  return spots;
}

/** Grid: compact rows, by level then name, for very big graphs. */
function grid(nodes) {
  const sorted = [...nodes].sort((a, b) => (a.level ?? 0) - (b.level ?? 0) || (a.id < b.id ? -1 : 1));
  const columns = Math.max(1, Math.ceil(Math.sqrt(sorted.length * 1.6)));
  const cellW = Math.max(LAYOUT.defaultBox.w, ...sorted.map((n) => n.w)) + GRID_GAP.x;
  const cellH = Math.max(LAYOUT.defaultBox.h, ...sorted.map((n) => n.h)) + GRID_GAP.y;
  return new Map(sorted.map((n, i) => [n.id, { x: (i % columns) * cellW, y: Math.floor(i / columns) * cellH }]));
}

// ---------- lines: bends around boxes, label spots (rects are {x1, y1, x2, y2}) ----------

// A detour may be at most `maxStretch` times the straight line (plus `slack`): longer ones
// read as lines running off to nothing, so the line stays straight over the box instead.
const ROUTE = { margin: 10, step: 34, maxOffset: 420, maxStretch: 1.4, slack: 60 };
const LABEL = { pad: 3, along: [0.5, 0.4, 0.6, 0.3, 0.7, 0.22, 0.78, 0.14, 0.86], sideSteps: 3 };

function rectsOverlap(a, b, margin = 0) {
  return a.x1 < b.x2 + margin && b.x1 < a.x2 + margin && a.y1 < b.y2 + margin && b.y1 < a.y2 + margin;
}

/** Does the segment p-q pass through `rect` (grown by `margin`)? Liang-Barsky clipping. */
function segmentHitsRect(p, q, rect, margin = 0) {
  const dx = q.x - p.x;
  const dy = q.y - p.y;
  let lo = 0;
  let hi = 1;
  const sides = [[-dx, p.x - (rect.x1 - margin)], [dx, rect.x2 + margin - p.x],
    [-dy, p.y - (rect.y1 - margin)], [dy, rect.y2 + margin - p.y]];
  for (const [step, room] of sides) {
    if (step === 0) { if (room < 0) return false; continue; }
    const t = room / step;
    if (step < 0) lo = Math.max(lo, t); else hi = Math.min(hi, t);
    if (lo > hi) return false;
  }
  return true;
}

function polylineHits(points, rects, margin) {
  for (let i = 1; i < points.length; i++) {
    if (rects.some((r) => segmentHitsRect(points[i - 1], points[i], r, margin))) return true;
  }
  return false;
}

function polylineLength(points) {
  let length = 0;
  for (let i = 1; i < points.length; i++) length += Math.hypot(points[i].x - points[i - 1].x, points[i].y - points[i - 1].y);
  return length;
}

/** The point at share `t` (0..1) of a polyline's length, and the direction there. */
function pointAlong(points, t) {
  let left = polylineLength(points) * t;
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1];
    const b = points[i];
    const length = Math.hypot(b.x - a.x, b.y - a.y);
    if (left <= length || i === points.length - 1) {
      const f = length ? Math.min(1, left / length) : 0;
      return { x: a.x + (b.x - a.x) * f, y: a.y + (b.y - a.y) * f, dx: (b.x - a.x) / (length || 1), dy: (b.y - a.y) / (length || 1) };
    }
    left -= length;
  }
  return { ...points[0], dx: 1, dy: 0 };
}

/** Detours for a line a-b: one bend sideways from the middle, or a channel above/below a blocker. */
function detours(a, b, blockers) {
  const length = Math.hypot(b.x - a.x, b.y - a.y) || 1;
  const normal = { x: -(b.y - a.y) / length, y: (b.x - a.x) / length };
  const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
  const routes = [];
  for (let off = ROUTE.step; off <= ROUTE.maxOffset; off += ROUTE.step) {
    for (const sign of [1, -1]) routes.push([{ x: mid.x + normal.x * off * sign, y: mid.y + normal.y * off * sign }]);
  }
  const at = (t) => ({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t });
  const [p1, p2] = [at(0.2), at(0.8)];
  for (const r of blockers) {  // channels past a blocker: above/below it, or left/right of it
    for (const y of [r.y1 - ROUTE.margin * 2, r.y2 + ROUTE.margin * 2]) routes.push([{ x: p1.x, y }, { x: p2.x, y }]);
    for (const x of [r.x1 - ROUTE.margin * 2, r.x2 + ROUTE.margin * 2]) routes.push([{ x, y: p1.y }, { x, y: p2.y }]);
  }
  return routes;
}

/**
 * Bend points (absolute) so the line from `a` to `b` misses every box in `boxes` (the two end
 * boxes left out by the caller), or [] when the straight line is already clear or no short
 * detour is. Picks the shortest clear detour.
 */
function routeAround(a, b, boxes) {
  const reach = ROUTE.maxOffset + ROUTE.margin;
  const near = boxes.filter((r) => r.x2 > Math.min(a.x, b.x) - reach && r.x1 < Math.max(a.x, b.x) + reach
    && r.y2 > Math.min(a.y, b.y) - reach && r.y1 < Math.max(a.y, b.y) + reach);
  const blockers = near.filter((r) => segmentHitsRect(a, b, r, ROUTE.margin));
  if (!blockers.length) return [];
  let best = null;
  const longest = Math.hypot(b.x - a.x, b.y - a.y) * ROUTE.maxStretch + ROUTE.slack;
  for (const bends of detours(a, b, blockers)) {
    const points = [a, ...bends, b];
    const length = polylineLength(points);
    if (length > longest || polylineHits(points, near, ROUTE.margin)) continue;
    if (!best || length < best.length) best = { bends, length };
  }
  return best ? best.bends : [];
}

/** Spots to try for a label on `path`: along the line first, then nudged off it either side. */
function labelCandidates(item) {
  const spots = [];
  for (let side = 0; side <= LABEL.sideSteps; side++) {
    for (const t of item.along || LABEL.along) {
      const p = pointAlong(item.path, t);
      for (const sign of side ? [-1, 1] : [0]) {
        const off = sign * side * (item.h + LABEL.pad);
        spots.push({ x: p.x - p.dy * off, y: p.y + p.dx * off });
      }
    }
  }
  return spots;
}

/**
 * Greedy label placement: each item {id, w, h, path: [{x, y}..], required} gets the first spot
 * along its line whose box misses `obstacles` and the labels placed before it. Returns
 * Map id -> {x, y} (label centre); an item with no clear spot is left out unless `required`,
 * which takes the spot with the fewest hits.
 */
function placeLabels(items, obstacles = []) {
  const taken = new Occupied();
  obstacles.forEach((r) => taken.add(r));
  const spots = new Map();
  const rectAt = (item, p) => ({ x1: p.x - item.w / 2, x2: p.x + item.w / 2, y1: p.y - item.h / 2, y2: p.y + item.h / 2 });
  for (const item of items) {
    let best = null;
    for (const spot of labelCandidates(item)) {
      const rect = rectAt(item, spot);
      const hits = item.required ? taken.count(rect, LABEL.pad) : Number(taken.hits(rect, LABEL.pad));
      if (!best || hits < best.hits) best = { spot, rect, hits };
      if (!hits) break;
    }
    if (best && (best.hits === 0 || item.required)) {
      spots.set(item.id, best.spot);
      taken.add(best.rect);
    }
  }
  return spots;
}

const CoderLayout = { layout, layoutByLevels, layered, LAYOUT, METHODS, pickMethod, hubCentre, foldColumn, routeAround, placeLabels,
  pointAlong, segmentHitsRect };
if (typeof module !== "undefined") module.exports = CoderLayout;
else window.CoderLayout = CoderLayout;
})();
