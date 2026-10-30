// Details-panel list helpers: merge calls by member, fold long lists. Run: node --test tests/js/test_lists.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const { mergeCalls, collapsedList } = createRequire(import.meta.url)("../../viewer/lists.js");

test("mergeCalls sums by member, sorts by count, keeps distinct callers", () => {
  const merged = mergeCalls([
    { from_member: "a", member: "ACCENT", count: 3 },
    { from_member: "b", member: "ACCENT", count: 2 },
    { from_member: "a", member: "ACCENT", count: 1 },
    { from_member: "a", member: "run", count: 9 },
    { from_member: null, member: "idle", count: 1 },
  ]);
  assert.deepEqual(merged.map((m) => [m.member, m.count]), [["run", 9], ["ACCENT", 6], ["idle", 1]]);
  assert.deepEqual(merged[1].callers, ["a", "b"]);
  assert.deepEqual(merged[2].callers, []);
});

test("collapsedList folds everything after the first 8", () => {
  const items = Array.from({ length: 20 }, (_, i) => `<li>${i}</li>`);
  const html = collapsedList(items);
  assert.match(html, /Show all 20<\/summary>/);
  assert.equal((html.match(/<li>/g) || []).length, 20);
  assert.equal(html.split("<details>")[0].match(/<li>/g).length, 8);
  assert.doesNotMatch(collapsedList(items.slice(0, 8)), /details/);
  assert.equal(collapsedList([]), "");
});
