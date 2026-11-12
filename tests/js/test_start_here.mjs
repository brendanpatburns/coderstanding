// Start-here step numbers (viewer/start_here.js). Run: node --test tests/js/test_start_here.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const StartHere = createRequire(import.meta.url)("../../viewer/start_here.js");
const steps = [{ focus: ["cli"] }, { focus: ["views", "model"] }, { focus: [] }, {}, { focus: ["cli"] }];

test("a start tour numbers the classes each step focuses, from 1", () => {
  const numbers = StartHere.numbersOf({ start: true, steps });
  assert.deepEqual([...numbers], [["cli", [1, 5]], ["views", [2]], ["model", [2]]]);
});

test("a tour that is not a start tour, or none, gets no numbers", () => {
  assert.equal(StartHere.numbersOf({ start: false, steps }).size, 0);
  assert.equal(StartHere.numbersOf(null).size, 0);
});
