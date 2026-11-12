// Focus + "In plain words" panel: the focus set (viewer/fanout.js) and the panel builders (viewer/field_help.js).
// Run: node --test tests/js/test_plain_panel.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../../viewer/fanout.js", import.meta.url), "utf8");
const sandbox = { state: {} };
vm.runInNewContext(`${source}\nthis.focusSet = focusSet; this.outOfFocus = outOfFocus;`, sandbox);
const { focusSet, outOfFocus } = sandbox;

const FieldHelp = createRequire(import.meta.url)("../../viewer/field_help.js");
const built = JSON.parse(readFileSync(new URL("../../docs/field_rules.json", import.meta.url), "utf8"));
FieldHelp.setRules({ rule: "", house: "", fields: {}, words: built.words });

const line = (from, to, extra = {}) => ({ key: `${from}→${to}`, from, to, kinds: ["dependency"], calls: [], ...extra });
const EDGES = [line("A", "B"), line("C", "A"), line("B", "D"), line("D", "E")];
const sorted = (set) => [...set].sort();

test("focus set of a class: itself, every line touching it and the classes at their other end", () => {
  const set = focusSet(EDGES, ["A"]);
  assert.deepEqual(sorted(set.nodes), ["A", "B", "C"]);
  assert.deepEqual(sorted(set.lines), ["A→B", "C→A"]);
});

test("focus set of a line: the line and both its ends only", () => {
  const set = focusSet(EDGES, [], ["B→D"]);
  assert.deepEqual(sorted(set.nodes), ["B", "D"]);
  assert.deepEqual(sorted(set.lines), ["B→D"]);
});

test("focus set of a tour step: several targets and lines together", () => {
  const set = focusSet(EDGES, ["A", "E"], ["B→D"]);
  assert.deepEqual(sorted(set.nodes), ["A", "B", "C", "D", "E"]);
  assert.deepEqual(sorted(set.lines), ["A→B", "B→D", "C→A", "D→E"]);
});

test("fan-out links fade only when the focus set leaves them all out", () => {
  const set = focusSet(EDGES, ["A"]);
  assert.equal(outOfFocus([{ edge: "A→B" }], set), false);
  assert.equal(outOfFocus([{ edge: "D→E" }], set), true);
  assert.equal(outOfFocus([{ edge: "D→E" }, { edge: "C→A" }], set), false);
  assert.equal(outOfFocus([{ from: "C" }], set), false, "a link with no class line: by its class");
  assert.equal(outOfFocus([{ edge: "D→E" }], null), false, "nothing selected: nothing fades");
});

test("plain lines: used by and uses, explained first, kind word and first call for the rest", () => {
  const edges = [line("A", "B", { calls: [{ member: "run" }] }), line("A", "C", { hint: "asks C for the map" }),
    line("D", "A"), line("A", "A")];
  const [usedBy, uses] = FieldHelp.plainLines("A", edges, { nameOf: (id) => id.toLowerCase() });
  assert.deepEqual(usedBy.map((l) => l.other), ["d"]);
  assert.deepEqual(uses.map((l) => [l.other, l.hint, l.call]), [["c", "asks C for the map", ""], ["b", "", "run"]]);
  assert.equal(uses[1].word, "uses");
  assert.equal(uses[1].key, "A→B");
});

test("class block: what it does, where it sits, lists with Ask AI on un-hinted lines", () => {
  const html = FieldHelp.classPlainHtml({ name: "scan", where: ["Scanning", "level 1 Systems", "12 functions"], warnings: 2,
    what: "Reads your code <fast>", usedBy: [{ key: "cli→scan", other: "cli", hint: "", word: "uses", call: "scan_repo" }],
    uses: [{ key: "scan→model", other: "model", hint: "fills the map", word: "uses", call: "" }] });
  assert.match(html, /<section class="plain" aria-label="In plain words">/);
  assert.match(html, /Scanning · level 1 Systems · 12 functions · <span class="warn">2 warnings<\/span>/);
  assert.match(html, /Reads your code &lt;fast&gt;/, "escaped");
  assert.match(html, /Used by \(1\)/);
  assert.match(html, /<b>cli<\/b> <span class="nonote">— uses scan_repo \(no note yet\)<\/span><button class="btn ask"[^>]*data-act="ask-line" data-key="cli→scan"/);
  assert.match(html, /<b>model<\/b> — fills the map/);
  assert.doesNotMatch(html, /Claude/);
});

test("class block: no description asks the AI; empty lists say so; long lists fold", () => {
  const many = Array.from({ length: 12 }, (_, i) => ({ key: `k${i}`, other: `c${i}`, hint: "h", word: "uses", call: "" }));
  const html = FieldHelp.classPlainHtml({ name: "main", uses: many });
  assert.match(html, /No description yet <button[^>]*data-act="ask-class"/);
  assert.match(html, /Nothing at this level: it starts things\./);
  assert.match(html, /Show all 12/);
});

test("line block: the line sentence, Ask AI only without a hint", () => {
  const plain = FieldHelp.linePlainHtml({ from: "cli", to: "views", kind: "dependency", hint: "", calls: "compute_view", key: "cli→views" });
  assert.match(plain, /<b>cli<\/b> uses <b>views<\/b>/);
  assert.match(plain, /data-act="ask-line" data-key="cli→views"/);
  const hinted = FieldHelp.linePlainHtml({ from: "cli", to: "views", kind: "dependency", hint: "asks what to print", calls: "", key: "cli→views" });
  assert.match(hinted, /: asks what to print/);
  assert.doesNotMatch(hinted, /ask-line/);
});
