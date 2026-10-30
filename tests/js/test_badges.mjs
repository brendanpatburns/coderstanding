// Box badges (viewer/fanout.js): start mechanism and hub. Run: node --test tests/js/test_badges.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../../viewer/fanout.js", import.meta.url), "utf8");
const sandbox = {};
vm.runInNewContext(`${source}\nthis.boxBadges = boxBadges;`, sandbox);
const texts = (flags, extra = {}) => sandbox.boxBadges({ flags, ...extra }).map((b) => `${b.text}/${b.look}`);

test("start badges name the mechanism and drop the generic entry", () => {
  assert.deepEqual(texts(["godot-main", "entry"]), ["Godot main/strong"]);
  assert.deepEqual(texts(["godot-main-scene", "entry"]), ["Godot main scene/strong"]);
  assert.deepEqual(texts(["autoload", "entry", "singleton"]), ["Godot autoload/soft"]);
  assert.deepEqual(texts(["script-main", "entry"]), ["Script __main__/quiet"]);
  assert.deepEqual(texts(["entry"]), ["Entry point/quiet"]);
  assert.deepEqual(texts(["abstract"]), []);
});

test("a hub gets its own informative badge with the connection count", () => {
  assert.deepEqual(texts(["hub"], { hub_degree: 42 }), ["Hub · 42 connections/hub"]);
  assert.deepEqual(texts(["godot-main", "hub"], { hub_degree: 7 }), ["Godot main/strong", "Hub · 7 connections/hub"]);
});
