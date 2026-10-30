// Tours player logic: spoken words, word spans, the word-count timeline and the step player.
// Run: node --test tests/js/test_tours.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const T = createRequire(import.meta.url)("../../viewer/tours.js");

const STEP = { title: "Where a scan starts", targets: ["cli#run_scan"],
  text: "Everything begins with the scan command. [[cli#run_scan]] checks the folder." };

test("links speak as their names; the spoken text is title then text", () => {
  assert.equal(T.linkWords("cli#run_scan"), "cli run scan");
  assert.equal(T.linkWords("cli>scan"), "cli to scan");
  assert.equal(T.linkWords("tour:scan-pipeline"), "scan-pipeline");
  assert.equal(T.spokenText(STEP), "Where a scan starts. Everything begins with the scan command. cli run scan checks the folder.");
  assert.equal(T.spokenText({ title: "  ", text: "Just [[x]]" }), "Just x");
});

test("word spans: a link is one span that speaks as several words; counts add up", () => {
  const { title, body, total } = T.stepSpans(STEP);
  assert.equal(title.length, 4);
  assert.deepEqual(title.map((s) => s.start), [0, 1, 2, 3]);
  const link = body.find((s) => s.text === "[[cli#run_scan]]");
  assert.equal(link.count, 3);
  assert.equal(link.start, 4 + 6);  // after the title and "Everything begins with the scan command."
  const last = body.at(-1);
  assert.equal(last.start + last.count, total);
  assert.equal(total, T.words(T.spokenText(STEP)).length);
});

test("span state follows the spoken word: said, now, not yet", () => {
  const span = { start: 10, count: 3 };
  assert.equal(T.spanState(span, -1), "");
  assert.equal(T.spanState(span, 9), "");
  assert.equal(T.spanState(span, 10), "now");
  assert.equal(T.spanState(span, 12), "now");
  assert.equal(T.spanState(span, 13), "said");
});

test("timeline: duration by words, word at a position and back, boundary char index", () => {
  assert.equal(T.duration(5), 4);  // never shorter than 4 s
  assert.equal(T.duration(40), 40 * T.PACE);
  assert.equal(T.wordAt(0, 10, 20), 0);
  assert.equal(T.wordAt(5, 10, 20), 10);
  assert.equal(T.wordAt(10, 10, 20), 19);  // clamped to the last word
  assert.equal(T.wordAt(1, 10, 0), -1);
  assert.equal(T.positionOf(10, 10, 20), 5);
  assert.equal(T.wordsBefore("one two three four", 8), 2);
  assert.equal(T.fmtTime(75.9), "1:15");
});

test("source: a recording beats the voice and may be out of date", () => {
  assert.deepEqual(T.source(STEP), { kind: "voice", stale: false });
  assert.deepEqual(T.source({ audio: { rec: null } }), { kind: "voice", stale: false });
  assert.deepEqual(T.source({ audio: { rec: { by: "you", stale: true } } }), { kind: "rec", by: "you", stale: true });
});

test("voices: offer the page language, skip the default Afrikaans, plain before variants", () => {
  const voices = [
    { name: "Afrikaans espeak-ng", lang: "af", default: true },
    { name: "English (America)+Adam espeak-ng", lang: "en-US" },
    { name: "English (Great Britain) espeak-ng", lang: "en-GB" },
    { name: "English (America) espeak-ng", lang: "en-US" },
    { name: "en_US-amy-medium", lang: "en_US" },
  ];
  assert.deepEqual(T.voiceChoices(voices, "en-US").map((v) => v.name),
    ["English (Great Britain) espeak-ng", "English (America) espeak-ng", "en_US-amy-medium", "English (America)+Adam espeak-ng"]);
  assert.equal(T.pickVoice(voices, "", "en-US").name, "English (America) espeak-ng");
  assert.equal(T.pickVoice(voices, "English (America)+Adam espeak-ng", "en-US").name, "English (America)+Adam espeak-ng");
  assert.equal(T.pickVoice(voices, "gone", "de-DE").name, "Afrikaans espeak-ng");  // nothing matches: all offered
  assert.equal(T.pickVoice([], "", "en"), null);
});

test("recorder type: Opus in WebM, else Ogg, else browser default", () => {
  assert.equal(T.recorderType(() => true), "audio/webm;codecs=opus");
  assert.equal(T.recorderType((t) => t.startsWith("audio/ogg")), "audio/ogg;codecs=opus");
  assert.equal(T.recorderType(() => false), "");
});

// ---------- the player, with a fake browser ----------

function fakeEnv({ voices = [{ name: "v", lang: "en-US" }] } = {}) {
  const env = { time: 0, frames: [], timers: [], spoken: [], cancels: 0, ends: 0, notes: [] };
  env.synth = {
    getVoices: () => voices,
    speak: (u) => env.spoken.push(u),
    cancel: () => { env.cancels++; },
  };
  env.Utterance = class { constructor(text) { this.text = text; } };
  env.now = () => env.time;
  env.frame = (fn) => { env.frames.push(fn); return env.frames.length; };
  env.cancelFrame = () => {};
  env.later = (fn, ms) => { env.timers.push({ fn, at: env.time + ms }); return env.timers.length; };
  env.cancelLater = () => {};
  env.onEnd = () => { env.ends++; };
  env.onNote = (text) => env.notes.push(text);
  /** Advance the clock by `ms` in 50 ms frames, running due timers. */
  env.run = (ms) => {
    for (let left = ms; left > 0; left -= 50) {
      env.time += Math.min(50, left);
      const due = env.timers.filter((t) => t.at <= env.time);
      env.timers = env.timers.filter((t) => t.at > env.time);
      due.forEach((t) => t.fn());
      const frames = env.frames;
      env.frames = [];
      frames.forEach((fn) => fn());
    }
  };
  return env;
}

