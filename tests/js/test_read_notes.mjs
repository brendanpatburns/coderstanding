// Reading line notes aloud (viewer/tours.js StepPlayer): pause the step, speak the note, go on.
// Run: node --test tests/js/test_read_notes.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const T = createRequire(import.meta.url)("../../viewer/tours.js");

const STEP = { title: "Chunks", text: "Godot runs the main scene first. Then the player makes the helper. The helper does the work again and again." };
const CUE = 9;  // "Then the player ..." starts at spoken word 7; the note pops up a little later

/** A fake browser: the voice speaks only when a test says so; recording audio is a stub. */
function setup({ rec = false, voices = [{ name: "v", lang: "en-US", localService: true }], read = true, notes = [{ at: CUE, text: "Hands over to the helper." }] } = {}) {
  const env = { time: 0, frames: [], spoken: [], cancels: 0, ends: 0, notes: [], audioCalls: [] };
  env.synth = { getVoices: () => voices, speak: (u) => env.spoken.push(u), cancel: () => { env.cancels++; } };
  env.Utterance = class { constructor(text) { this.text = text; } };
  env.now = () => env.time;
  env.frame = (fn) => { env.frames.push(fn); return env.frames.length; };
  env.cancelFrame = () => {};
  env.later = () => 0;
  env.cancelLater = () => {};
  env.onEnd = () => { env.ends++; };
  env.onNote = (text) => env.notes.push(text);
  env.run = (ms) => {
    for (let left = ms; left > 0; left -= 50) {
      env.time += Math.min(50, left);
      const frames = env.frames;
      env.frames = [];
      frames.forEach((fn) => fn());
    }
  };
  env.makeAudio = () => {
    const a = { currentTime: 0, duration: 10, calls: env.audioCalls,
      play() { env.audioCalls.push("play"); this.playing = true; return Promise.resolve(); },
      pause() { env.audioCalls.push("pause"); this.playing = false; } };
    return a;
  };
  const player = new T.StepPlayer(env);
  player.readNotes = read;
  player.load(rec ? { ...STEP, audio: { rec: { url: "x", dur: 10 } } } : STEP);
  player.setNotes(notes);
  return { env, player };
}

/** Speak the queued narration utterances one by one up to (and including) the one that reaches
 *  `word`; returns that utterance. Fires onstart (and onend for the ones before it). */
function narrateUntil(env, player, word) {
  let first = 0;
  for (let k = 0; k < env.spoken.length; k++) {
    const u = env.spoken[k], n = T.words(u.text).length;
    u.onstart();
    if (first + n > word) return u;
    env.run(100);
    u.onend();
    first += n;
  }
  assert.fail("word not reached");
}

/** Let the clock run until a note starts being read (the highlight reaches its cue between voice events). */
function untilReading(env, player) {
  for (let i = 0; i < 400 && player.readingNote < 0; i++) env.run(50);
  assert.ok(player.readingNote >= 0, "a note started");
}

test("pause at the cue, read the note, resume the step from the same word", () => {
  const { env, player } = setup();
  player.play();
  const queued = env.spoken.length;
  narrateUntil(env, player, CUE);
  untilReading(env, player);
  const note = env.spoken.at(-1);
  assert.equal(note.text, "Hands over to the helper.");
  assert.ok(env.spoken.length > queued);
  const word = player.word, pos = player.track.pos;
  env.run(3000);  // the note takes a while: nothing moves
  assert.equal(player.word, word, "word held");
  assert.equal(player.track.pos, pos, "clock held");
  assert.equal(player.readingNote, 0);
  const before = env.spoken.length;
  note.onend();
  assert.equal(player.readingNote, -1);
  assert.ok(env.spoken.length > before, "narration queued again");
  assert.equal(env.spoken[before].text.split(" ")[0], T.words(T.spokenText(STEP))[word], "from the word it paused at");
  assert.equal(player.playing, true);
});

