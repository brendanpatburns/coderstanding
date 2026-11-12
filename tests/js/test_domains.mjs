// Domain frames, pure part (viewer/domains.js). Run: node --test tests/js/test_domains.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const D = createRequire(import.meta.url)("../../viewer/domains.js");

const node = (id, domain, level = 1) => ({ id, w: 120, h: 40, level, kind: "script", flags: [], domain });
const edge = (from, to, calls = 1) => ({ from, to, kinds: ["dependency"], calls: [{ member: "f", count: calls }] });

const rectsOverlap = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

test("arrangeFrames: one frame per domain plus Unsorted, members inside, no overlaps", () => {
  const nodes = [node("A", "Net"), node("B", "Net"), node("C", "UI"), node("D", null), node("E", "UI", 2)];
  const edges = [edge("A", "B"), edge("A", "C"), edge("C", "E"), edge("D", "A")];
  const frames = D.arrangeFrames(nodes, edges, { aspect: 1.6 });
  assert.deepEqual(frames.map((b) => b.name).sort(), ["Net", "UI", "Unsorted"]);
  assert.deepEqual(frames.find((b) => b.name === "UI").members.sort(), ["C", "E"]);
  for (const b of frames) {
    for (const id of b.members) {
      const p = b.inner.get(id);
      assert.ok(p.x - 60 >= D.FRAME.pad - 0.01 && p.x + 60 <= b.w + 0.01, `${id} inside ${b.name} across`);
      assert.ok(p.y - 20 >= D.FRAME.head && p.y + 20 <= b.h + 0.01, `${id} inside ${b.name} down`);
    }
  }
  frames.forEach((a, i) => frames.slice(i + 1).forEach((b) => assert.ok(!rectsOverlap(a, b), `${a.name} overlaps ${b.name}`)));
  // callers first: Unsorted (D calls Net) comes before Net, Net before UI
  const order = frames.map((b) => b.name);
  assert.ok(order.indexOf("Unsorted") < order.indexOf("Net") && order.indexOf("Net") < order.indexOf("UI"), order.join(","));
});

test("packShelves: rows follow the canvas shape", () => {
  const frames = () => Array.from({ length: 9 }, (_, i) => ({ name: `b${i}`, w: 200, h: 150 }));
  const wide = D.packShelves(frames(), 3);
  const tall = D.packShelves(frames(), 1);
  const rows = (list) => new Set(list.map((b) => b.y)).size;
  assert.ok(rows(wide) < rows(tall), `${rows(wide)} rows wide, ${rows(tall)} tall`);
  assert.equal(wide[0].x, 0);
  assert.equal(wide[0].y, 0);
});

test("hueOf: stable, Unsorted grey hue", () => {
  assert.equal(D.hueOf("Net"), D.hueOf("Net"));
  assert.ok(D.HUES.includes(D.hueOf("HUD")));
  assert.equal(D.hueOf(D.UNSORTED), 215);
});

test("bundlesOf: one bundle per unordered pair, both directions counted, inner lines skipped", () => {
  const frameOf = (id) => ({ A: "Net", B: "Net", C: "UI", D: "Audio" }[id]);
  const bundles = D.bundlesOf([edge("A", "C"), edge("B", "C"), edge("C", "A"), edge("A", "B"), edge("D", "A")], frameOf);
  assert.equal(bundles.length, 2);
  const netUi = bundles.find((b) => b.key === "Net|UI");
  assert.equal(netUi.edges.length, 3);
  assert.equal(netUi.fwd, 2);
  assert.equal(netUi.back, 1);
  assert.equal(D.bundleLabel(netUi), "3 ⇄");
  const audio = bundles.find((b) => b.key === "Audio|Net");
  assert.equal(D.bundleLabel(audio), "1");
  assert.ok(D.bundleWidth(9) > D.bundleWidth(1));
});

test("topLines: busiest first, capped", () => {
  const lines = [edge("A", "B", 2), edge("A", "C", 9), edge("B", "C", 5)];
  assert.deepEqual(D.topLines(lines, 2).map((e) => e.to + e.calls[0].count), ["C9", "C5"]);
  assert.equal(D.callCount({ calls: [{ count: 2 }, { count: 3 }] }), 5);
});

