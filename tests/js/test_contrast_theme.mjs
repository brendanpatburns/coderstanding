// High-contrast theme tokens (viewer/style.css :root[data-theme="contrast"]): WCAG ratios of the key pairs.
// Run: node --test tests/js/test_contrast_theme.mjs
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const root = new URL("../../viewer/", import.meta.url);
const css = fs.readFileSync(new URL("style.css", root), "utf8");
const chips = { };
vm.runInNewContext(`${fs.readFileSync(new URL("typechips.js", root), "utf8")}\nthis.api = { contrast, TYPE_COLORS };`, chips);
const { contrast } = chips.api;
const chips2 = chips.api;

const block = css.match(/:root\[data-theme="contrast"\]\s*\{([^}]*)\}/)[1];
const tok = Object.fromEntries([...block.matchAll(/--([\w-]+):\s*([^;}]+?)\s*(?=;|$)/g)].map((m) => [m[1], m[2]]));
const ratio = (a, b) => contrast(tok[a], tok[b]);

test("contrast theme is dark-scheme with a near-black ground", () => {
  assert.match(block, /color-scheme:\s*dark/);
  assert.ok(ratio("bg", "ink") >= 19);
});

test("text tokens pass AAA (7:1) on bg, surface and row", () => {
  for (const text of ["ink", "muted", "faded", "com", "kw", "str", "done", "accent", "hl", "derived", "signal", "error", "warn"]) {
    for (const ground of ["bg", "surface", "row"]) {
      assert.ok(ratio(text, ground) >= 7, `${text} on ${ground}: ${ratio(text, ground).toFixed(2)}`);
    }
  }
});

test("lines and dimmed things stay visible", () => {
  assert.ok(ratio("line", "bg") >= 4.5, `line ${ratio("line", "bg").toFixed(2)}`);
  assert.ok(ratio("dim", "bg") >= 4.5, `dim ${ratio("dim", "bg").toFixed(2)}`);
});

test("text on filled accents and highlights", () => {
  assert.ok(ratio("accent-ink", "accent") >= 7);
  assert.ok(ratio("on-hl", "hl") >= 7);
  assert.ok(ratio("caution-ink", "caution") >= 7);
  assert.ok(ratio("ink", "hl-soft") >= 7, "line-note callout text");
  assert.ok(ratio("ink", "scene") >= 7 && ratio("ink", "ext") >= 7);
});

test("accents differ in hue and lightness from each other", () => {
  const names = ["accent", "hl", "derived", "signal", "error", "warn", "str"];
  for (let i = 0; i < names.length; i++) for (let j = i + 1; j < names.length; j++) {
    assert.notEqual(tok[names[i]], tok[names[j]], `${names[i]} vs ${names[j]}`);
  }
});

test("thicker focus ring", () => {
  assert.equal(tok["focus-w"], "3px");
});

// ---- extreme theme: pure black and white, signal colours >= 12:1, no fades ----
const extremeBlock = css.match(/:root\[data-theme="extreme"\]\s*\{([^}]*)\}/)[1];
const ext = Object.fromEntries([...extremeBlock.matchAll(/--([\w-]+):\s*([^;}]+?)\s*(?=;|$)/g)].map((m) => [m[1], m[2]]));
const extRatio = (a, b) => contrast(ext[a], ext[b]);

test("extreme: pure black ground, pure white text, no grey text", () => {
  assert.equal(ext.bg, "#000000");
  for (const name of ["ink", "muted", "faded", "com", "line", "dim"]) assert.equal(ext[name], "#ffffff", name);
  for (const name of ["surface", "row", "ext", "scene", "hl-soft"]) assert.equal(ext[name], "#000000", name);
});

test("extreme: every text and line colour is at least 12:1 on black", () => {
  for (const name of ["ink", "muted", "line", "accent", "hl", "derived", "signal", "error", "warn", "kw", "str", "done", "caution"]) {
    assert.ok(extRatio(name, "bg") >= 12, `${name} ${ext[name]}: ${extRatio(name, "bg").toFixed(2)}`);
  }
  assert.ok(extRatio("accent-ink", "accent") >= 12 && extRatio("on-hl", "hl") >= 12 && extRatio("caution-ink", "caution") >= 12);
});

test("extreme: nothing fades, dimmed things are dashed instead", () => {
  for (const name of ["op-dim", "op-faint", "op-soft"]) assert.equal(ext[name], "1", name);
  assert.equal(ext["dim-line"], "dashed");
  assert.notEqual(ext["dim-dash"], "none");
  assert.match(css, /--op-dim: \.25/, "other themes keep their fades");
});

test("extreme: thicker lines, focus ring and font weight", () => {
  assert.equal(ext["focus-w"], "4px");
  assert.ok(parseFloat(ext["line-boost"]) >= 1 && parseInt(ext.bw) >= 2 && parseInt(ext.fw) >= 600);
  assert.match(extremeBlock, /color-scheme:\s*dark/);
});

test("extreme: signal colours differ from each other", () => {
  const names = ["accent", "hl", "derived", "signal", "error", "warn"];
  for (let i = 0; i < names.length; i++) for (let j = i + 1; j < names.length; j++) assert.notEqual(ext[names[i]], ext[names[j]]);
});

test("extreme: chip colours are black-text signal colours", () => {
  const palette = chips2.TYPE_COLORS.extreme;
  for (const [type, bg] of Object.entries(palette)) assert.ok(contrast(bg, "#000000") >= 12, type);
});
