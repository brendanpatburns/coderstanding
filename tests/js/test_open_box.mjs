// Open boxes (viewer/fanout.js): private and field groups fold into one row each; Self calls switch.
// Run: node --test tests/js/test_open_box.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../../viewer/fanout.js", import.meta.url), "utf8");
const sandbox = { state: null };
vm.runInNewContext(`${source}\nObject.assign(this, { isPrivateName, memberGroups, sectionsOf, anchorOf, shownLinks, hotFns, isHot, FAN });`, sandbox);
const S = sandbox;

const fns = (...names) => names.map((name) => ({ name }));
const fields = (n) => Array.from({ length: n }, (_, i) => ({ name: `f${i}`, kind: "var" }));
const setState = (parts, extra = {}) => {
  S.state = { current: { expanded: { A: parts }, fanout: extra.fanout || [] }, fnFilter: new Map(Object.entries(extra.filter || {})),
    listScroll: new Map(), openGroups: new Set(extra.open || []), fanHidden: extra.hidden || [] };
};
const rows = { fnRow: 24, fieldRow: 18 };
const plain = (x) => JSON.parse(JSON.stringify(x));  // values from the vm realm

test("private names: _x and __x, not Godot callbacks or Python dunders", () => {
  for (const name of ["_helper", "__secret", "_on_button_pressed"]) assert.ok(S.isPrivateName(name), name);
  for (const name of ["_ready", "_process", "_physics_process", "_input", "_unhandled_input", "_unhandled_key_input", "_gui_input",
    "_notification", "_init", "_enter_tree", "_exit_tree", "_draw", "_integrate_forces", "_get_configuration_warnings",
    "__init__", "run", "<init>"]) assert.ok(!S.isPrivateName(name), name);
});

test("memberGroups: public functions listed, private and properties as groups, empty groups dropped", () => {
  const g = S.memberGroups({ functions: fns("_ready", "go", "_helper"), properties: fields(3) });
  assert.deepEqual(plain(g.functions.map((f) => f.name)), ["_ready", "go"]);
  assert.deepEqual(plain(g.groups.map((x) => [x.kind, x.items.length])), [["private", 1], ["property", 3]]);
  assert.equal(S.memberGroups({ functions: fns("go"), properties: [] }).groups.length, 0);
});

test("sectionsOf: groups folded by default (one row each); opening one adds its rows", () => {
  const parts = { functions: fns("go", "stop", "_a", "_b"), properties: fields(12) };
  setState(parts);
  const folded = S.sectionsOf("A", rows);
  assert.deepEqual(plain(folded.groups.map((g) => [g.kind, g.open, g.top])), [["private", false, 48], ["property", false, 48 + S.FAN.groupRow]]);
  assert.equal(folded.height, 48 + 2 * S.FAN.groupRow + S.FAN.pad);
  setState(parts, { open: ["A#property"] });
  const open = S.sectionsOf("A", rows);
  assert.equal(open.groups[1].list.rows, S.FAN.visibleFields);
  assert.equal(open.height, folded.height + S.FAN.visibleFields * 18);
});

test("the filter narrows the private group too, and the box keeps its size", () => {
  const parts = { functions: fns("go", "_load_a", "_load_b", "_save"), properties: [] };
  setState(parts);
  const before = S.sectionsOf("A", rows).height;
  setState(parts, { filter: { A: "load" }, open: ["A#private"] });
  const after = S.sectionsOf("A", rows);
  assert.deepEqual(plain(after.groups[0].list.items.map((f) => f.name)), ["_load_a", "_load_b"]);
  assert.equal(after.fn.items.length, 0);
  setState(parts, { filter: { A: "load" } });
  assert.equal(S.sectionsOf("A", rows).height, before);
});

test("a line to a folded private function attaches to the group row; open, to its own row", () => {
  const parts = { functions: fns("go", "_a", "_b"), properties: [] };
  const box = { id: "A", x1: 0, x2: 200, y1: 0, top: 0, headH: 46, head: 46, open: true, ...rows };
  setState(parts);
  const groupY = 46 + 24 + S.FAN.groupRow / 2 + 2;
  assert.equal(S.anchorOf(box, "_b", "right").y, groupY);
  assert.equal(S.anchorOf(box, "go", "right").y, 46 + 12);
  setState(parts, { open: ["A#private"] });
  assert.equal(S.anchorOf(box, "_b", "right").y, 46 + 24 + S.FAN.groupRow + 24 + 12);
});

test("Self calls off: internal links are not drawn", () => {
  const fanout = [{ from: "A", to: "A", internal: true }, { from: "A", to: "B", internal: false }];
  setState({ functions: [], properties: [] }, { fanout });
  assert.equal(S.shownLinks(S.state.current).length, 2);
  setState({ functions: [], properties: [] }, { fanout, hidden: ["self"] });
  assert.deepEqual(plain(S.shownLinks(S.state.current).map((l) => l.to)), ["B"]);
});

test("tour step: a box lists the step's functions (private too), the rest fold into \"N more\"", () => {
  setState({ functions: fns("go", "_helper", "stop", "run"), properties: fields(2) },
    { fanout: [{ from: "A", from_fn: "go", to: "A", to_fn: "_helper" }, { from: "B", from_fn: "x", to: "A", to_fn: "run" }] });
  Object.assign(S.state, { tourStep: true });
  S.state.current.focus = { functions: ["A#go"] };
  const g = plain(S.memberGroups(S.state.current.expanded.A, "A"));
  assert.deepEqual(g.functions.map((f) => f.name), ["go", "_helper"]);
  assert.deepEqual(g.groups.map((x) => [x.kind, x.items.length]), [["more", 2], ["property", 2]]);
  S.state.tourStep = false;
  assert.deepEqual(plain(S.memberGroups(S.state.current.expanded.A, "A")).groups.map((x) => x.kind), ["private", "property"]);
});

test("tour step: every focused function's lines are lit until another function is hovered", () => {
  setState({ functions: fns("go", "stop"), properties: [] });
  Object.assign(S.state, { tourStep: true, activeFn: { cls: "A", fn: "go" } });
  S.state.current.focus = { functions: ["A#go", "B#run"] };
  const toB = { from: "C", from_fn: "x", to: "B", to_fn: "run" };
  assert.deepEqual([...S.hotFns()], ["A#go", "B#run"]);
  assert.equal(S.isHot(toB), true);
  S.state.activeFn = { cls: "A", fn: "stop" };
  assert.deepEqual([...S.hotFns()], ["A#stop"]);
  assert.equal(S.isHot(toB), false);
  Object.assign(S.state, { tourStep: false, activeFn: null });
  assert.equal(S.hotFns(), null);
});
