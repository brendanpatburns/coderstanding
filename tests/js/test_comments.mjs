// Comments panel logic: threads, open counts per target and per code line, safe Markdown.
// Run: node --test tests/js/test_comments.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const C = createRequire(import.meta.url)("../../viewer/comments.js");

const LIST = [  // the server's (thread, time) order
  { id: "a", thread: "a", target: "tour:t", resolved: null },
  { id: "b", thread: "b", target: "tour:t#k1", line: 12, resolved: null },
  { id: "c", thread: "b", target: "tour:t#k1", parent: "b", resolved: null },
  { id: "d", thread: "d", target: "tour:t#k1", line: 12, resolved: { by: "ana" } },
  { id: "e", thread: "e", target: "tour:t#k2", resolved: null },
];

test("threads group replies under their first comment", () => {
  const threads = C.threadsOf(LIST);
  assert.deepEqual(threads.map((t) => [t.root.id, t.replies.map((r) => r.id)]),
    [["a", []], ["b", ["c"]], ["d", []], ["e", []]]);
});

test("open counts per target and per line skip resolved threads", () => {
  assert.deepEqual(C.openCounts(LIST), { "tour:t": 1, "tour:t#k1": 1, "tour:t#k2": 1 });
  assert.deepEqual(C.lineCounts(LIST, C.stepTarget("t", "k1")), { 12: 1 });
  assert.deepEqual(C.lineCounts(LIST, "tour:t#k2"), {});
});

test("Markdown is escaped first, then code, bold, links and breaks", () => {
  const html = C.mdHtml("**Rename** `hp` in [[Player#hit]]\n<b>no</b>\n\nnext");
  assert.equal(html, '<p><b>Rename</b> <code>hp</code> in <span class="wl" data-target="Player#hit" role="link" tabindex="0" title="Select Player#hit">Player#hit</span><br>&lt;b&gt;no&lt;/b&gt;</p><p>next</p>');
});
