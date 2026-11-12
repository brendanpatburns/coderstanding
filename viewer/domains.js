// Domain frames (DESIGN 6b): the pure part. Sorts the drawn classes into one frame per domain
// (the layered layout inside each frame, frames ordered by the domain graph and shelf-packed to
// the canvas shape), groups the lines between frames into bundles, merges lines that land on a
// folded frame's card, and places the faded domain cards around a focused domain.
// No DOM here: domain_view.js draws it. Tested with `node --test tests/js/test_domains.mjs`.
(function () {
"use strict";

const Layout = typeof module !== "undefined" ? require("./layout.js") : window.CoderLayout;

const UNSORTED = "Unsorted";  // the frame (and card) of classes without a domain; matches views.UNSORTED
const FRAME = { pad: 22, head: 34, desc: 20, colGap: 90, rowGap: 70, card: { w: 210, h: 66 } };
const HUES = [210, 150, 28, 275, 340, 188, 95, 0, 245, 48, 312];
const UNSORTED_HUE = 215;
const RING_GAP = 120;  // room between a focused domain and the cards around it

function domainOf(node) {
  return node.domain || UNSORTED;
}

/** A stable tint per domain name (the same in every level, the frames and the focus). */
function hueOf(name) {
  if (name === UNSORTED) return UNSORTED_HUE;
  let hash = 0;
  for (const ch of name) hash = (hash * 31 + ch.codePointAt(0)) >>> 0;
  return HUES[hash % HUES.length];
}

function defaultTitleWidth(name) {
  return name.length * 8.5 + 90;
}

/** Lay out one domain's classes on their own; returns {inner: Map id -> {x, y} centre offsets
 *  from the frame's content corner, w, h}. */
function layoutInside(members, edges) {
  const ids = new Set(members.map((n) => n.id));
  const spots = Layout.layered(members, edges.filter((e) => ids.has(e.from) && ids.has(e.to)));
  const left = Math.min(...members.map((n) => spots.get(n.id).x - n.w / 2));
  const top = Math.min(...members.map((n) => spots.get(n.id).y - n.h / 2));
  const inner = new Map(members.map((n) => [n.id, { x: spots.get(n.id).x - left, y: spots.get(n.id).y - top }]));
  const w = Math.max(...members.map((n) => inner.get(n.id).x + n.w / 2));
  const h = Math.max(...members.map((n) => inner.get(n.id).y + n.h / 2));
  return { inner, w, h };
}

/** Frame names in the layered order of the domain-to-domain graph: callers first (left), then down. */
function frameOrder(frames, edges, domainById) {
  const pairs = new Map();
  for (const e of edges) {
    const a = domainById.get(e.from), b = domainById.get(e.to);
    if (a && b && a !== b) pairs.set(`${a}>${b}`, { from: a, to: b, kinds: e.kinds || [] });
  }
  const nodes = frames.map((b) => ({ id: b.name, w: b.w, h: b.h, level: 1, kind: "script", flags: [] }));
  const spots = Layout.layered(nodes, [...pairs.values()]);
  const at = (b) => spots.get(b.name);
  return [...frames].sort((p, q) => at(p).x - at(q).x || at(p).y - at(q).y || (p.name < q.name ? -1 : 1));
}

/** Shelf-pack frames in order: rows about as wide as the canvas shape (`aspect` = width / height)
 *  asks for, so the frames fill the screen instead of one long strip. Sets x, y (top-left). */
function packShelves(frames, aspect, gaps = FRAME) {
  const area = frames.reduce((sum, b) => sum + (b.w + gaps.colGap) * (b.h + gaps.rowGap), 0);
  const rowWidth = Math.max(Math.sqrt(area * Math.max(1, aspect)), ...frames.map((b) => b.w));
  let x = 0, y = 0, rowH = 0;
  for (const b of frames) {
    if (x > 0 && x + b.w > rowWidth) { x = 0; y += rowH + gaps.rowGap; rowH = 0; }
    b.x = x; b.y = y;
    x += b.w + gaps.colGap;
    rowH = Math.max(rowH, b.h);
  }
  return frames;
}

/** An open box grows right and down from its closed top-left corner (fanout.js grownSpot). The
 *  frames are laid out and packed with closed sizes, so opening a class never re-sorts anything:
 *  only its own frame grows to fit the open box (it may then overlap a neighbour). */
function growOpenBoxes(frame, nodeById) {
  for (const id of frame.members) {
    const n = nodeById.get(id);
    if (!n.widen && !n.grow) continue;
    const p = frame.inner.get(id);
    const at = { x: p.x + (n.widen || 0), y: p.y + (n.grow || 0) };
    frame.inner.set(id, at);
    frame.w = Math.max(frame.w, at.x + n.w / 2 + (n.widen || 0) + FRAME.pad);
    frame.h = Math.max(frame.h, at.y + n.h / 2 + (n.grow || 0) + FRAME.pad);
  }
}

/** Sort classes into frames. nodes: {id, w, h, level, kind, flags, domain, widen?, grow?} with the
 *  closed box size in w, h and an open box's extra half width / height in widen, grow;
 *  edges: {from, to, kinds}.
 *  `described(name)`: the frame shows a description line under its header (FRAME.desc taller).
 *  Returns frames [{name, hue, x, y, w, h, top, members: [id], inner: Map id -> centre offset from x, y}];
 *  `top` = where the frame's body starts (header plus description line). */
function arrangeFrames(nodes, edges, { aspect = 16 / 9, titleWidth = defaultTitleWidth, described = () => false } = {}) {
  const groups = new Map();
  for (const n of nodes) {
    const name = domainOf(n);
    if (!groups.has(name)) groups.set(name, []);
    groups.get(name).push(n);
  }
  const frames = [...groups].map(([name, members]) => {
    const inside = layoutInside(members, edges);
    const top = FRAME.head + (described(name) ? FRAME.desc : 0);
    const inner = new Map([...inside.inner].map(([id, p]) => [id, { x: FRAME.pad + p.x, y: top + FRAME.pad * 0.7 + p.y }]));
    return { name, hue: hueOf(name), members: members.map((n) => n.id), inner, top,
      w: Math.max(inside.w + 2 * FRAME.pad, titleWidth(name)), h: top + FRAME.pad * 0.7 + inside.h + FRAME.pad };
  });
  const domainById = new Map(nodes.map((n) => [n.id, domainOf(n)]));
  const packed = packShelves(frameOrder(frames, edges, domainById), aspect);
  const nodeById = new Map(nodes.map((n) => [n.id, n]));
  packed.forEach((b) => growOpenBoxes(b, nodeById));
  return packed;
}

/** One bundle per unordered pair of frames: {key, p, q, fwd (p -> q lines), back, edges}. */
function bundlesOf(edges, frameOf) {
  const out = new Map();
  for (const e of edges) {
    const a = frameOf(e.from), b = frameOf(e.to);
    if (a === b) continue;
    const [p, q] = a < b ? [a, b] : [b, a];
    const key = `${p}|${q}`;
    if (!out.has(key)) out.set(key, { key, p, q, fwd: 0, back: 0, edges: [] });
    const bundle = out.get(key);
    bundle.edges.push(e);
    if (a === p) bundle.fwd++; else bundle.back++;
  }
  return [...out.values()];
}

/** "27 ⇄" when lines run both ways, else "27". */
function bundleLabel(bundle) {
  return bundle.fwd && bundle.back ? `${bundle.edges.length} ⇄` : String(bundle.edges.length);
}

/** Line thickness for a bundle of n lines. */
function bundleWidth(n) {
  return 2 + Math.sqrt(n) * 1.6;
}

function callCount(edge) {
  return (edge.calls || []).reduce((sum, c) => sum + (c.count || 1), 0);
}

/** The busiest lines first (by call count), at most `max`. */
function topLines(edges, max = 12) {
  return [...edges].sort((a, b) => callCount(b) - callCount(a)).slice(0, max);
}

/** Lines grouped by the ends they are drawn between: `endOf(id)` is the class itself or its folded
 *  frame's card key. Lines inside one card vanish; the rest merge per end pair {key, a, b, edges}. */
function mergeEnds(edges, endOf) {
  const groups = new Map();
  for (const e of edges) {
    const a = endOf(e.from), b = endOf(e.to);
    if (a === b) continue;
    const key = `${a}>${b}`;
    if (!groups.has(key)) groups.set(key, { key, a, b, edges: [] });
    groups.get(key).edges.push(e);
  }
  return [...groups.values()];
}

/** Spots (centres) for items {id, w, h, anchor: {x, y} | null} on a ring around `box`
 *  {x1, y1, x2, y2}, evenly spaced and never overlapping, in the order of their anchors (the
 *  classes each talks to) around the box, turned so each sits near its anchor's side. */
function ringSpots(box, items, gap = RING_GAP) {
  const spots = new Map();
  if (!items.length) return spots;
  const cx = (box.x1 + box.x2) / 2, cy = (box.y1 + box.y2) / 2;
  const angleOf = (item) => (item.anchor ? Math.atan2(item.anchor.y - cy, item.anchor.x - cx) : Math.PI / 2);
  const sorted = [...items].sort((a, b) => angleOf(a) - angleOf(b) || (a.id < b.id ? -1 : 1));
  const step = (2 * Math.PI) / sorted.length;
  const size = Math.max(...items.map((i) => Math.hypot(i.w, i.h))) + 24;  // room each item needs
  const minR = sorted.length > 1 ? size / (2 * Math.sin(Math.min(step, Math.PI) / 2)) : 0;
  // an ellipse through the box's corners (half-sizes times sqrt 2), pushed out by the gap and item size
  const rx = Math.max((box.x2 - box.x1) / 2 * Math.SQRT2 + gap + size / 2, minR);
  const ry = Math.max((box.y2 - box.y1) / 2 * Math.SQRT2 + gap + size / 2, minR);
  // the turn that best matches the anchors: circular mean of each item's offset from its slot
  const turn = Math.atan2(...sorted.reduce(([sy, sx], item, i) =>
    [sy + Math.sin(angleOf(item) - i * step), sx + Math.cos(angleOf(item) - i * step)], [0, 0]));
  sorted.forEach((item, i) => {
    const angle = turn + i * step;
    spots.set(item.id, { x: cx + rx * Math.cos(angle), y: cy + ry * Math.sin(angle) });
  });
  return spots;
}

const CoderDomains = { UNSORTED, FRAME, HUES, domainOf, hueOf, arrangeFrames, packShelves, frameOrder, bundlesOf,
  bundleLabel, bundleWidth, callCount, topLines, mergeEnds, ringSpots };
if (typeof module !== "undefined") module.exports = CoderDomains;
else window.CoderDomains = CoderDomains;
})();
