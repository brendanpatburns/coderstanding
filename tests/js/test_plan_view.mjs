// Planned work, viewer side (viewer/plan_view.js, DESIGN 7f): box tabs, footers, row marks.
// Run: node --test tests/js/test_plan_view.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const read = (f) => readFileSync(new URL(`../../viewer/${f}`, import.meta.url), "utf8");
const sandbox = {};
vm.runInNewContext(`${read("plan_view.js")}\n${read("fanout.js")}\nObject.assign(this, { PlanView, boxBadges, fnText });`, sandbox);
const { PlanView, boxBadges, fnText } = sandbox;

test("a planned box's tab says Planned, pink while an AI draft; a manual one Added manually", () => {
  assert.deepEqual(boxBadges({ flags: [], made: "planned" }).map((b) => `${b.text}/${b.look}`), ["Planned/planned"]);
  assert.deepEqual(boxBadges({ flags: [], made: "planned", made_ai: true }).map((b) => `${b.text}/${b.look}`), ["Planned · AI draft/draft"]);
  assert.deepEqual(boxBadges({ flags: ["entry"], made: "manual" }).map((b) => b.text), ["Added manually", "Entry point"]);
  assert.equal(boxBadges({ flags: [] }).length, 0);
  assert.equal(PlanView.faceClass({ made: "planned", made_ai: true }), " made-planned made-ai");
  assert.equal(PlanView.faceClass({}), "");
});

test("footer: the plan and 'not in the code yet', or where a manual thing lives", () => {
  assert.equal(PlanView.footText({ made: "planned", stage: "todo", stage_from: "own", priority: "high" }), "todo · work: high · not in the code yet");
  assert.equal(PlanView.footText({ made: "planned", stage: "review", stage_from: "spec" }), "review (spec) · not in the code yet");
  assert.equal(PlanView.footText({ made: "manual", where: "deploy/hosted.json" }), "where: deploy/hosted.json");
  assert.equal(PlanView.footText({ made: "manual" }), "where: unknown");
});

test("function rows: planned and stub marks after the name", () => {
  assert.equal(fnText({ name: "add", made: "planned" }), "add()  planned");
  assert.equal(fnText({ name: "clear", stub: true }), "clear()  stub");
  assert.equal(fnText({ name: "real" }), "real()");
});
