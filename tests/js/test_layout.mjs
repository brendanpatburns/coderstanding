// Viewer auto layout (viewer/layout.js). Run: node --test tests/js/test_layout.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const { layout, layoutByLevels, foldColumn, routeAround, placeLabels, pointAlong, segmentHitsRect, pickMethod, hubCentre, METHODS } =
  createRequire(import.meta.url)("../../viewer/layout.js");

const node = (id, level = 1, kind = "script") => ({ id, w: 100, h: 30, level, kind });
const edge = (from, to, kinds = ["dependency"]) => ({ from, to, kinds });

function overlaps(a, b, spots) {
  const p = spots.get(a.id);
  const q = spots.get(b.id);
  return Math.abs(p.x - q.x) < (a.w + b.w) / 2 && Math.abs(p.y - q.y) < (a.h + b.h) / 2;
}

function assertNoOverlap(nodes, spots) {
  nodes.forEach((a, i) => nodes.slice(i + 1).forEach((b) =>
    assert.ok(!overlaps(a, b, spots), `${a.id} overlaps ${b.id}`)));
}

test("foldColumn keeps the order reading top to bottom", () => {
  assert.deepEqual(foldColumn(["a", "b", "c", "d", "e"], 2), [["a", "d"], ["b", "e"], ["c"]]);
  assert.deepEqual(foldColumn(["a", "b"], 6), [["a", "b"]]);
});

test("callers sit left of callees, level 1 above level 2", () => {
  const nodes = [node("main"), node("net"), node("deep", 2), node("deeper", 2)];
  const spots = layout(nodes, [edge("main", "net"), edge("deep", "deeper"), edge("main", "deep")]);
  assert.ok(spots.get("main").x < spots.get("net").x);
  assert.ok(spots.get("deep").x < spots.get("deeper").x);
  assert.ok(spots.get("deep").y > Math.max(spots.get("main").y, spots.get("net").y));
});

test("a hub's callees share one column instead of a long chain", () => {
  const nodes = [node("main"), node("a"), node("b"), node("c")];
  const spots = layout(nodes, [edge("main", "a"), edge("main", "b"), edge("main", "c"), edge("a", "b")]);
  assert.equal(new Set(["a", "b", "c"].map((id) => spots.get(id).x)).size, 1);
});

test("outside code sits right beside its first user; unused outside code in a grid", () => {
  const ext = Array.from({ length: 6 }, (_, i) => node(`ext${i}`, 5, "external"));
  const loose = Array.from({ length: 12 }, (_, i) => node(`loose${i}`, 5, "external"));
  const nodes = [node("main"), node("lib"), node("deep", 2), ...ext, ...loose];
  const edges = [edge("main", "lib"), edge("lib", "deep"), ...ext.map((e) => edge("deep", e.id)), edge("main", "ext0")];
  for (const method of METHODS) {
    const spots = layout(nodes, edges, new Map(), { method });
    const deep = spots.get("deep");
    ext.slice(1).forEach((e) => {
      const p = spots.get(e.id);
      assert.ok(p.x > deep.x && p.x - deep.x < 200 && Math.abs(p.y - deep.y) < 320, `${method}: ${e.id} by deep`);
    });
    const main = spots.get("main");
    assert.ok(Math.abs(spots.get("ext0").x - main.x) < 200, `${method}: ext0 by main (lowest level user)`);
    assertNoOverlap(nodes, spots);
  }
  const spots = layout(nodes, edges);
  assert.ok(new Set(loose.map((e) => spots.get(e.id).x)).size > 1, "unused outside code: more than one column");
});

test("a scene sits in the band of the script it holds", () => {
  const nodes = [node("main.gd"), node("main.tscn", 3, "scene"), node("other", 3)];
  const spots = layout(nodes, [edge("main.tscn", "main.gd", ["composition"])]);
  assert.ok(spots.get("main.tscn").y < spots.get("other").y);
});

test("fixed spots never move and free classes stay clear of them", () => {
  const nodes = Array.from({ length: 12 }, (_, i) => node(`n${i}`, 1 + (i % 3)));
  const edges = nodes.slice(1).map((n, i) => edge(nodes[i].id, n.id));
  const auto = layout(nodes, edges);
  const fixed = new Map([["n0", auto.get("n3")], ["n5", { x: 0, y: 0 }]]);  // on top of others
  const spots = layout(nodes, edges, fixed);
  assert.deepEqual(spots.get("n0"), auto.get("n3"));
  assert.deepEqual(spots.get("n5"), { x: 0, y: 0 });
  assertNoOverlap(nodes, spots);
});