test("several notes at one word are read in order, then the step goes on", () => {
  const { env, player } = setup({ notes: [{ at: CUE, text: "First." }, { at: CUE, text: "Second." }] });
  player.play();
  narrateUntil(env, player, CUE);
  untilReading(env, player);
  assert.equal(env.spoken.at(-1).text, "First.");
  env.spoken.at(-1).onend();
  assert.equal(player.readingNote, 1);
  assert.equal(env.spoken.at(-1).text, "Second.");
  env.spoken.at(-1).onend();
  assert.equal(player.readingNote, -1);
});

test("each note is read once per play-through; seeking back before its cue reads it again", () => {
  const { env, player } = setup();
  player.play();
  narrateUntil(env, player, CUE);
  untilReading(env, player);
  env.spoken.at(-1).onend();
  env.run(500);
  assert.equal(player.readingNote, -1, "not read twice");
  env.spoken.length = 0;
  player.seek(0.1 * player.track.dur);  // before the cue
  for (const u of [...env.spoken]) {  // speak on until the note's turn
    if (player.readingNote >= 0) break;
    u.onstart();
    for (let i = 0; i < 20 && player.readingNote < 0; i++) env.run(50);
    if (player.readingNote < 0) u.onend();
  }
  assert.equal(player.readingNote, 0, "read again");
});

test("pause during a note: Play reads the rest of it, then the step", () => {
  const { env, player } = setup({ notes: [{ at: CUE, text: "Hands over to the helper now." }] });
  player.play();
  narrateUntil(env, player, CUE);
  untilReading(env, player);
  const note = env.spoken.at(-1);
  note.onboundary({ name: "word", charIndex: note.text.indexOf("helper") });
  player.pause();
  assert.equal(player.readingNote, 0, "still mid-note");
  const n = env.spoken.length;
  player.play();
  assert.equal(env.spoken[n].text, "helper now.", "the rest of the note");
  assert.equal(player.readingNote, 0);
  env.spoken[n].onend();
  assert.equal(player.readingNote, -1);
  assert.ok(env.spoken.length > n + 1, "then the step");
});

test("seek during a note cancels it cleanly; the old note's events do nothing", () => {
  const { env, player } = setup();
  player.play();
  narrateUntil(env, player, CUE);
  untilReading(env, player);
  const note = env.spoken.at(-1);
  player.seek(0.9 * player.track.dur);
  assert.equal(player.readingNote, -1);
  const n = env.spoken.length;
  note.onend();
  assert.equal(env.spoken.length, n, "stale end ignored");
  assert.ok(n > 0 && player.track.speaking, "narration restarted at the new spot");
  env.run(500);
  assert.equal(player.readingNote, -1, "a note before the new word is not read late");
});

test("a recording: the audio pauses, the voice reads the note, the audio resumes; the clock holds", () => {
  const { env, player } = setup({ rec: true });
  player.play();
  const audio = player.track.audio;
  audio.currentTime = 5;
  env.run(100);
  assert.equal(player.readingNote, 0);
  assert.equal(audio.playing, false);
  assert.equal(env.spoken.at(-1).text, "Hands over to the helper.");
  const pos = player.track.pos;
  env.run(2000);
  assert.equal(player.track.pos, pos);
  env.spoken.at(-1).onend();
  assert.equal(audio.playing, true);
  assert.equal(audio.currentTime, 5, "resumes where it paused");
  assert.equal(player.readingNote, -1);
});

test("a recording with no voice just shows the note: no pause", () => {
  const { env, player } = setup({ rec: true, voices: [] });
  player.play();
  player.track.audio.currentTime = 5;
  env.run(100);
  assert.equal(player.readingNote, -1);
  assert.equal(player.track.audio.playing, true);
});

test("option off, or silent clock mode: nothing changes", () => {
  const off = setup({ read: false });
  off.player.play();
  narrateUntil(off.env, off.player, CUE);
  off.env.run(200);
  assert.equal(off.player.readingNote, -1);
  assert.ok(!off.env.spoken.some((u) => u.text.startsWith("Hands")), "note not spoken");
  const silent = setup({ voices: [] });
  silent.env.synth = null;
  silent.player.play();
  silent.env.run(30000);
  assert.equal(silent.player.readingNote, -1);
  assert.equal(silent.env.ends, 1, "the step still ends on the clock");
});
