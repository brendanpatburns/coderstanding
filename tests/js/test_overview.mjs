// 30-second overview, pure part (viewer/overview.js). Run: node --test tests/js/test_overview.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const O = createRequire(import.meta.url)("../../viewer/overview.js");

test("chunks: domain names become chips, the rest stays text", () => {
  const out = O.chunks("You run Commands; Map data holds it.", ["Commands", "Map data"]);
  assert.deepEqual(out, [{ text: "You run " }, { domain: "Commands" }, { text: "; " }, { domain: "Map data" }, { text: " holds it." }]);
});

test("chunks: whole words only, longest name first", () => {
  assert.deepEqual(O.chunks("Viewers and Views", ["Views", "View"]), [{ text: "Viewers and " }, { domain: "Views" }]);
  assert.deepEqual(O.chunks("Map data and Map", ["Map", "Map data"]), [{ domain: "Map data" }, { text: " and " }, { domain: "Map" }]);
});

test("chunks: no names, empty sentence, regex characters in names", () => {
  assert.deepEqual(O.chunks("Plain.", []), [{ text: "Plain." }]);
  assert.deepEqual(O.chunks("", ["A"]), []);
  assert.deepEqual(O.chunks("Uses C++ now", ["C++"]), [{ text: "Uses " }, { domain: "C++" }, { text: " now" }]);
});

test("sentenceOf: the written note, else the generated one", () => {
  assert.deepEqual(O.sentenceOf({ sentence: "Mine.", generated: "Gen." }), { text: "Mine.", written: true });
  assert.deepEqual(O.sentenceOf({ sentence: null, generated: "Gen." }), { text: "Gen.", written: false });
});
