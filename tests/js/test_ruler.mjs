// Depth ruler helpers (viewer/ruler.js). Run: node --test tests/js/test_ruler.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const Ruler = createRequire(import.meta.url)("../../viewer/ruler.js");
const scale = { count: 3, names: ["Domains", "App", "Systems", "Parts"], counts: [0, 2, 10, 40] };
const looks = (cols) => cols.map((c) => c.look);

test("levels above the shown one are mid grey, deeper ones light", () => {
  assert.deepEqual(looks(Ruler.columns(scale, 2)), ["above", "above", "here", "deeper"]);
});

test("a zoom that jumped levels hatches the skipped ones", () => {
  assert.deepEqual(looks(Ruler.columns(scale, 3, 1)), ["above", "above", "skipped", "here"]);
});

test("a zoom one past the last level keeps the last column lit", () => {
  assert.equal(Ruler.columns(scale, 4, 3).find((c) => c.look === "here").level, 3);
});

test("level 0 is disabled without domains", () => {
  assert.equal(Ruler.columns(scale, 1)[0].disabled, true);
  assert.equal(Ruler.columns(scale, 1, null, true)[0].disabled, false);
});

test("bar heights scale with the count; empty levels stay visible", () => {
  assert.equal(Ruler.barHeight(40, 40), 26);
  assert.equal(Ruler.barHeight(0, 40), 2);
  assert.ok(Ruler.barHeight(1, 400) >= 3);
});

test("label is level, name and count", () => {
  assert.equal(Ruler.label(Ruler.columns(scale, 1)[2]), "2 Systems · 10");
});
