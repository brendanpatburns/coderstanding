// View options table (viewer/view_options.js). Run: node --test tests/js/test_view_options.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";

const VO = createRequire(import.meta.url)("../../viewer/view_options.js");

function memoryStorage() {
  const items = new Map();
  return { getItem: (k) => (items.has(k) ? items.get(k) : null), setItem: (k, v) => items.set(k, String(v)), items };
}

test("every option has a key, URL name, type, default, label, group and scope; names are unique", () => {
  for (const o of VO.OPTIONS) {
    assert.ok(o.key && o.url && o.type && "def" in o && o.label && o.group && o.scope, o.key);
    if (o.type === "enum") assert.ok(o.values.includes(o.def), o.key);
  }
  assert.equal(new Set(VO.OPTIONS.map((o) => o.url)).size, VO.OPTIONS.length);
  assert.ok(!VO.option("theme"), "the theme is personal: not a view option");
});

test("the layout methods match layout.js", () => {
  const layout = readFileSync(new URL("../../viewer/layout.js", import.meta.url), "utf8");
  const methods = JSON.parse(layout.match(/const METHODS = (\[[^\]]*\])/)[1]);
  assert.deepEqual(VO.option("layout").values, methods);
});

test("parse reads URL text, stored values and rejects bad ones", () => {
  const bool = VO.option("selfCalls"), list = VO.option("hiddenKinds"), lines = VO.option("lines");
  assert.equal(VO.parse(bool, "0"), false);
  assert.equal(VO.parse(bool, "1"), true);
  assert.equal(VO.parse(bool, false), false);
  assert.equal(VO.parse(bool, 0), false);
  assert.equal(VO.parse(bool, "maybe"), undefined);
  assert.equal(VO.parse(lines, "ALL"), "all");
  assert.equal(VO.parse(lines, "loud"), undefined);
  assert.deepEqual(VO.parse(list, "signal, dependency,signal"), ["dependency", "signal"]);
  assert.deepEqual(VO.parse(list, ["b", "a"]), ["a", "b"]);
  assert.deepEqual(VO.parse(list, "signal"), ["signal"]);
  assert.equal(VO.parse(VO.option("level"), "3"), 3);
  assert.equal(VO.parse(lines, { a: 1 }), undefined);
});

test("fromUrl takes today's URL names; lists also as repeated names", () => {
  const values = VO.fromUrl("?level=3&view=concrete&tests=1&selfcalls=0&boxtext=full&lines=all&domains=1&between=every&overview=0&details=open&mode=edit&layout=hub&hide_kind=signal&hide_kind=dependency&boxtext_x=1&lines=");
  assert.deepEqual(values, { level: 3, view: "concrete", tests: true, selfCalls: false, boxText: "full", lines: "all", domains: true,
    between: "every", overview: false, details: "open", mode: "edit", layout: "hub", hiddenKinds: ["dependency", "signal"] });
  assert.deepEqual(VO.fromUrl("boxtext=saved"), {}, "a bad value leaves the option alone");
});

test("toUrl writes only what differs from the defaults, and fromUrl reads it back", () => {
  const values = VO.resolve({ lines: "all", selfCalls: false, hiddenKinds: ["signal"], external: true });
  const text = VO.toUrl(values).toString();
  assert.equal(text, "selfcalls=0&lines=all&hide_kind=signal");
  assert.deepEqual(VO.resolve(VO.fromUrl(text)), values);
});

test("diff lists the changed options; resolve merges layers over the defaults", () => {
  assert.deepEqual(VO.diff(VO.defaults()), {});
  assert.deepEqual(VO.diff(VO.resolve({ boxText: "name" })), { boxText: "name" });
  assert.deepEqual(VO.resolve(null, { lines: "all" }, undefined, { lines: "explained" }).lines, "explained");
  assert.deepEqual(VO.resolve({ nope: 1 }), VO.defaults(), "unknown keys are ignored");
});

