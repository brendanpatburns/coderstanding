// Tour line notes (viewer/tours.js): when each popup shows, and the editor's notes box.
// Run: node --test tests/js/test_line_notes.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const T = createRequire(import.meta.url)("../../viewer/tours.js");

const STEP = { title: "Where a scan starts", targets: ["cli#run_scan"],
  text: "Everything begins with the scan command. [[cli#run_scan]] checks the folder." };

test("a cue pops its note at that spoken word (links count as their spoken words)", () => {
  assert.deepEqual(T.lineNoteCues(STEP, [{ cue: "Checks the" }]), [13]);
});

test("notes without a cue (or with one not in the text) spread in order over the text", () => {
  assert.deepEqual(T.lineNoteCues(STEP, [{}, { cue: "checks" }, { cue: "nowhere" }]), [4, 13, 10]);
  assert.deepEqual(T.lineNoteCues({ title: "T", text: "" }, [{}]), [1]);
});

test("all notes show before playing; while playing, the ones reached", () => {
  assert.deepEqual(T.lineNotesShown([4, 13], -1), [true, true]);
  assert.deepEqual(T.lineNotesShown([4, 13], 0), [false, false]);
  assert.deepEqual(T.lineNotesShown([4, 13], 13), [true, true]);
  assert.deepEqual(T.lineNotesShown([4, 13], 5), [true, false]);
});

test("the editor's notes box round-trips and names a bad line", () => {
  const notes = [{ from: 50, to: 51, text: "Builds the graph.", cue: null }, { from: 52, to: 52, text: "Saves it: done.", cue: "saves it" }];
  const text = T.lineNotesText(notes);
  assert.equal(text, "50-51: Builds the graph.\n52: Saves it: done. @ saves it");
  assert.deepEqual(T.parseLineNotes(text), { notes, error: "" });
  assert.deepEqual(T.parseLineNotes("\n  \n"), { notes: [], error: "" });
  assert.match(T.parseLineNotes("50: ok\nno lines here").error, /not "no lines here"/);
  assert.match(T.parseLineNotes("52-50: backwards").error, /52-50/);
});
