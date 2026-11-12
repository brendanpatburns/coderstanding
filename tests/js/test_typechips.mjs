// Type chips (viewer/typechips.js): colours, contrast, name wrapping. Run: node --test tests/js/test_typechips.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../../viewer/typechips.js", import.meta.url), "utf8");
const t = {};
vm.runInNewContext(`${source}\nthis.api = { TYPE_COLORS, fileType, typeColors, contrast, relativeLuminance, wrapName, wrapSub, wrapText, clampLines };`, t);
const { TYPE_COLORS, fileType, typeColors, contrast, relativeLuminance } = t.api;
const plain = (fn) => (...args) => [...fn(...args)];  // arrays from the sandbox have another prototype
const wrapName = plain(t.api.wrapName);
const wrapSub = plain(t.api.wrapSub);
const mono = (text) => text.length * 8;  // 8px per character

test("fileType is the lowercase extension, empty without one", () => {
  assert.equal(fileType("src/Player.GD"), ".gd");
  assert.equal(fileType("a.b/Makefile"), "");
  assert.equal(fileType(""), "");
});

test("every chip passes text contrast in every theme", () => {
  for (const theme of ["light", "dark", "contrast"]) {
    for (const type of Object.keys(TYPE_COLORS[theme])) {
      const { bg, ink } = typeColors(type, theme);
      assert.ok(contrast(bg, ink) >= 4.5, `${theme} ${type} ${contrast(bg, ink).toFixed(2)}`);
    }
  }
});

test("chip colours differ in lightness, not just hue", () => {
  for (const theme of ["light", "dark", "contrast"]) {
    const lums = Object.values(TYPE_COLORS[theme]).map(relativeLuminance).sort((a, b) => a - b);
    for (let i = 1; i < lums.length; i++) assert.ok((lums[i] + 0.05) / (lums[i - 1] + 0.05) >= 1.06, `${theme} step ${i}`);
  }
});

test("an unknown type is the neutral grey", () => {
  assert.equal(typeColors(".zzz", "light").bg, TYPE_COLORS.light.other);
});

test("a name that fits stays on one line", () => {
  assert.deepEqual(wrapName("Player", mono, 200), ["Player"]);
});

test("a long name wraps at camelCase, underscore and dot boundaries without losing text", () => {
  const camel = wrapName("InventoryItemSlotController", mono, 14 * 8);
  assert.deepEqual(camel, ["InventoryItem", "SlotController"]);
  assert.deepEqual(wrapName("player_input_handler_node", mono, 13 * 8), ["player_input_", "handler_node"]);
  assert.deepEqual(wrapName("Game.Net.Server", mono, 9 * 8), ["Game.Net.", "Server"]);
});

test("a single unbreakable word is cut by characters, never an ellipsis", () => {
  const lines = wrapName("Abcdefghijklmnop", mono, 6 * 8);
  assert.equal(lines.join(""), "Abcdefghijklmnop");
  assert.ok(lines.every((l) => mono(l) <= 48 && !l.includes("…")));
});

test("a sub-line wraps whole marks", () => {
  assert.deepEqual(wrapSub("3 functions · 2 fields · #net · draft", mono, 25 * 8), ["3 functions · 2 fields ·", "#net · draft"]);
});

test("wrapText breaks prose between words; clampLines keeps N lines and ends the last in an ellipsis that fits", () => {
  const wrapText = plain(t.api.wrapText);
  const clampLines = plain(t.api.clampLines);
  const lines = wrapText("Keeps every slot of the inventory in step with the items", mono, 80);
  assert.ok(lines.length > 2 && lines.every((l) => mono(l) <= 80 && l === l.trim()));
  assert.equal(lines.join(" "), "Keeps every slot of the inventory in step with the items");
  const two = clampLines(lines, 2, mono, 80);
  assert.equal(two.length, 2);
  assert.ok(two[1].endsWith("…") && mono(two[1]) <= 80, two[1]);
  assert.deepEqual(clampLines(["short"], 2, mono, 80), ["short"], "nothing cut: no ellipsis");
});