test("a few new classes go next to their placed neighbours", () => {
  const nodes = [node("a"), node("b"), node("c"), node("d"), node("new")];
  const fixed = new Map([["a", { x: 0, y: 0 }], ["b", { x: 0, y: 100 }], ["c", { x: 0, y: 200 }],
    ["d", { x: 2000, y: 2000 }]]);
  const spots = layout(nodes, [edge("a", "new")], fixed);
  assert.ok(spots.get("new").x > 0 && spots.get("new").x < 500);
  assert.ok(Math.abs(spots.get("new").y) < 200);
});

test("big graphs stay fast", () => {
  const nodes = Array.from({ length: 5000 }, (_, i) => node(`n${i}`, 1 + (i % 5), i % 40 ? "class" : "external"));
  const edges = nodes.map((n, i) => edge(n.id, `n${(i * 7919) % 5000}`));
  const start = performance.now();
  assert.equal(layout(nodes, edges).size, 5000);
  assert.ok(performance.now() - start < 2000);
});

// ---------- lines: bends around boxes, label spots ----------

const rect = (x1, y1, x2, y2) => ({ x1, y1, x2, y2 });
const pathHits = (points, rects) => points.slice(1).some((q, i) => rects.some((r) => segmentHitsRect(points[i], q, r)));

test("segmentHitsRect sees crossing and missing segments", () => {
  assert.ok(segmentHitsRect({ x: 0, y: 0 }, { x: 100, y: 0 }, rect(40, -10, 60, 10)));
  assert.ok(!segmentHitsRect({ x: 0, y: 30 }, { x: 100, y: 30 }, rect(40, -10, 60, 10)));
  assert.ok(segmentHitsRect({ x: 0, y: 15 }, { x: 100, y: 15 }, rect(40, -10, 60, 10), 6));
});

test("pointAlong walks a polyline by length", () => {
  const p = pointAlong([{ x: 0, y: 0 }, { x: 100, y: 0 }, { x: 100, y: 100 }], 0.75);
  assert.deepEqual([p.x, p.y, p.dx, p.dy], [100, 50, 0, 1]);
});

test("routeAround leaves clear lines straight and bends a line that skips a column", () => {
  const a = { x: 0, y: 0 };
  const b = { x: 600, y: 0 };
  assert.deepEqual(routeAround(a, b, [rect(250, 60, 350, 100)]), []);
  const boxes = [rect(250, -20, 350, 20), rect(250, 60, 350, 100)];
  const bends = routeAround(a, b, boxes);
  assert.ok(bends.length > 0);
  assert.ok(!pathHits([a, ...bends, b], boxes), "the bent line misses every box");
});

test("routeAround never sends a short line on a long detour to empty space", () => {
  // Regression (a real game's level 1): world.gd -> background.gd, 154 apart, bent out 250 to the side
  // because a wall of boxes sat between them; the bend read as a line ending at nothing.
  const a = { x: 1417, y: 1489 };
  const b = { x: 1435, y: 1336 };
  const wall = [rect(1340, 1400, 1520, 1430)];
  const bends = routeAround(a, b, wall);
  const length = [a, ...bends, b].reduce((sum, p, i, all) => (i ? sum + Math.hypot(p.x - all[i - 1].x, p.y - all[i - 1].y) : 0), 0);
  assert.ok(length <= Math.hypot(b.x - a.x, b.y - a.y) * 1.4 + 60, `detour of ${Math.round(length)}`);
});

test("layoutByLevels lays out level 1 on its own and keeps those spots at level 2", () => {
  const top = [node("main"), node("hubA"), node("hubB"), { ...node("lib", 1, "external"), w: 60 }];
  const topEdges = [edge("main", "hubA"), edge("main", "hubB"), edge("hubA", "hubB"), edge("hubB", "lib")];
  const more = Array.from({ length: 30 }, (_, i) => node(`c${i}`, 2));
  const moreEdges = more.map((n, i) => edge(i % 2 ? "hubA" : "hubB", n.id));
  const steps = [{ nodes: top, edges: topEdges }, { nodes: [...top, ...more], edges: [...topEdges, ...moreEdges] }];
  const spots = layoutByLevels(steps, new Map(), { method: "organic" });
  const alone = layout(top, topEdges, new Map(), { method: "organic" });
  for (const n of top) assert.deepEqual(spots.get(n.id), alone.get(n.id), `${n.id} keeps its level-1 spot`);
  assertNoOverlap([...top, ...more], spots);
  const gap = (p, q, a, b) => Math.max(Math.abs(p.x - q.x) - (a.w + b.w) / 2, Math.abs(p.y - q.y) - (a.h + b.h) / 2);
  top.forEach((a, i) => top.slice(i + 1).forEach((b) =>
    assert.ok(gap(alone.get(a.id), alone.get(b.id), a, b) >= 40, `${a.id}/${b.id} keep a gap`)));
  const hubB = alone.get("hubB");
  const lib = alone.get("lib");
  assert.ok(Math.hypot(lib.x - hubB.x, lib.y - hubB.y) < 260, "outside code sits by its user");
});

