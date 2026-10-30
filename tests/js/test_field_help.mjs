// Every editable note field has built-in rules (docs/field_rules.json), and the "?" tips show them.
// Run: node --test tests/js/test_field_help.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";

const FieldHelp = createRequire(import.meta.url)("../../viewer/field_help.js");
const read = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), "utf8");
const built = JSON.parse(read("docs/field_rules.json"));
const withCustom = (fields) => ({ rule: built.rule, house: "", fields: Object.fromEntries(Object.entries(fields).map(([n, f]) => [n, { ...f, custom: [] }])) });

test("every viewer note field and every text field of the store has rules", () => {
  const viewer = [...read("viewer/app.js").matchAll(/\{ name: "(\w+)", label:/g)].map((m) => m[1]);
  const store = read("scanner/coderstanding/notes.py").match(/TEXT_FIELDS = \(([^)]*)\)/)[1].match(/\w+/g);
  assert.ok(viewer.length >= 5 && store.length >= 5);
  FieldHelp.setRules(withCustom(built.fields));
  for (const name of [...viewer, ...store, "level", "domain"]) {
    for (const key of ["what", "who", "not", "good", "bad"]) assert.ok(built.fields[name]?.[key], `${name}.${key}`);
    assert.match(FieldHelp.helpButton(name), /aria-describedby="help-/);
  }
  assert.equal(FieldHelp.helpButton("nope"), "");
});

test("the tip shows who, what goes in, what does not, and both examples", () => {
  FieldHelp.setRules(withCustom(built.fields));
  const tip = FieldHelp.helpButton("hint");
  for (const key of ["what", "who", "not", "good"]) assert.ok(tip.includes(built.fields.hint[key].replace(/&/g, "&amp;")), key);
  assert.match(tip, /Bad:/);
  assert.match(FieldHelp.helpList(), /Edit field rules/);
});

test("a project override and house rules show up in the tips and the list", () => {
  const fields = { ...built.fields, hint: { ...built.fields.hint, what: "Team wording." } };
  const rules = withCustom(fields);
  rules.fields.hint.custom = ["what"];
  rules.house = "Say pod not node.";
  FieldHelp.setRules(rules);
  assert.match(FieldHelp.helpButton("hint"), /Team wording\./);
  assert.match(FieldHelp.helpList(), /Say pod not node\./);
  assert.match(FieldHelp.helpList(), /\(this project\)/);
});

test("help says AI, never Claude", () => {
  assert.doesNotMatch(JSON.stringify(built), /claude/i);
});
