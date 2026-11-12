// Coderstanding auto layout: a small layered (Sugiyama-style) layout. Placement only, no graph logic.
//
// - Bands, stacked top to bottom: one per level (level 1 at the top), so a low detail level stays
//   compact while every class keeps one spot at every level. Scenes join the band of the script
//   they hold. Outside code gets its own grid band at the right of the top band.
// - Inside a band: columns by call depth (callers left, callees right; cycles broken), rows
//   ordered by barycenter sweeps to cut crossings (the best order kept), tall columns folded into
//   side-by-side columns by where their users sit above, rows lined up with their neighbours.
// - Each band below the first slides sideways to sit under the classes above that it uses.
// - Fixed spots (saved or pinned) never move; free classes are placed around them. Saved spots
//   whose boxes grew into each other are nudged apart first (settleFixed).
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
  sweepLimit: 10000,  // above this many nodes, skip the sweeps and row alignment (stay fast on huge graphs)
  alignSweeps: 4,     // row-alignment passes before the last both-sides pass
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

/** Pairs of lines that cross between neighbouring columns (lines that skip a column not counted). */
function adjacentCrossings(columns, neighbours) {
  let total = 0;
  for (let c = 0; c + 1 < columns.length; c++) {
    const next = new Map(columns[c + 1].map((id, i) => [id, i]));
    const ends = [];
    columns[c].forEach((id, i) => { for (const m of neighbours.get(id) || []) if (next.has(m)) ends.push([i, next.get(m)]); });
    ends.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    total += inversions(ends.map((pair) => pair[1]), columns[c + 1].length);
  }
  return total;
}

/** How many pairs i < j have values[i] > values[j] (values in 0..size-1), with a Fenwick tree. */
function inversions(values, size) {
  const tree = new Array(size + 1).fill(0);
  let count = 0;
  values.forEach((v, seen) => {
    let below = 0;  // values <= v seen so far
    for (let i = v + 1; i > 0; i -= i & -i) below += tree[i];
    count += seen - below;
    for (let i = v + 1; i <= size; i += i & -i) tree[i]++;
  });
  return count;
}

/** Barycenter sweeps, left to right then back: each node moves toward its neighbours' rows.
 *  Keeps the order with the fewest crossings seen (a later sweep can make it worse). */
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
  let best = { crossings: sweeps ? adjacentCrossings(columns, neighbours) : 0, order: columns.map((col) => [...col]) };
  for (let pass = 0; pass < sweeps && best.crossings; pass++) {
    if (pass % 2 === 0) for (let c = 1; c < columns.length; c++) sortColumn(c, (m) => m < c);
    else for (let c = columns.length - 2; c >= 0; c--) sortColumn(c, (m) => m > c);
    const crossings = adjacentCrossings(columns, neighbours);
    if (crossings < best.crossings) best = { crossings, order: columns.map((col) => [...col]) };
  }
  best.order.forEach((col, c) => { columns[c] = col; });
  return columns;
}

/** Fold a tall column into side-by-side columns, row by row, so the order still reads top to bottom. */
function foldColumn(col, maxRows) {
  const count = Math.ceil(col.length / maxRows);
  const folded = Array.from({ length: count }, () => []);
  col.forEach((id, i) => folded[i % count].push(id));
  return folded;
}

// ---------- rows ----------

/**
 * Whole-number positions closest to `targets` (least squares) that keep their order with at least
 * 1 between neighbours: isotonic regression (pool adjacent violators), then rounded, so every
 * column stays on one shared row grid.
 */
function spacedRows(targets) {
  const blocks = [];
  targets.forEach((t, i) => {
    let block = { sum: t - i, count: 1 };
    while (blocks.length && blocks.at(-1).sum / blocks.at(-1).count > block.sum / block.count) {
      const last = blocks.pop();
      block = { sum: last.sum + block.sum, count: last.count + block.count };
    }
    blocks.push(block);
  });
  const rows = [];
  for (const block of blocks) {
    const z = Math.round(block.sum / block.count);
    for (let k = 0; k < block.count; k++) rows.push(z + rows.length);
  }
  return rows;
}