test("precedence: URL > tour step > tour > remembered > preset > default", () => {
  const preset = VO.presetValues("read");                     // selfCalls off, external off
  const remembered = { external: true };                       // the user brought outside code back
  const tour = VO.fromStored({ lines: "all", selfcalls: true, boxtext: "name" }, { tourOnly: true });
  const step = VO.fromStored({ boxtext: "full" }, { tourOnly: true });
  const url = { boxText: "short" };
  const all = VO.resolve(preset, remembered, tour, step, url);
  assert.equal(all.external, true);
  assert.equal(all.selfCalls, true);
  assert.equal(all.lines, "all");
  assert.equal(all.boxText, "short");
  assert.equal(VO.resolve(preset, remembered, tour, step).boxText, "full");
  assert.equal(VO.resolve(preset).domains, true);
});

test("a stored tour view: URL names, unknown or non-tour options skipped (old and new tours load)", () => {
  assert.deepEqual(VO.fromStored({ lines: "all", selfcalls: 0, hide_kind: "signal,call", future_switch: 1, domains: true }, { tourOnly: true }),
    { lines: "all", selfCalls: false, hiddenKinds: ["call", "signal"] });
  assert.deepEqual(VO.fromStored(null), {});
  assert.deepEqual(VO.fromStored(["x"]), {});
  assert.deepEqual(VO.toStored({ selfCalls: false, hiddenKinds: ["signal"] }), { selfcalls: false, hide_kind: ["signal"] });
});

test("presets: Explore = the defaults, Read and Edit set their switches, Custom when one differs", () => {
  assert.deepEqual(VO.resolve(VO.presetValues("explore")), VO.defaults());
  assert.deepEqual(VO.presetSets("read").sort(), ["domains", "external", "selfCalls"].sort());
  assert.deepEqual(VO.presetSets("edit"), ["mode"]);
  assert.equal(VO.DEFAULT_PRESET, "read");
  const read = VO.resolve(VO.presetValues("read"));
  assert.ok(VO.isPreset(read, "read"));
  assert.ok(!VO.isPreset({ ...read, lines: "all" }, "read"));
  assert.ok(VO.isPreset({ ...read, filter: "tag:x", layout: "hub" }, "read"), "page-only and per-browser switches are not part of a preset");
  assert.equal(VO.presetFromUrl("?preset=Explore"), "explore");
  assert.equal(VO.presetFromUrl("?preset=wild"), null);
});

test("remembered: the preset plus the changed switches, per app (and per browser for layout and details)", () => {
  const storage = memoryStorage();
  assert.deepEqual(VO.readRemembered(storage, "a"), { preset: "read", changed: {} });
  let mem = VO.remember({ preset: "read", changed: {} }, "lines", "all");
  mem = VO.remember(mem, "selfCalls", false);  // what Read sets anyway: not a change
  mem = VO.remember(mem, "layout", "hub");
  assert.deepEqual(mem.changed, { lines: "all", layout: "hub" });
  VO.writeRemembered(storage, "a", { preset: "edit", changed: mem.changed });
  assert.deepEqual(VO.readRemembered(storage, "a"), { preset: "edit", changed: { lines: "all", layout: "hub" } });
  assert.deepEqual(VO.readRemembered(storage, "b"), { preset: "read", changed: { layout: "hub" } }, "layout is per browser");
  assert.deepEqual(VO.remember(mem, "lines", "explained").changed, { layout: "hub" }, "back to the preset's value: no change");
  const broken = { getItem: () => { throw new Error("private"); }, setItem: () => { throw new Error("private"); } };
  assert.deepEqual(VO.readRemembered(broken, "a"), { preset: "read", changed: {} });
  VO.writeRemembered(broken, "a", mem);  // no throw
});

test("valueText says a value in words", () => {
  assert.equal(VO.valueText("lines", "all"), "Lines: All equal");
  assert.equal(VO.valueText("selfCalls", false), "Self calls: off");
  assert.equal(VO.valueText("hiddenKinds", ["signal"]), "Hidden line kinds: signal");
});
