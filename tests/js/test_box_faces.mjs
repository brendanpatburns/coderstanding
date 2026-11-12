// Box faces (viewer/fanout.js): Box text modes, what a class does, footer, importance, used-by
// badge, faint (quiet) fan-out lines. Run: node --test tests/js/test_box_faces.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const read = (name) => readFileSync(new URL(`../../viewer/${name}`, import.meta.url), "utf8");
const sandbox = { state: { boxText: "short" }, token: () => "mono", textWidth: (text) => String(text).length * 7 };
vm.runInNewContext(`${read("typechips.js")}\n${read("plan_view.js")}\n${read("fanout.js")}\nObject.assign(this, { describedLayout, faceLayout, fanNodeData, footText,
  whatItDoes, importance, usedByBadge, boxTextMode, isFaint, DESC, FACE });`, sandbox);
const S = sandbox;
const plain = (x) => JSON.parse(JSON.stringify(x));
const node = (extra = {}) => ({ id: "a", name: "scan", kind: "module", file: "scan.py", flags: [], functions: 6, properties: 0, used_by: 1, ...extra });
const base = (n) => ({ name: n.name, marks: "", label: n.name, w: 100, h: 30 });

test("whatItDoes: the hint first (one line), else the description; Full prefers the description", () => {
  assert.equal(S.whatItDoes({ hint: "Short", description: "Long text" }), "Short");
  assert.equal(S.whatItDoes({ description: "From the code\n comment" }), "From the code comment");
  assert.equal(S.whatItDoes({ hint: "Short", description: "Long text" }, true), "Long text");
  assert.equal(S.whatItDoes(undefined), "");
});

test("short: what it does under the name, clamped to 2 lines; Full: all of it, domain in the footer", () => {
  const text = "Reads a whole repo and turns it into the map that every other part shows, ".repeat(4);
  const n = node({ hint: text, description: text, domain: "Scanning" });
  const short = plain(S.describedLayout(n, base(n), "short"));
  assert.equal(short.descLines.length, 2);
  assert.ok(short.descLines[1].endsWith("…"));
  assert.ok(short.w <= S.FACE.wrapAt && short.w >= S.DESC.minW);
  const full = plain(S.describedLayout(n, base(n), "full"));
  assert.ok(full.descLines.length > 2 && full.faceH > short.faceH, "Full is taller");
  assert.ok(full.footLines.join(" ").includes("Scanning") && !short.footLines.join(" ").includes("Scanning"));
});

test("a long name wraps and still gets its 2-line description; the type chip moves to its own row when it does not fit", () => {
  const n = node({ name: "VeryLongInventoryItemSlotControllerForTheWholeGameUi", file: "long_names.gd", hint: "Keeps slots in step" });
  const face = plain(S.describedLayout(n, base(n), "short"));
  assert.ok(face.nameLines.length > 1 && face.nameLines.join("") === n.name);
  assert.equal(face.descLines.length, 1);
  assert.ok(face.headH > S.DESC.head);
});

test("no description: room for 'No description yet' + Ask AI, footer counts properties only when there are some", () => {
  const face = plain(S.describedLayout(node(), base(node()), "short"));
  assert.deepEqual(face.descLines, []);
  assert.equal(face.footLines.join(" "), "6 functions");
  assert.equal(S.footText(node({ properties: 2, functions: 1 }), "#ui", "short"), "1 function · 2 properties · #ui");
});

test("Box text: name keeps today's face; short/full boxes are taller; unknown values fall back to short", () => {
  const n = node({ hint: "Reads code" });
  S.state.boxText = "name";
  const named = plain(S.fanNodeData(n, base(n)));
  assert.equal(named.descLines, undefined);
  S.state.boxText = "short";
  const short = plain(S.fanNodeData(n, base(n)));
  assert.ok(short.h > named.h && short.face === short.h);
  S.state.boxText = "bogus";
  assert.equal(S.boxTextMode(), "short");
});

test("importance dots and the used-by badge: by how many classes on the map use it; a hub has its own badge", () => {
  assert.equal(S.importance(node({ used_by: 0 })), 1);
  assert.equal(S.importance(node({ used_by: 3 })), 2);
  assert.equal(S.importance(node({ used_by: 6 })), 3);
  assert.equal(S.importance(node({ used_by: 0, flags: ["hub"] })), 3);
  assert.equal(S.usedByBadge(node({ used_by: 8 })), "used by 8");
  assert.equal(S.usedByBadge(node({ used_by: 5 })), "");
  assert.equal(S.usedByBadge(node({ used_by: 30, flags: ["hub"] })), "");
});

test("quiet fan-out links are faint until one of their classes is hovered or selected", () => {
  const link = { from: "A", to: "B", quiet: true };
  S.state.nearIds = new Set();
  assert.equal(S.isFaint(link), true);
  S.state.nearIds = new Set(["B"]);
  assert.equal(S.isFaint(link), false);
  assert.equal(S.isFaint({ from: "A", to: "B" }), false, "an older helper's link (no quiet) is never faint");
});
