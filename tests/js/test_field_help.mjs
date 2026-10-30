// Every editable note field has help text, and the docs repeat it. Run: node --test tests/js/test_field_help.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";

const { FIELD_HELP, helpButton } = createRequire(import.meta.url)("../../viewer/field_help.js");
const read = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), "utf8");

test("every viewer note field and every text field of the store has help", () => {
  const viewer = [...read("viewer/app.js").matchAll(/\{ name: "(\w+)", label:/g)].map((m) => m[1]);
  const store = read("scanner/coderstanding/notes.py").match(/TEXT_FIELDS = \(([^)]*)\)/)[1].match(/\w+/g);
  assert.ok(viewer.length >= 5 && store.length >= 5);
  for (const name of [...viewer, ...store, "level", "domain"]) {
    assert.ok(FIELD_HELP[name]?.text && FIELD_HELP[name]?.example, `help for ${name}`);
    assert.match(helpButton(name), /aria-describedby="help-/);
  }
  assert.equal(helpButton("nope"), "");
});

test("docs/FOR_CLAUDE.md repeats the help wording", () => {
  const docs = read("docs/FOR_CLAUDE.md");
  for (const [name, help] of Object.entries(FIELD_HELP)) assert.ok(docs.includes(help.text), `docs text for ${name}`);
});

test("help says AI, never Claude", () => {
  assert.doesNotMatch(JSON.stringify(FIELD_HELP), /claude/i);
});
