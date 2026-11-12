// Viewer auto layout (viewer/layout.js). Run: node --test tests/js/test_layout.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const { layout, layoutByLevels, settleFixed, focusLayout, focusFrame, foldColumn, routeAround, placeLabels, pointAlong, segmentHitsRect, pickMethod, hubCentre, METHODS,
  measure, nudgeBends, LAYOUT } =
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

test("rows line up with neighbours on one shared row grid", () => {
  // m calls a, b, c; n calls only c: n sits level with c instead of in the middle of its column
  const nodes = ["m", "n", "a", "b", "c"].map((id) => node(id));
  const spots = layout(nodes, [edge("m", "a"), edge("m", "b"), edge("m", "c"), edge("n", "c")]);
  assert.equal(spots.get("n").y, spots.get("c").y);
  const step = 30 + LAYOUT.rowGap;
  const ys = nodes.map((n) => spots.get(n.id).y - spots.get("m").y);
  assert.ok(ys.every((y) => Math.abs(y / step - Math.round(y / step)) < 1e-9), `rows off the grid: ${ys}`);
  assertNoOverlap(nodes, spots);
});

test("a lower band slides under the classes that use it", () => {
  // level 1 is a chain a > b > c > d running right; x (level 2) is used by d only
  const nodes = [node("a"), node("b"), node("c"), node("d"), node("x", 2)];
  const spots = layout(nodes, [edge("a", "b"), edge("b", "c"), edge("c", "d"), edge("d", "x")]);
  assert.ok(Math.abs(spots.get("x").x - spots.get("d").x) < 1, `x at ${spots.get("x").x}, d at ${spots.get("d").x}`);
});

test("a tall lower column folds by where its users sit above", () => {
  const nodes = [node("p"), node("q"), ...Array.from({ length: 24 }, (_, i) => node(`k${i}`, 2))];
  const edges = [edge("p", "q"), ...nodes.slice(2).map((n, i) => edge(i % 2 ? "q" : "p", n.id))];
  const spots = layout(nodes, edges);
  const xs = (odd) => nodes.slice(2).filter((n, i) => i % 2 === odd).map((n) => spots.get(n.id).x);
  assert.ok(new Set(xs(0)).size > 1, "the column folds");
  assert.ok(Math.max(...xs(0)) < Math.min(...xs(1)), "p's classes fold to the left of q's");
  assertNoOverlap(nodes, spots);
});