test("placeLabels spreads labels of one busy hub and keeps them off boxes", () => {
  const hub = rect(-60, -15, 60, 15);
  const items = ["a", "b", "c", "d"].map((id) => ({ id, w: 80, h: 14, path: [{ x: 60, y: 0 }, { x: 400, y: 0 }] }));
  const spots = placeLabels(items, [hub]);
  assert.equal(spots.size, 4);
  const boxes = [...spots.values()].map((p) => rect(p.x - 40, p.y - 7, p.x + 40, p.y + 7));
  boxes.forEach((r, i) => {
    assert.ok(!(r.x1 < hub.x2 && hub.x1 < r.x2 && r.y1 < hub.y2 && hub.y1 < r.y2), "label off the hub box");
    boxes.slice(i + 1).forEach((s) => assert.ok(!(r.x1 < s.x2 && s.x1 < r.x2 && r.y1 < s.y2 && s.y1 < r.y2), "labels apart"));
  });
});

test("placeLabels drops a label with no clear spot unless it is required", () => {
  const wall = rect(-1000, -1000, 1000, 1000);
  const path = [{ x: 0, y: 0 }, { x: 100, y: 0 }];
  assert.equal(placeLabels([{ id: "x", w: 40, h: 14, path }], [wall]).size, 0);
  assert.equal(placeLabels([{ id: "x", w: 40, h: 14, path, required: true }], [wall]).size, 1);
});

// ---------- layout methods ----------

const flagged = (id, flags, kind = "script", level = 1) => ({ ...node(id, level, kind), flags });

function appGraph() {
  const nodes = [flagged("main", ["godot-main", "entry"]), flagged("net", ["autoload"]), flagged("media", ["autoload"]),
    node("world"), node("chat"), flagged("make_fonts", ["script-main", "entry"]), node("pip:numpy", 1, "external")];
  const edges = [edge("main", "net"), edge("main", "media"), edge("main", "world"), edge("world", "chat"),
    edge("chat", "net"), edge("media", "net"), edge("make_fonts", "pip:numpy")];
  return { nodes, edges };
}

test("pickMethod falls back to grid for force layouts on big graphs", () => {
  assert.deepEqual(pickMethod("organic", 200), { method: "organic", fellBack: false });
  assert.deepEqual(pickMethod("hub", 5000), { method: "grid", fellBack: true });
  assert.deepEqual(pickMethod("layered", 5000), { method: "layered", fellBack: false });
  assert.equal(pickMethod("nonsense", 10).method, "layered");
});

test("every method keeps fixed spots and leaves no overlaps", () => {
  const { nodes, edges } = appGraph();
  const fixed = new Map([["net", { x: 500, y: 40 }]]);
  for (const method of METHODS) {
    const spots = layout(nodes, edges, fixed, { method });
    assert.deepEqual(spots.get("net"), { x: 500, y: 40 }, method);
    assert.equal(spots.size, nodes.length, method);
    assertNoOverlap(nodes, spots);
  }
});

test("organic is deterministic, puts tools left and outside code by its user", () => {
  const { nodes, edges } = appGraph();
  const a = layout(nodes, edges, new Map(), { method: "organic" });
  const b = layout(nodes, edges, new Map(), { method: "organic" });
  assert.deepEqual([...a], [...b]);
  const others = nodes.filter((n) => !["make_fonts", "pip:numpy"].includes(n.id));
  assert.ok(others.every((n) => a.get("make_fonts").x < a.get(n.id).x), "tool left of the app");
  const dist = (p, q) => Math.hypot(p.x - q.x, p.y - q.y);
  assert.ok(dist(a.get("pip:numpy"), a.get("make_fonts")) < dist(a.get("pip:numpy"), a.get("main")), "outside code near its user");
});

test("hub centres on the chosen class, else the app's start", () => {
  const { nodes, edges } = appGraph();
  assert.equal(hubCentre(nodes, edges), "main");
  assert.equal(hubCentre(nodes, edges, "world"), "world");
  const spots = layout(nodes, edges, new Map(), { method: "hub", center: "world" });
  assert.deepEqual(spots.get("world"), { x: 0, y: 0 });
});
