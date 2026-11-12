// Location refs (viewer/refs.js): formatting, links, clipboard parts. Run: node --test tests/js/test_refs.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";

const R = createRequire(import.meta.url)("../../viewer/refs.js");
const FIXTURE = JSON.parse(readFileSync(new URL("../fixtures/refs.json", import.meta.url), "utf8"));

/** The fixture's parsed parts (the backend's refs.parse_ref shape) through the viewer's builders. */
function targetOf(p) {
  const [from, to] = p.lines || [];
  switch (p.kind) {
    case "lines": return R.target.lines(p.path, from, to);
    case "target": return p.target;
    case "heading": return `${p.path}#${p.heading}`;
    case "tour": return R.target.tour(p.tour, p.step ?? null, from ?? null, to ?? null);
    case "comment": return R.target.comment(p.id);
    default: return R.target.card(p.kind, p.id);  // spec, memo
  }
}

test("formatRef writes every fixture ref from its parsed parts", () => {
  for (const { ref, canonical, parsed } of FIXTURE) {
    assert.equal(R.formatRef(parsed.app, targetOf(parsed), parsed.hint), canonical ?? ref);
  }
});

test("target builders write the fixture targets", () => {
  assert.equal(R.target.lines("src/net/peer.gd", 120, 134), "src/net/peer.gd:120-134");
  assert.equal(R.target.lines("src/net/peer.gd", 120, 120), "src/net/peer.gd:120");
  assert.equal(R.target.lines("src/net/peer.gd", 120), "src/net/peer.gd:120");
  assert.equal(R.target.member("Peer", "send_voice"), "Peer#send_voice");
  assert.equal(R.target.member("Peer"), "Peer");
  assert.equal(R.target.tour("first-steps"), "tour first-steps");
  assert.equal(R.target.tour("first-steps", 2), "tour first-steps step 2");
  assert.equal(R.target.tour("first-steps", 2, 8, 10), "tour first-steps step 2 lines 8-10");
  assert.equal(R.target.tour("first-steps", 2, 8), "tour first-steps step 2 lines 8");
  assert.equal(R.target.comment("c12"), "comment c12");
  assert.equal(R.target.card("memo", "ideas"), "memo ideas");
});

test("codeHint: unit plus offsets below its first line; no unit, no hint", () => {
  assert.equal(R.codeHint("Peer#send_voice", 116, 120, 134), "Peer#send_voice +4..+18");
  assert.equal(R.codeHint("Peer", 8, 10, 12), "Peer +2..+4");
  assert.equal(R.codeHint("Peer#send_voice"), "Peer#send_voice");        // first line unknown
  assert.equal(R.codeHint("Peer#f", 20, 10, 12), "Peer#f");              // lines start above the unit
  assert.equal(R.codeHint("", 1, 2, 3), "");
  assert.equal(R.formatRef("demo", R.target.lines("a.gd", 120, 134), R.codeHint("Peer#send_voice", 116, 120, 134)), "@demo a.gd:120-134 (Peer#send_voice +4..+18)");
  assert.equal(R.formatRef("demo", R.target.lines("a.gd", 1, 2), R.codeHint("")), "@demo a.gd:1-2");
});

test("lineRange sorts a backwards selection", () => {
  assert.deepEqual(R.lineRange(9, 4), [4, 9]);
  assert.deepEqual(R.lineRange(4, 9), [4, 9]);
});

test("refLink encodes the ref after app= and ref=", () => {
  const link = R.refLink("http://127.0.0.1:8517/", "demo", "@demo tour a step 2 (x & y)");
  assert.equal(link, "http://127.0.0.1:8517/?app=demo&ref=%40demo%20tour%20a%20step%202%20(x%20%26%20y)");
  assert.equal(new URL(link).searchParams.get("ref"), "@demo tour a step 2 (x & y)");
});

test("clipboardParts: ref lines as text, one escaped link per line as HTML", () => {
  const { text, html } = R.clipboardParts("demo", ["@demo Peer", "@demo memo <a>"], "http://h/");
  assert.equal(text, "@demo Peer\n@demo memo <a>");
  assert.equal(html, '<a href="http://h/?app=demo&amp;ref=%40demo%20Peer">@demo Peer</a><br><a href="http://h/?app=demo&amp;ref=%40demo%20memo%20%3Ca%3E">@demo memo &lt;a&gt;</a>');
});

test("copyButtons: Copy text and Copy location side by side; no text, no Copy text", () => {
  const both = R.copyButtons("@demo Peer", 'a "b" <c>');
  assert.match(both, /data-copy-text="a &quot;b&quot; &lt;c&gt;"[^>]*aria-label="Copy text"/);
  assert.match(both, /data-copy-ref="@demo Peer"[^>]*aria-label="Copy location"/);
  assert.ok(both.indexOf("Copy text") < both.indexOf("Copy location"));
  assert.equal(both.split("<svg").length, 3, "one icon each");
  assert.ok(!R.copyButtons("@demo Peer").includes("Copy text"));
  const menu = R.copyButtons("@demo Peer", "t", true);
  assert.match(menu, /^<div class="copy-menu" role="menu">/);
  assert.match(menu, /<span>Copy text<\/span>.*<span>Copy location<\/span>/s);
});
