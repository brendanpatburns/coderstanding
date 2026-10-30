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

test("character timeline: word starts, word at a character, boundary char index", () => {
  const { starts, total } = T.charStarts(["one", "two", "three"]);
  assert.deepEqual(starts, [0, 4, 8]);
  assert.equal(total, 13);
  assert.equal(T.wordAtChar(starts, 0), 0);
  assert.equal(T.wordAtChar(starts, 3.9), 0);
  assert.equal(T.wordAtChar(starts, 4), 1);
  assert.equal(T.wordAtChar(starts, 99), 2);
  assert.equal(T.wordAtChar([], 1), -1);
  assert.equal(T.wordsBefore("one two three four", 8), 2);
  assert.equal(T.fmtTime(75.9), "1:15");
});

test("chunks: sentence ends, and commas once long enough; short pieces join", () => {
  const list = T.words("Hi. This is the first sentence. A clause, short; then a much longer clause that goes on and on, and the end. Ok.");
  const texts = T.chunksOf(list).map((c) => list.slice(c.first, c.last + 1).join(" "));
  assert.deepEqual(texts, [
    "Hi. This is the first sentence.",                                          // "Hi." is too short alone
    "A clause, short; then a much longer clause that goes on and on,",          // , ; too early; then long enough
    "and the end. Ok.",                                                         // "Ok." joins the tail
  ]);
  assert.deepEqual(T.chunksOf([]), []);
  assert.deepEqual(T.chunksOf(["one", "two"]), [{ first: 0, last: 1 }]);
});

test("pace book: moving average per voice, kept in storage, nonsense ignored", () => {
  const store = new Map();
  const storage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) };
  const book = new T.PaceBook(storage);
  assert.equal(book.get("v"), T.DEFAULT_PACE);
  book.learn("v", 0.05);
  assert.equal(book.get("v"), 0.05);
  book.learn("v", 0.001);  // cut short: not speech
  assert.equal(book.get("v"), 0.05);
  for (let i = 0; i < 12; i++) book.learn("v", 0.09);
  assert.ok(Math.abs(book.get("v") - 0.09) < 0.001, `converges: ${book.get("v")}`);
  assert.equal(new T.PaceBook(storage).get("v"), book.get("v"), "remembered");
  assert.equal(book.get("other"), T.DEFAULT_PACE);
  const broken = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } };
  const quiet = new T.PaceBook(broken);
  quiet.learn("v", 0.06);
  assert.equal(quiet.get("v"), 0.06, "works without storage");
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
  ].map((v) => ({ ...v, localService: true }));
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

test("voices: on-device only; online voices (Google, Microsoft Online) are never offered or picked", () => {
  const google = { name: "Google US English", lang: "en-US", localService: false, default: true };
  const edge = { name: "Microsoft Aria Online (Natural) - English (United States)", lang: "en-US", localService: false };
  const sapi = { name: "Microsoft David - English (United States)", lang: "en-US", localService: true };
  const unknown = { name: "no flag", lang: "en-US" };  // localService missing: not trusted
  const all = [google, edge, unknown, sapi];
  assert.deepEqual(T.localVoices(all), [sapi]);
  assert.deepEqual(T.voiceChoices(all, "en-US"), [sapi]);
  assert.equal(T.pickVoice(all, "", "en-US"), sapi);
  assert.equal(T.pickVoice(all, google.name, "en-US"), sapi, "a saved online voice is ignored");
  assert.equal(T.pickVoice([google, edge], "", "en-US"), null);
  assert.deepEqual(T.voiceChoices([google, edge], "en-US"), []);
});

test("only online voices: the player stays silent on its timeline with the no-voice note", () => {
  const env = fakeEnv({ voices: [{ name: "Google US English", lang: "en-US", localService: false }] });
  const player = new T.StepPlayer(env);
  player.load(STEP);
  player.play();
  env.run(1200);
  assert.equal(env.spoken.length, 0, "nothing sent to an online voice");
  assert.equal(env.notes.at(-1), T.NOTES.noVoice);
});

// ---------- the player, with a fake browser ----------

