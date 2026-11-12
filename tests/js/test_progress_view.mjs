// Progress tracks, viewer side, pure part (viewer/progress_view.js). Run: node --test tests/js/test_progress_view.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

globalThis.esc = (text) => String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");  // app.js's, at call time
const P = createRequire(import.meta.url)("../../viewer/progress_view.js");

test("percentText: rounds down below 100, so only all done reads 100%", () => {
  assert.equal(P.percentText({ done: 1, total: 3 }), "33%");
  assert.equal(P.percentText({ done: 999, total: 1000 }), "99%");
  assert.equal(P.percentText({ done: 4, total: 4 }), "100%");
  assert.equal(P.percentText({ done: 0, total: 0 }), "-");
  assert.equal(P.percentText(null), "-");
});

test("ringDash: the done arc is the done share of the ring", () => {
  const [done, rest] = P.ringDash({ done: 1, total: 4 }, 10).split(" ").map(Number);
  assert.ok(Math.abs(done - (2 * Math.PI * 10) / 4) < 0.01);
  assert.ok(Math.abs(done + rest - 2 * Math.PI * 10) < 0.02);
  assert.equal(P.ringDash({ done: 0, total: 0 }, 10).split(" ")[0], "0.00");
});

test("pillText, barHtml and chipHtml say the track and the numbers", () => {
  assert.equal(P.pillText("Proven", { done: 1, total: 3 }), "Proven: 1 of 3 done (33%)");
  const bar = P.barHtml("Proven", { done: 1, total: 4 });
  assert.match(bar, /data-pct="25"/);
  assert.match(bar, /Proven 25% · 1\/4/);
  assert.equal(P.barHtml("Proven", { done: 0, total: 0 }), "");
  assert.equal(P.barHtml(null, { done: 1, total: 2 }), "");
  assert.match(P.chipHtml("Draft", { done: 2, total: 2 }), /class="chip prog full"[^>]*>100% Draft</);
  assert.match(P.chipHtml("A<b", { done: 1, total: 2 }), /50% A&lt;b/);
});