function median(values) {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = sorted.length >> 1;
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

/**
 * Rows for ordered columns (Brandes-Köpf in spirit, much simpler): each box moves toward the
 * median row of its neighbours in the next columns (sweeps left to right and back, then both
 * sides at once), keeping the column order and one row between boxes. Lines between neighbouring
 * columns run level more often, with no new crossings. Returns id -> row (0 = the middle).
 */
function alignRows(columns, neighbours, sweeps = LAYOUT.alignSweeps) {
  const row = new Map();
  const column = new Map();
  columns.forEach((col, c) => col.forEach((id, i) => { row.set(id, i - (col.length - 1) / 2); column.set(id, c); }));
  const settle = (c, isSide) => {
    const col = columns[c];
    const targets = col.map((id) => {
      const rows = neighbours.get(id).filter((m) => column.has(m) && isSide(column.get(m))).map((m) => row.get(m));
      return rows.length ? median(rows) : row.get(id);
    });
    spacedRows(targets).forEach((r, i) => row.set(col[i], r));
  };
  for (let pass = 0; pass < sweeps; pass++) {
    if (pass % 2 === 0) for (let c = 1; c < columns.length; c++) settle(c, (m) => m === c - 1);
    else for (let c = columns.length - 2; c >= 0; c--) settle(c, (m) => m === c + 1);
  }
  for (let c = 0; c < columns.length; c++) settle(c, (m) => m === c - 1 || m === c + 1);
  return row;
}

// ---------- coordinates ----------

/** Place columns left to right from `left`; `rowOf(id, i, col)` gives a box's row (0 = `middle`;
 *  default: the column centred on `middle`). Returns the band's box. */
function placeColumns(columns, byId, left, middle, spots, rowOf = (id, i, col) => i - (col.length - 1) / 2) {
  const rowStep = Math.max(...columns.flat().map((id) => byId.get(id).h)) + LAYOUT.rowGap;
  let x = left;
  let top = Infinity;
  let bottom = -Infinity;
  for (const col of columns) {
    const width = Math.max(...col.map((id) => byId.get(id).w));
    col.forEach((id, i) => {
      const y = middle + rowOf(id, i, col) * rowStep;
      spots.set(id, { x: x + width / 2, y });
      top = Math.min(top, y - rowStep / 2);
      bottom = Math.max(bottom, y + rowStep / 2);
    });
    x += width + LAYOUT.columnGap;
  }
  if (top > bottom) top = bottom = middle;  // no boxes
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

/** Place a band's columns, tall ones folded side by side (by where the classes they use sit in
 *  earlier bands), rows lined up with their neighbours. */
function layoutBand(columns, graph, left, middle, spots) {
  const size = columns.reduce((sum, col) => sum + col.length, 0);
  const maxRows = Math.max(LAYOUT.minRows, Math.ceil(Math.sqrt(size) * LAYOUT.rowsPerRoot));
  const aboveX = xAbove(columns.flat(), graph, spots);
  const folded = columns.flatMap((col) => (aboveX.size ? foldUnder(col, maxRows, aboveX) : foldColumn(col, maxRows)));
  if (graph.nodes.length > LAYOUT.sweepLimit) return placeColumns(folded, graph.byId, left, middle, spots);
  const rows = alignRows(folded, graph.neighbours);
  return placeColumns(folded, graph.byId, left, middle, spots, (id) => rows.get(id));
}

/** id -> median x of its neighbours already placed (earlier bands; outside code left out). */
function xAbove(ids, graph, spots) {
  const xs = new Map();
  for (const id of ids) {
    const placed = graph.neighbours.get(id).filter((m) => spots.has(m) && graph.byId.get(m).kind !== "external");
    if (placed.length) xs.set(id, median(placed.map((m) => spots.get(m).x)));
  }
  return xs;
}

/** Fold a tall column into side-by-side columns by where the classes they use sit above (left
 *  ones left), each keeping the column's top-to-bottom order; classes with nothing above count
 *  as sitting at the middle of the others. */
function foldUnder(col, maxRows, aboveX) {
  const count = Math.ceil(col.length / maxRows);
  if (count < 2) return [col];
  const known = col.filter((id) => aboveX.has(id)).map((id) => aboveX.get(id));
  if (!known.length) return foldColumn(col, maxRows);
  const fallback = median(known);
  const key = new Map(col.map((id) => [id, aboveX.has(id) ? aboveX.get(id) : fallback]));
  const order = new Map(col.map((id, i) => [id, i]));
  const sorted = [...col].sort((a, b) => key.get(a) - key.get(b) || order.get(a) - order.get(b));
  const per = Math.ceil(col.length / count);
  return Array.from({ length: count }, (_, k) => sorted.slice(k * per, (k + 1) * per).sort((a, b) => order.get(a) - order.get(b)))
    .filter((part) => part.length);
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
    if (i > 0) shiftUnder(ids, graph, spots);
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
    const others = [...spots];
    const box = stackBand(ids, 0, (at) => placeColumns(columns, graph.byId, at, 0, spots), -width, spots);
    const beside = others.filter(([id, s]) => Math.abs(s.y - (box.top + box.bottom) / 2) < (box.bottom - box.top + graph.byId.get(id).h) / 2);
    const room = Math.min(0, ...beside.map(([id, s]) => s.x - graph.byId.get(id).w / 2)) - LAYOUT.columnGap - (box.right - box.left);
    ids.forEach((id) => { spots.get(id).x += Math.min(0, room - box.left); });  // clear of bands slid left
  }

  function stackExternals(ids, right) {
    const place = (left) => layoutExternals(ids, graph, left, 0, spots);
    return stackBand(ids, 0, place, right + LAYOUT.bandGap, spots).bottom;
  }
}

/** Slide a placed band sideways by the median gap to its neighbours in earlier bands, so lines
 *  between bands run as straight down as they can on the whole. */
function shiftUnder(ids, graph, spots) {
  const inBand = new Set(ids);
  const dxs = ids.flatMap((id) => graph.neighbours.get(id)
    .filter((m) => !inBand.has(m) && spots.has(m) && graph.byId.get(m).kind !== "external")
    .map((m) => spots.get(m).x - spots.get(id).x));
  if (!dxs.length) return;
  const dx = median(dxs);
  ids.forEach((id) => { spots.get(id).x += dx; });
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

/**
 * Saved spots nudged so no two of these boxes overlap (boxes grew since the spots were saved,
 * e.g. taller box faces): top to bottom, a box that overlaps one above it moves down to just
 * below it. Boxes that already fit stay where they are. `nodes` gives the sizes (closed boxes);
 * returns only the moved spots (id -> {x, y}).
 */
function settleFixed(nodes, fixed, gap = LAYOUT.rowGap / 3) {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const ids = [...fixed.keys()].filter((id) => byId.has(id))
    .sort((a, b) => fixed.get(a).y - fixed.get(b).y || fixed.get(a).x - fixed.get(b).x);
  const placed = [];
  const moved = new Map();
  for (const id of ids) {
    const node = byId.get(id);
    let spot = { x: fixed.get(id).x, y: fixed.get(id).y };
    for (let k = 0; k <= placed.length; k++) {
      const hits = placed.filter((b) => rectsOverlap(boxOf(node, spot), b));
      if (!hits.length) break;
      spot = { x: spot.x, y: Math.max(...hits.map((b) => b.y2)) + gap + node.h / 2 };
    }
    if (spot.y !== fixed.get(id).y) moved.set(id, spot);
    placed.push(boxOf(node, spot));
  }
  return moved;
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

// ---------- focus: a tour step's compact layout ----------

const FOCUS_LAYOUT = { columnGap: 110, rowGap: 34, minRows: 3 };

/** Which side of the focus a class goes: left when its lines mostly come into `towards`
 *  (it calls them), right when they mostly go out to it; ties and no lines: right. */
function sideOf(id, edges, towards) {
  let left = 0, right = 0;
  for (const e of edges) {
    if (e.from === id && towards.has(e.to)) left++;
    else if (e.to === id && towards.has(e.from)) right++;
  }
  return left > right ? "left" : "right";
}

/** Split a column, in order, into columns no taller than `height` (a too-tall box stands alone;
 *  at least FOCUS_LAYOUT.minRows boxes fit before a fold). */
function foldByHeight(col, byId, height) {
  const parts = [];
  let part = [], used = 0;
  for (const id of col) {
    const h = byId.get(id).h + FOCUS_LAYOUT.rowGap;
    if (part.length >= FOCUS_LAYOUT.minRows && used + h > height) { parts.push(part); part = []; used = 0; }
    part.push(id);
    used += h;
  }
  if (part.length) parts.push(part);
  return parts;
}

/**
 * A tour step's layout (DESIGN 7c): the focus classes in the middle column, the unfaded classes
 * (what a focused function really calls or is called by) in the columns beside it, callers left
 * and callees right, and the faded neighbours in the outer columns on the side of what they
 * touch. Columns fold so none is taller than `height` (canvas units), and are ordered to cut
 * crossings, so lines stay short; boxes line up on the column edge facing the middle.
 * `nodes`: {id, w, h, faded}; returns id -> centre {x, y},
 * the middle column at x = 0.
 */
function focusLayout(nodes, edges, { focus, height = 600 }) {
  const graph = graphOf(nodes, edges);
  const middle = nodes.filter((n) => focus.has(n.id)).map((n) => n.id);
  const near = nodes.filter((n) => !focus.has(n.id) && !n.faded).map((n) => n.id);
  const far = nodes.filter((n) => !focus.has(n.id) && n.faded).map((n) => n.id);
  const inner = new Set([...middle]);
  const nearSide = new Map(near.map((id) => [id, sideOf(id, graph.edges, inner)]));
  const placed = new Set([...middle, ...near]);
  const farSide = new Map(far.map((id) => {
    const touches = graph.neighbours.get(id).filter((m) => placed.has(m));
    const lefts = touches.filter((m) => nearSide.get(m) === "left").length;
    const rights = touches.filter((m) => nearSide.get(m) === "right").length;
    if (lefts !== rights) return [id, lefts > rights ? "left" : "right"];
    return [id, sideOf(id, graph.edges, placed)];
  }));
  const pick = (ids, sides, side) => ids.filter((id) => sides.get(id) === side).sort();
  const columns = [pick(far, farSide, "left"), pick(near, nearSide, "left"), middle.sort(),
    pick(near, nearSide, "right"), pick(far, farSide, "right")];
  const centre = 2;
  orderColumns(columns.filter((col) => col.length), graph.neighbours, LAYOUT.sweeps);
  // fold outward: the folded columns of a left group read from the outside in
  const folded = columns.map((col, c) => {
    const parts = foldByHeight(col, graph.byId, height);
    return c < centre ? parts.reverse() : parts;
  });
  const spots = new Map();
  const flat = folded.flat();
  let x = 0;
  const widths = flat.map((col) => Math.max(...col.map((id) => graph.byId.get(id).w)));
  const xs = widths.map((width) => {
    const at = x + width / 2;
    x += width + FOCUS_LAYOUT.columnGap;
    return at;
  });
  const middleIndex = folded.slice(0, centre).flat().length;  // first middle column
  // boxes line up on the column edge facing the middle: callers' right edges, the rest's left edges
  const alignedX = (c, w) => (c < middleIndex ? xs[c] + widths[c] / 2 - w / 2 : xs[c] - widths[c] / 2 + w / 2);
  flat.forEach((col, c) => {
    const heights = col.map((id) => graph.byId.get(id).h);
    const total = heights.reduce((a, b) => a + b, 0) + (col.length - 1) * FOCUS_LAYOUT.rowGap;
    let y = -total / 2;
    col.forEach((id, row) => {
      spots.set(id, { x: alignedX(c, graph.byId.get(id).w), y: y + heights[row] / 2 });
      y += heights[row] + FOCUS_LAYOUT.rowGap;
    });
  });
  const shift = middle.length ? xs[middleIndex] : 0;
  for (const spot of spots.values()) spot.x -= shift;
  return spots;
}

/**
 * The camera for a tour step's layout (DESIGN 7c): the whole step when it still reads at
 * `readAll` (faded neighbours at the edges), else the targets and the classes they really call
 * (`core`); never above `max` nor below `min` (then centred on the `focus` boxes).
 * `rects`: [{x, y, w, h, core, focus}] centres and sizes; `size`: {width, height} of the canvas.
 * Returns {zoom, pan} for Cytoscape.
 */
function focusFrame(rects, size, { pad = 40, min = 0.4, max = 1.2, readAll = 0.56 } = {}) {
  const boxOf = (list) => {
    const x1 = Math.min(...list.map((r) => r.x - r.w / 2)), x2 = Math.max(...list.map((r) => r.x + r.w / 2));
    const y1 = Math.min(...list.map((r) => r.y - r.h / 2)), y2 = Math.max(...list.map((r) => r.y + r.h / 2));
    return { cx: (x1 + x2) / 2, cy: (y1 + y2) / 2, w: x2 - x1, h: y2 - y1 };
  };
  const fit = (box) => Math.min((size.width - 2 * pad) / Math.max(box.w, 1), (size.height - 2 * pad) / Math.max(box.h, 1));
  if (!rects.length) return { zoom: 1, pan: { x: size.width / 2, y: size.height / 2 } };
  const core = rects.filter((r) => r.core);
  let box = boxOf(rects);
  let zoom = fit(box);
  if (zoom < readAll && core.length) { box = boxOf(core); zoom = fit(box); }
  if (zoom > max) zoom = max;
  if (zoom < min) {
    const focus = rects.filter((r) => r.focus);
    if (focus.length) box = boxOf(focus);
    zoom = min;
  }
  return { zoom, pan: { x: size.width / 2 - box.cx * zoom, y: size.height / 2 - box.cy * zoom } };
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
const ROUTE = { margin: 10, step: 34, maxOffset: 420, maxStretch: 1.4, slack: 60, nudge: 4, maxNudge: 10 };
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

/** Detours for a line a-b: one bend sideways from the middle, or a channel beside a blocker
 *  (entered part way along the line, or straight out of the end boxes: the gap between columns). */
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
    for (const y of [r.y1 - ROUTE.margin * 2, r.y2 + ROUTE.margin * 2]) {
      routes.push([{ x: p1.x, y }, { x: p2.x, y }]);
      routes.push([{ x: a.x, y }, { x: b.x, y }]);  // straight out of the end boxes into the gap
    }
    for (const x of [r.x1 - ROUTE.margin * 2, r.x2 + ROUTE.margin * 2]) {
      routes.push([{ x, y: p1.y }, { x, y: p2.y }]);
      routes.push([{ x, y: a.y }, { x, y: b.y }]);
    }
  }
  // Down the gap beside the first blocker, across a gap above/below some blocker, down the gap
  // beside the last one: for long lines between bands whose columns do not line up.
  const along = (r) => ((r.x1 + r.x2) / 2 - a.x) * (b.x - a.x) + ((r.y1 + r.y2) / 2 - a.y) * (b.y - a.y);
  const sorted = [...blockers].sort((r, q) => along(r) - along(q));
  const sides = (r) => [r.x1 - ROUTE.margin * 2, r.x2 + ROUTE.margin * 2];
  const ys = blockers.flatMap((r) => [r.y1 - ROUTE.margin * 2, r.y2 + ROUTE.margin * 2]);
  for (const x1 of sides(sorted[0])) {
    for (const x2 of sides(sorted.at(-1))) {
      for (const y of ys) routes.push([{ x: x1, y: a.y }, { x: x1, y }, { x: x2, y }, { x: x2, y: b.y }]);
    }
  }
  return routes;
}

/** The longest a bent line from `a` to `b` may be. */
function longestDetour(a, b) {
  return Math.hypot(b.x - a.x, b.y - a.y) * ROUTE.maxStretch + ROUTE.slack;
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
  const longest = longestDetour(a, b);
  for (const bends of detours(a, b, blockers)) {
    const points = [a, ...bends, b];
    const length = polylineLength(points);
    if (length > longest || polylineHits(points, near, ROUTE.margin)) continue;
    if (!best || length < best.length) best = { bends, length };
  }
  return best ? best.bends : [];
}

/**
 * Lines bent into the same gap run on top of each other and read as one thick line. Spread them:
 * bend-to-bend segments that are level (or upright) on the same row (column) and overlap get
 * `ROUTE.nudge` apart, at most `ROUTE.maxNudge` either side; a line the spreading would make too
 * long (`longestDetour`) keeps its bends. `routes` = [{a, b, bends: [{x, y}..]}], bends changed
 * in place; returns `routes`.
 */
function nudgeBends(routes) {
  const before = routes.map((route) => route.bends.map((p) => ({ ...p })));
  const groups = new Map();
  for (const { bends } of routes) {
    for (let i = 0; i + 1 < bends.length; i++) {
      const [p, q] = [bends[i], bends[i + 1]];
      const axis = Math.abs(p.x - q.x) < 0.5 ? "x" : Math.abs(p.y - q.y) < 0.5 ? "y" : null;  // the coordinate it keeps
      if (!axis) continue;
      const other = axis === "x" ? "y" : "x";
      const key = `${axis}${Math.round(p[axis])}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push({ axis, p, q, lo: Math.min(p[other], q[other]), hi: Math.max(p[other], q[other]) });
    }
  }
  for (const segments of groups.values()) {
    segments.sort((s, t) => s.lo - t.lo || s.hi - t.hi);
    let cluster = [];
    let reach = -Infinity;
    const spread = () => {
      const step = Math.min(ROUTE.nudge, (2 * ROUTE.maxNudge) / Math.max(1, cluster.length - 1));
      cluster.forEach((s, k) => {
        const off = (k - (cluster.length - 1) / 2) * step;
        s.p[s.axis] += off;
        s.q[s.axis] += off;
      });
    };
    for (const s of segments) {
      if (s.lo >= reach && cluster.length) { spread(); cluster = []; }
      cluster.push(s);
      reach = Math.max(reach, s.hi);
    }
    spread();
  }
  routes.forEach((route, i) => {
    if (polylineLength([route.a, ...route.bends, route.b]) <= longestDetour(route.a, route.b)) return;
    route.bends.forEach((p, k) => Object.assign(p, before[i][k]));
  });
  return routes;
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

// ---------- readability numbers (for tests and the layout bench) ----------

/** Do segments p1-p2 and q1-q2 cross (touching ends do not count)? */
function segmentsCross(p1, p2, q1, q2) {
  const side = (a, b, c) => Math.sign((b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x));
  return side(p1, p2, q1) * side(p1, p2, q2) < 0 && side(q1, q2, p1) * side(q1, q2, p2) < 0;
}

/**
 * How readable a placement is, with lines drawn straight between box centres (one per pair of
 * classes): `crossings` (line pairs that cross; pairs sharing an end left out), `under` (lines
 * passing under a box that is not one of their ends), `stuck` (of those, lines `routeAround` finds
 * no short way around for, so the viewer draws them under boxes), `overlaps` (box pairs), `length` (mean line
 * length in typical box widths), `backward` (share of lines running right to left, against the
 * callers-left reading), `slant` (mean line angle from level, degrees), `fill` (box area /
 * bounding-box area), `aspect` (width / height).
 */
function measure(nodes, edges, spots) {
  const byId = new Map(nodes.filter((n) => spots.has(n.id)).map((n) => [n.id, n]));
  const pairs = new Map();
  for (const e of edges) {
    if (e.from === e.to || !byId.has(e.from) || !byId.has(e.to)) continue;
    const key = e.from < e.to ? `${e.from}\n${e.to}` : `${e.to}\n${e.from}`;
    if (!pairs.has(key)) pairs.set(key, e);
  }
  const lines = [...pairs.values()].map((e) => ({ e, a: spots.get(e.from), b: spots.get(e.to) }));
  const boxes = [...byId.values()].map((n) => ({ id: n.id, ...boxOf(n, spots.get(n.id)) }));
  const taken = new Occupied(300);
  boxes.forEach((r) => taken.add(r));
  let overlaps = 0;
  boxes.forEach((r) => { overlaps += taken.count(r, -1) - 1; });
  let crossings = 0;
  for (let i = 0; i < lines.length; i++) {
    const p = lines[i];
    for (let j = i + 1; j < lines.length; j++) {
      const q = lines[j];
      if (p.e.from === q.e.from || p.e.from === q.e.to || p.e.to === q.e.from || p.e.to === q.e.to) continue;
      if (segmentsCross(p.a, p.b, q.a, q.b)) crossings++;
    }
  }
  let under = 0;
  let stuck = 0;
  for (const { e, a, b } of lines) {
    const span = { x1: Math.min(a.x, b.x), x2: Math.max(a.x, b.x), y1: Math.min(a.y, b.y), y2: Math.max(a.y, b.y) };
    const near = new Set(taken.cells(span).flatMap((key) => taken.buckets.get(key) || []));
    if (![...near].some((r) => r.id !== e.from && r.id !== e.to && segmentHitsRect(a, b, r, -2))) continue;
    under++;
    const others = boxes.filter((r) => r.id !== e.from && r.id !== e.to);
    if (!routeAround(a, b, others).length) stuck++;
  }
  const unit = mean(boxes.map((r) => r.x2 - r.x1)) || 1;
  const width = Math.max(...boxes.map((r) => r.x2)) - Math.min(...boxes.map((r) => r.x1)) || 1;
  const height = Math.max(...boxes.map((r) => r.y2)) - Math.min(...boxes.map((r) => r.y1)) || 1;
  const round = (v, d = 2) => Math.round((v || 0) * 10 ** d) / 10 ** d;
  const angle = ({ a, b }) => Math.atan2(Math.abs(b.y - a.y), Math.abs(b.x - a.x)) * 180 / Math.PI;
  return {
    nodes: boxes.length, lines: lines.length, crossings, under, stuck, overlaps: overlaps / 2,
    length: round(mean(lines.map(({ a, b }) => Math.hypot(b.x - a.x, b.y - a.y))) / unit),
    backward: round(lines.filter(({ a, b }) => b.x < a.x - 1).length / (lines.length || 1)),
    slant: round(mean(lines.map(angle)), 1),
    fill: round(boxes.reduce((s, r) => s + (r.x2 - r.x1) * (r.y2 - r.y1), 0) / (width * height), 3),
    aspect: round(width / height),
  };
}

const CoderLayout = { layout, layoutByLevels, settleFixed, layered, focusLayout, focusFrame, LAYOUT, METHODS, pickMethod, hubCentre, foldColumn, routeAround, placeLabels,
  pointAlong, segmentHitsRect, measure, nudgeBends };
if (typeof module !== "undefined") module.exports = CoderLayout;
else window.CoderLayout = CoderLayout;
})();