test("measure counts crossings, lines under boxes, overlaps and length", () => {
  const nodes = ["a", "b", "c", "d", "e"].map((id) => node(id));
  const spots = new Map([["a", { x: 0, y: 0 }], ["b", { x: 400, y: 200 }], ["c", { x: 0, y: 200 }],
    ["d", { x: 400, y: 0 }], ["e", { x: 200, y: 100 }]]);
  const m = measure(nodes, [edge("a", "b"), edge("c", "d"), edge("b", "a"), edge("a", "a")], spots);
  assert.equal(m.lines, 2, "one line per pair, self lines left out");
  assert.equal(m.crossings, 1);
  assert.equal(m.under, 2, "both lines pass under e");
  assert.equal(m.overlaps, 0);
  assert.ok(Math.abs(m.length - Math.hypot(400, 200) / 100) < 0.01);
  spots.set("e", { x: 30, y: 10 });
  assert.equal(measure(nodes, [], spots).overlaps, 1);
  assert.equal(measure(nodes, [edge("a", "c"), edge("a", "d")], spots).crossings, 0, "lines sharing an end never cross");
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

// a grid of boxes 100x30, columns at `xs`, a row every 80 from y0 to y1, without the boxes at `skip`
const grid = (xs, y0, y1, skip) => xs.flatMap((x) => Array.from({ length: (y1 - y0) / 80 + 1 }, (_, i) => ({ x, y: y0 + i * 80 })))
  .filter((p) => !skip.some((s) => s.x === p.x && s.y === p.y)).map((p) => rect(p.x - 50, p.y - 15, p.x + 50, p.y + 15));

test("routeAround takes the gap between columns straight out of the end boxes", () => {
  // Regression: a line down a column of boxes found no bend (entries part way along hit boxes) and ran under them
  const a = { x: 0, y: 0 };
  const b = { x: 200, y: 480 };
  const boxes = grid([0, 200], 0, 480, [a, b]);
  const bends = routeAround(a, b, boxes);
  assert.ok(bends.length > 0);
  assert.ok(!pathHits([a, ...bends, b], boxes));
});

test("routeAround runs a line between bands down one gap, across, and down the next", () => {
  const a = { x: 0, y: 0 };
  const b = { x: 300, y: 640 };
  const boxes = [...grid([0, 200], 0, 320, [a]), ...grid([100, 300], 400, 720, [b])];
  const bends = routeAround(a, b, boxes);
  assert.equal(bends.length, 4);
  assert.ok(!pathHits([a, ...bends, b], boxes));
});

test("nudgeBends spreads lines sharing a gap, a little, and leaves others", () => {
  const route = (bends, a = { x: 0, y: 0 }, b = { x: 0, y: 400 }) => ({ a, b, bends });
  const shared = () => route([{ x: 70, y: 0 }, { x: 70, y: 400 }]);
  const routes = [shared(), shared(), shared(), route([{ x: 300, y: 0 }, { x: 300, y: 100 }]), route([{ x: 0, y: 0 }, { x: 50, y: 60 }])];
  nudgeBends(routes);
  const xs = routes.slice(0, 3).map((r) => r.bends[0].x);
  assert.equal(new Set(xs).size, 3, "the three shared lines are apart");
  assert.ok(xs.every((x) => Math.abs(x - 70) <= 10));
  routes.slice(0, 3).forEach((r) => assert.equal(r.bends[0].x, r.bends[1].x, "still upright"));
  assert.deepEqual(routes[3].bends, [{ x: 300, y: 0 }, { x: 300, y: 100 }], "a line alone stays put");
  assert.deepEqual(routes[4].bends, [{ x: 0, y: 0 }, { x: 50, y: 60 }], "slanted segments stay put");
});

test("nudgeBends never makes a line a long detour", () => {
  // Regression (a real app's level 2): spreading pushed a bent line just past the detour limit
  const a = { x: 0, y: 0 };
  const b = { x: 0, y: 100 };
  const limit = 100 * 1.4 + 60;
  const side = (limit - 100) / 2 - 1;  // as long as allowed, minus 2
  const routes = [0, 1].map(() => ({ a, b, bends: [{ x: side, y: 0 }, { x: side, y: 100 }] }));
  nudgeBends(routes);
  for (const r of routes) {
    const length = [a, ...r.bends, b].reduce((sum, p, i, all) => (i ? sum + Math.hypot(p.x - all[i - 1].x, p.y - all[i - 1].y) : 0), 0);
    assert.ok(length <= limit, `length ${length}`);
  }
  assert.ok(routes.some((r) => r.bends[0].x !== side), "the line that got shorter still moved");
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

test("focus layout: targets in the middle, callers left, callees right, faded outside, compact", () => {
  const n = (id, faded = false, h = 30) => ({ id, w: 100, h, faded });
  // scan (target) calls 3 lit classes; cli calls scan; 20 faded neighbours around them
  const nodes = [n("scan", false, 180), n("cli"), n("filewalk"), n("resolver"), n("levels")];
  const edges = [{ from: "cli", to: "scan" }, { from: "scan", to: "filewalk" }, { from: "scan", to: "resolver" }, { from: "scan", to: "levels" }];
  for (let i = 0; i < 20; i++) {
    nodes.push(n(`f${i}`, true));
    edges.push(i % 2 ? { from: "resolver", to: `f${i}` } : { from: `f${i}`, to: "cli" });
  }
  const spots = focusLayout(nodes, edges, { focus: new Set(["scan"]), height: 500 });
  const at = (id) => spots.get(id);
  assert.deepEqual(at("scan"), { x: 0, y: 0 }, "target in the middle");
  assert.ok(at("cli").x < 0, "caller left");
  for (const id of ["filewalk", "resolver", "levels"]) assert.ok(at(id).x > 0, `${id} callee right`);
  assert.ok(at("f0").x < at("cli").x, "faded neighbours of a caller further left");
  assert.ok(at("f1").x > at("resolver").x, "faded neighbours of a callee further right");
  for (const a of nodes) for (const b of nodes) {
    if (a !== b) assert.ok(!overlaps(a, b, spots), `${a.id} and ${b.id} overlap`);
  }
  const xs = [...spots.values()].map((p) => p.x), ys = [...spots.values()].map((p) => p.y);
  assert.ok(Math.max(...ys) - Math.min(...ys) <= 560, `fits the height: ${Math.max(...ys) - Math.min(...ys)}`);
  assert.ok(Math.max(...xs) - Math.min(...xs) < 1600, `compact width: ${Math.max(...xs) - Math.min(...xs)}`);
});

test("focus frame: the whole step when it reads, else its core; clamped zoom, centred", () => {
  const size = { width: 800, height: 400 };
  const r = (x, y, extra = {}) => ({ x, y, w: 100, h: 40, ...extra });
  const small = focusFrame([r(0, 0, { core: true, focus: true }), r(200, 0)], size);
  assert.equal(small.zoom, 1.2, "small step: at most 1.2");
  assert.deepEqual(small.pan, { x: 400 - 100 * 1.2, y: 200 }, "centred on the whole step");
  const wide = [r(0, 0, { core: true, focus: true }), r(150, 0, { core: true }), r(-3000, 0), r(3000, 0)];
  const framed = focusFrame(wide, size);
  assert.ok(framed.zoom > 0.56, "faded far ends dropped: the core is framed");
  assert.equal(framed.pan.x, 400 - 75 * framed.zoom);
  const huge = focusFrame([r(-5000, 0, { core: true }), r(5000, 0, { core: true, focus: true })], size);
  assert.equal(huge.zoom, 0.4, "never below the readable minimum");
  assert.equal(huge.pan.x, 400 - 5000 * 0.4, "then centred on the targets");
});

test("focus layout: columns line up on the edge facing the middle", () => {
  const n = (id, w) => ({ id, w, h: 30, faded: false });
  const nodes = [n("t", 100), n("c1", 80), n("c2", 160), n("d1", 60), n("d2", 140)];
  const edges = [{ from: "c1", to: "t" }, { from: "c2", to: "t" }, { from: "t", to: "d1" }, { from: "t", to: "d2" }];
  const spots = focusLayout(nodes, edges, { focus: new Set(["t"]), height: 600 });
  const right = (id) => spots.get(id).x + nodes.find((x) => x.id === id).w / 2;
  const left = (id) => spots.get(id).x - nodes.find((x) => x.id === id).w / 2;
  assert.equal(right("c1"), right("c2"), "callers: right edges aligned");
  assert.equal(left("d1"), left("d2"), "callees: left edges aligned");
});

test("settleFixed: saved spots whose boxes grew are nudged down until none overlap; fitting ones stay", () => {
  // saved when boxes were 30 tall, 50 apart; now they are 90 tall (box text) and overlap
  const tall = (id) => ({ id, w: 100, h: 90 });
  const fixed = new Map([["A", { x: 0, y: 0 }], ["B", { x: 0, y: 50 }], ["C", { x: 0, y: 100 }], ["D", { x: 400, y: 50 }]]);
  const moved = settleFixed(["A", "B", "C", "D"].map(tall), fixed);
  assert.deepEqual([...moved.keys()].sort(), ["B", "C"], "A and the far-off D keep their spots");
  const spots = new Map([...fixed].map(([id, s]) => [id, moved.get(id) || s]));
  const ids = [...spots.keys()];
  for (const a of ids) for (const b of ids) {
    if (a >= b) continue;
    const [p, q] = [spots.get(a), spots.get(b)];
    assert.ok(Math.abs(p.x - q.x) >= 100 || Math.abs(p.y - q.y) >= 90, `${a} and ${b} overlap`);
  }
  assert.ok(spots.get("B").y < spots.get("C").y, "order kept top to bottom");
  assert.equal(settleFixed(["A", "B"].map((id) => ({ id, w: 100, h: 30 })), fixed).size, 0, "boxes that still fit never move");
});

test("settleFixed spots feed layout as fixed: free classes never land on the nudged boxes", () => {
  const tall = (id) => ({ id, w: 100, h: 90, level: 1, kind: "script" });
  const saved = new Map([["A", { x: 0, y: 0 }], ["B", { x: 0, y: 50 }]]);
  const fixed = new Map([...saved].map(([id, s]) => [id, settleFixed([tall("A"), tall("B")], saved).get(id) || s]));
  const spots = layout(["A", "B", "C"].map(tall), [edge("A", "C"), edge("B", "C")], fixed);
  const c = spots.get("C");
  for (const id of ["A", "B"]) {
    const p = spots.get(id);
    assert.ok(Math.abs(p.x - c.x) >= 100 || Math.abs(p.y - c.y) >= 90, `C overlaps ${id}`);
  }
});