function fakeEnv({ voices = [{ name: "v", lang: "en-US", localService: true }] } = {}) {
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

const LONG = { title: "Chunks",
  text: "Godot runs the main scene's script first. [[res://src/main.gd#_ready]] makes the player and the big helper, then announces the start on the bus. Each frame the player ticks; the big helper carries two more steps of work, again and again." };

/** The fake voice speaks the queued utterances from `from` on, one after another; utterance k
 *  takes chars x perChar(k) seconds. Checks the highlight at every chunk start and end (exact)
 *  and every 50 ms in between (true word: by characters through the chunk). Returns the worst
 *  in-between error in words. */
function speakQueue(env, player, perChar, from = 0, firstWord = 0) {
  let word = firstWord, worst = 0;
  for (let k = from; k < env.spoken.length; k++) {
    const u = env.spoken[k], list = T.words(u.text), last = word + list.length - 1;
    u.onstart();
    assert.equal(player.word, word, `chunk ${k} start on its first word`);
    const ms = u.text.length * perChar(k) * 1000;
    for (let t = 50; t < ms; t += 50) {
      env.run(50);
      const { starts } = T.charStarts(list);
      const truth = word + T.wordAtChar(starts, (t / ms) * u.text.length);
      worst = Math.max(worst, Math.abs(player.word - truth));
    }
    env.run(ms % 50 || 50);
    u.onend();
    if (k < env.spoken.length - 1) assert.equal(player.word, last, `chunk ${k} end on its last word`);
    word = last + 1;
  }
  return worst;
}

test("voice in chunks: queued at once, start/end snap, highlight follows uneven chunks", () => {
  const env = fakeEnv();
  const player = new T.StepPlayer(env);
  player.load(LONG);
  player.play();
  const chunks = player.track.chunks;
  assert.ok(chunks.length >= 4, `chunks: ${chunks.length}`);
  assert.equal(env.spoken.length, chunks.length, "every chunk queued at once");
  assert.equal(env.spoken.map((u) => u.text).join(" "), T.words(T.spokenText(LONG)).join(" "));
  // uneven like real chunks (Firefox 73-83 ms/char, Chromium + espeak-ng 60-67 measured)
  const uneven = (k) => [0.065, 0.077, 0.068, 0.074, 0.063][k % 5];
  const worst = speakQueue(env, player, uneven);
  assert.ok(worst <= 2, `within two words between chunk events: ${worst}`);
  assert.equal(env.ends, 1);
});

test("the learned pace converges and sets the step's length", () => {
  const store = new Map();
  const env = fakeEnv();
  env.storage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) };
  const player = new T.StepPlayer(env);
  player.setVoice({ name: "v", lang: "en-US" }, "en-US");
  for (let round = 0; round < 4; round++) {
    env.spoken.length = 0;
    player.load(LONG);
    player.play();
    speakQueue(env, player, () => 0.06);
    player.pause();
  }
  assert.ok(Math.abs(player.pace - 0.06) < 0.002, `pace ${player.pace}`);
  player.load(LONG);
  assert.ok(Math.abs(player.track.dur - player.track.total * 0.06) < 0.2, "length from the learned pace");
  assert.ok(store.get("coderstanding-tour-pace").includes('"v"'), "kept per voice");
  const worst = (() => { env.spoken.length = 0; player.play(); return speakQueue(env, player, () => 0.06); })();
  assert.ok(worst <= 1, `with the right pace the highlight is on the word: ${worst}`);
});

test("boundary events place the highlight exactly; the clock does not guess", () => {
  const env = fakeEnv();
  const player = new T.StepPlayer(env);
  player.load(LONG);
  player.play();
  const u = env.spoken[1];
  env.spoken[0].onstart();
  env.spoken[0].onend();
  u.onstart();
  const first = T.words(env.spoken[0].text).length;
  u.onboundary({ name: "word", charIndex: u.text.indexOf("makes") });
  const at = player.word;
  assert.equal(T.words(T.spokenText(LONG))[at], "makes");
  env.run(2000);
  assert.equal(player.word, at, "waits for the next word event");
  u.onboundary({ name: "word", charIndex: u.text.indexOf("player") });
  assert.equal(player.word, first + T.words(u.text.slice(0, u.text.indexOf("player"))).length);
});

test("seek: cancel, speak from the chunk holding the word, trimmed to start there", () => {
  const env = fakeEnv();
  const player = new T.StepPlayer(env);
  player.load(LONG);
  player.play();
  const all = T.words(T.spokenText(LONG));
  const target = all.indexOf("announces");
  const { starts, total } = T.charStarts(all);
  const before = env.spoken.length, cancels = env.cancels;
  player.seek((starts[target] / total) * player.track.dur);
  assert.ok(env.cancels > cancels, "cancelled");
  const queued = env.spoken.slice(before);
  assert.ok(queued[0].text.startsWith("announces the start on the bus."), queued[0].text);
  assert.equal(queued.map((u) => u.text).join(" "), all.slice(target).join(" "));
  assert.equal(player.word, target);
  env.spoken[0].onend();  // a cancelled utterance's late event does nothing
  assert.equal(player.word, target);
  const worst = speakQueue(env, player, () => 0.07, before, target);
  assert.ok(worst <= 2);
  assert.equal(env.ends, 1);
});

test("no voices: silent timeline with a note, then speaks once voices load late", () => {
  const voices = [];
  const env = fakeEnv({ voices });
  const player = new T.StepPlayer(env);
  player.load(LONG);
  player.play();
  assert.equal(env.spoken.length, 0);
  env.run(1200);
  assert.equal(env.notes.at(-1), T.NOTES.noVoice);
  assert.ok(player.word > 0, "moves on the clock");
  voices.push({ name: "late", lang: "en-US", localService: true });
  player.voicesChanged();
  assert.ok(env.spoken.length > 0, "starts speaking from the current word");
  assert.equal(T.words(env.spoken[0].text)[0], T.words(T.spokenText(LONG))[player.word]);
});

test("silent timeline runs to the end and finishes", () => {
  const env = fakeEnv({ voices: [] });
  const player = new T.StepPlayer(env);
  player.load(STEP);
  player.play();
  env.run(player.track.dur * 1000 + 100);
  assert.equal(env.ends, 1);
  assert.equal(player.word, T.words(T.spokenText(STEP)).length - 1);
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
  player.load(LONG);
  player.play();
  env.spoken[0].onstart();
  env.run(1500);
  player.pause();
  const word = player.word;
  assert.ok(word > 0);
  env.run(1000);
  assert.equal(player.word, word, "nothing moves while paused");
  player.play();
  assert.equal(T.words(env.spoken.at(-1 - (player.queue.length - 1)).text)[0], T.words(T.spokenText(LONG))[word]);
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
  assert.equal(player.word, T.wordAtChar(player.track.starts, player.track.total / 2), "halfway in time = halfway in characters");
  player.seek(8);
  assert.equal(audio.currentTime, 8);
  audio.onended();
  assert.equal(env.ends, 1);
});