test("mergeEnds: lines onto a folded card merge per class, lines inside the card vanish", () => {
  const folded = new Set(["UI"]);
  const frameOf = (id) => ({ A: "Net", B: "Net", C: "UI", E: "UI" }[id]);
  const endOf = (id) => (folded.has(frameOf(id)) ? "frame:UI" : id);
  const groups = D.mergeEnds([edge("A", "C"), edge("A", "E"), edge("B", "C"), edge("C", "E"), edge("E", "A")], endOf);
  const byKey = Object.fromEntries(groups.map((g) => [g.key, g.edges.length]));
  assert.deepEqual(byKey, { "A>frame:UI": 2, "B>frame:UI": 1, "frame:UI>A": 1 });
});

test("ringSpots: around the box, no overlaps, near each anchor's side", () => {
  const box = { x1: 0, y1: 0, x2: 400, y2: 200 };
  const items = ["n", "e", "s", "w", "x", "y"].map((id) => ({ id, w: 210, h: 66, anchor: null }));
  items[0].anchor = { x: 200, y: -500 };
  items[1].anchor = { x: 900, y: 100 };
  items[3].anchor = { x: -500, y: 100 };
  const spots = D.ringSpots(box, items);
  assert.equal(spots.size, 6);
  const rect = (id) => ({ x: spots.get(id).x - 105, y: spots.get(id).y - 33, w: 210, h: 66 });
  for (const it of items) assert.ok(!rectsOverlap(rect(it.id), { x: 0, y: 0, w: 400, h: 200 }), `${it.id} off the box`);
  items.forEach((a, i) => items.slice(i + 1).forEach((b) => assert.ok(!rectsOverlap(rect(a.id), rect(b.id)), `${a.id} overlaps ${b.id}`)));
  assert.ok(spots.get("e").x > spots.get("w").x, "east right of west");
  assert.equal(D.ringSpots(box, []).size, 0);
});

test("ringSpots: a wide box keeps its corners clear", () => {
  const box = { x1: 0, y1: 0, x2: 1200, y2: 300 };
  const items = Array.from({ length: 8 }, (_, i) => ({ id: `c${i}`, w: 210, h: 66, anchor: { x: Math.cos(i) * 900 + 600, y: Math.sin(i) * 400 + 150 } }));
  const spots = D.ringSpots(box, items);
  for (const it of items) {
    const p = spots.get(it.id);
    assert.ok(!rectsOverlap({ x: p.x - 105, y: p.y - 33, w: 210, h: 66 }, { x: 0, y: 0, w: 1200, h: 300 }), `${it.id} off the box`);
  }
});

test("arrangeFrames: an open box grows only its own frame; nothing else moves (open/close keeps the picture)", () => {
  const nodes = [node("A", "Net"), node("B", "Net"), node("C", "UI"), node("D", null), node("E", "UI", 2)];
  const edges = [edge("A", "B"), edge("A", "C"), edge("C", "E"), edge("D", "A")];
  const closed = D.arrangeFrames(nodes, edges, { aspect: 1.6 });
  const open = D.arrangeFrames(nodes.map((n) => (n.id === "A" ? { ...n, widen: 80, grow: 150 } : n)), edges, { aspect: 1.6 });
  const byName = (frames) => new Map(frames.map((b) => [b.name, b]));
  const [c, o] = [byName(closed), byName(open)];
  for (const name of ["UI", "Unsorted"]) {
    const [p, q] = [c.get(name), o.get(name)];
    assert.deepEqual([q.x, q.y, q.w, q.h], [p.x, p.y, p.w, p.h], `${name} stays`);
    assert.deepEqual([...q.inner], [...p.inner], `${name} boxes stay`);
  }
  const [net, grown] = [c.get("Net"), o.get("Net")];
  assert.deepEqual([grown.x, grown.y], [net.x, net.y], "Net keeps its corner");
  assert.deepEqual(grown.inner.get("B"), net.inner.get("B"), "B stays");
  // A grows right and down from its closed top-left corner, and the frame fits it
  const [a, ga] = [net.inner.get("A"), grown.inner.get("A")];
  assert.deepEqual([ga.x - 80 - 60, ga.y - 150 - 20], [a.x - 60, a.y - 20], "A's top-left corner stays");
  assert.ok(grown.w >= ga.x + 60 + 80 + D.FRAME.pad - 0.01 && grown.h >= ga.y + 20 + 150 + D.FRAME.pad - 0.01, "Net fits open A");
});