test("voice: speaks the whole text, boundary events move the slider, end finishes the step", () => {
  const env = fakeEnv();
  const player = new T.StepPlayer(env);
  player.load(STEP);
  assert.equal(player.word, -1);
  player.play();
  assert.equal(env.spoken.length, 1);
  const u = env.spoken[0];
  assert.equal(u.text, T.spokenText(STEP));
  u.onstart();
  u.onboundary({ name: "word", charIndex: u.text.indexOf("cli") });
  assert.equal(player.word, 10);
  u.onboundary({ name: "sentence", charIndex: 0 });  // not a word: ignored
  assert.equal(player.word, 10);
  u.onend();
  assert.equal(env.ends, 1);
  assert.equal(player.track.pos, player.track.dur);
});

test("voice without boundary events: the slider moves on the clock and holds until the voice ends", () => {
  const env = fakeEnv();
  const player = new T.StepPlayer(env);
  player.load(STEP);
  player.play();
  env.spoken[0].onstart();
  env.run(2000);
  assert.ok(player.word > 0, "moved on the clock");
  env.run(player.track.dur * 1000);
  assert.ok(player.track.pos <= player.track.dur * T.HOLD + 1e-9, "waits for the voice");
  assert.equal(env.ends, 0);
  env.spoken[0].onend();
  assert.equal(env.ends, 1);
});

test("seeking while speaking restarts the voice at the word under the slider", () => {
  const env = fakeEnv();
  const player = new T.StepPlayer(env);
  player.load(STEP);
  player.play();
  const old = env.spoken[0];
  const { total, dur } = player.track;
  player.seek(T.positionOf(10, dur, total) + 0.01);
  assert.equal(env.spoken.length, 2);
  assert.equal(env.spoken[1].text, T.words(T.spokenText(STEP)).slice(10).join(" "));
  assert.equal(player.word, 10);
  old.onend();  // the cancelled utterance's late end event does nothing
  assert.equal(env.ends, 0);
  env.spoken[1].onboundary({ name: "word", charIndex: env.spoken[1].text.indexOf("checks") });
  assert.equal(player.word, 13);
});

test("no voices: silent timeline with a note, then speaks once voices load late", () => {
  const voices = [];
  const env = fakeEnv({ voices });
  const player = new T.StepPlayer(env);
  player.load(STEP);
  player.play();
  assert.equal(env.spoken.length, 0);
  env.run(1200);
  assert.equal(env.notes.at(-1), T.NOTES.noVoice);
  voices.push({ name: "late", lang: "en-US" });
  player.voicesChanged();
  assert.equal(env.spoken.length, 1, "starts speaking from the current word");
  assert.ok(env.spoken[0].text.length < T.spokenText(STEP).length);
});

test("silent timeline runs to the end and finishes", () => {
  const env = fakeEnv({ voices: [] });
  const player = new T.StepPlayer(env);
  player.load(STEP);
  player.play();
  env.run(player.track.dur * 1000 + 100);
  assert.equal(env.ends, 1);
  assert.equal(player.word, player.track.total - 1);
});

test("a voice that never starts, or errors, falls back to the clock", () => {
  const env = fakeEnv();
  const player = new T.StepPlayer(env);
  player.load(STEP);
  player.play();
  env.run(T.START_TIMEOUT_MS + 100);
  assert.equal(env.notes.at(-1), T.NOTES.noStart);
  env.run(player.track.dur * 1000);
  assert.equal(env.ends, 1);

  const env2 = fakeEnv();
  const p2 = new T.StepPlayer(env2);
  p2.load(STEP);
  p2.play();
  env2.spoken[0].onerror({ error: "not-allowed" });
  assert.equal(env2.notes.at(-1), T.NOTES.blocked);
  env2.run(p2.track.dur * 1000 + 100);
  assert.equal(env2.ends, 1);
});

test("pause keeps the position; play resumes from that word", () => {
  const env = fakeEnv();
  const player = new T.StepPlayer(env);
  player.load(STEP);
  player.play();
  env.spoken[0].onstart();
  env.run(1500);
  player.pause();
  const word = player.word;
  assert.ok(word > 0);
  env.run(1000);
  assert.equal(player.word, word, "nothing moves while paused");
  player.play();
  assert.equal(env.spoken.at(-1).text, T.words(T.spokenText(STEP)).slice(word).join(" "));
});

test("recording: plays the audio element, follows its time, ends with it; no speech", () => {
  const env = fakeEnv();
  const audio = { currentTime: 0, duration: Infinity, play: () => Promise.resolve(), pause: () => {} };
  env.makeAudio = (url) => { audio.url = url; return audio; };
  const player = new T.StepPlayer(env);
  player.load({ ...STEP, audio: { rec: { by: "you", dur: 10, url: "/a.webm", stale: false } } });
  audio.onloadedmetadata();  // Infinity: keeps the measured length
  assert.equal(player.track.dur, 10);
  player.play();
  assert.equal(env.spoken.length, 0);
  audio.currentTime = 5;
  env.run(50);
  assert.equal(player.word, Math.floor(player.track.total / 2));
  player.seek(8);
  assert.equal(audio.currentTime, 8);
  audio.onended();
  assert.equal(env.ends, 1);
});
