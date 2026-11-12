// Plain words: legend entries, the line sentence, the Words list, and the old-helper fallback.
// Run: node --test tests/js/test_plain_words.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";

const FieldHelp = createRequire(import.meta.url)("../../viewer/field_help.js");
const built = JSON.parse(readFileSync(new URL("../../docs/field_rules.json", import.meta.url), "utf8"));
const withWords = () => FieldHelp.setRules({ rule: "", house: "", fields: {}, words: built.words });
const withoutWords = () => FieldHelp.setRules({ rule: "", house: "", fields: {} });  // an older helper

test("legend: the kinds in the view plus hidden ones, plain word first with the UML name", () => {
  withWords();
  const entries = FieldHelp.legendEntries(["dependency", "derived"], ["signal"]);
  assert.deepEqual(entries.map((e) => e.kind), ["dependency", "signal", "derived"]);
  assert.equal(entries[0].word, "uses");
  assert.equal(entries[0].uml, "dependency");
  assert.deepEqual(entries.map((e) => e.hidden), [false, true, false]);
});

test("line sentence: A uses B: hint, the tip and the calls; no note says so", () => {
  withWords();
  const html = FieldHelp.lineTipHtml({ from: "cli", to: "offline", kind: "dependency", hint: "Every command <b>switches</b> it on", calls: "install, check" });
  assert.match(html, /<b>cli<\/b> uses <b>offline<\/b>: Every command &lt;b&gt;switches/);  // note text is escaped
  assert.match(html, /uses = calls its functions/);
  assert.match(html, /calls: install, check/);
  assert.match(FieldHelp.lineTipHtml({ from: "a", to: "b", kind: "derived", hint: "", calls: "" }), /through hidden classes <b>b<\/b> <span class="muted">\(no note yet\)/);
});

test("an older helper without words: today's labels, no UML names, no glossary, nothing throws", () => {
  withoutWords();
  const entries = FieldHelp.legendEntries(["composition", "loaded-by"], []);
  assert.deepEqual(entries.map((e) => e.word), ["composition", "loaded by (by name)"]);
  assert.ok(entries.every((e) => e.uml === "" && e.tip === ""));
  assert.equal(FieldHelp.wordsList(), "");
  assert.doesNotMatch(FieldHelp.lineTipHtml({ from: "a", to: "b", kind: "signal", hint: "x", calls: "" }), /class="k"/);
  assert.doesNotThrow(() => FieldHelp.explainTerms({ querySelectorAll: () => [] }));
});

test("the Words list holds every line kind and term of the table", () => {
  withWords();
  const list = FieldHelp.wordsList();
  for (const kind of Object.values(built.words.kinds)) assert.ok(list.includes(kind.word), kind.word);
  for (const term of Object.keys(built.words.terms)) assert.ok(list.includes(`<dt>${term}</dt>`), term);
});
