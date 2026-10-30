// Tours player logic (DESIGN 7c Audio): spoken words, word spans for the follow-along highlight,
// the word-count seek bar and the step player (your recording, else the browser voice, else a
// silent clock). No DOM here: the page hands in the browser parts (speech, audio, clock), so
// `node --test tests/js/test_tours.mjs` drives the player with fakes.
(() => {
const PACE = 0.4;               // seconds per spoken word at 1x: the length estimate for the voice
const MIN_DURATION = 4;
const START_TIMEOUT_MS = 2500;  // no "start" from the voice by then: it is not coming, run on the clock
const HOLD = 0.98;              // while the voice speaks, the clock waits here: the voice decides the end
const LINK = /\[\[([^\]]+)\]\]/g;
const RECORD_TYPES = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/webm"];

const NOTES = {
  noVoice: "No speech voice found: this step runs silently on its timeline. On Linux, Firefox needs speech-dispatcher with a voice (e.g. espeak-ng); Chromium also needs to be started with --enable-speech-dispatcher.",
  blocked: "The browser blocked speech until you click the page; running on the timeline.",
  noStart: "The voice did not start; running on the timeline.",
  failed: (error) => `Speech failed (${error}); running on the timeline instead.`,
  noAudio: (error) => `Could not play the recording (${error}); running on the timeline.`,
};

// ---------- spoken words ----------

/** How a link target is read aloud: `cli#run_scan` -> "cli run scan", `A>B` -> "A to B". */
function linkWords(target) {
  return target.replace(/^tour:/, "").replace(/>|→/g, " to ").replace(/[#_./]/g, " ");
}

/** Text as the voice reads it: links become their spoken names. */
function speakable(text) {
  return String(text || "").replace(LINK, (_, target) => linkWords(target));
}

function words(text) {
  return String(text || "").split(/\s+/).filter(Boolean);
}

/** What Play reads for a step: "Title. text". */
function spokenText(step) {
  const title = String(step.title || "").trim();
  const text = speakable(step.text);
  return title ? `${title}. ${text}` : text;
}

/** Word spans of `text` (one per written word) numbered from `start`: {text, start, count};
 *  `count` = how many spoken words it is (a link like [[cli#run_scan]] speaks as three). */
function spansOf(text, start = 0) {
  let at = start;
  return words(text).map((token) => {
    const span = { text: token, start: at, count: words(speakable(token)).length };
    at += span.count;
    return span;
  });
}

/** The title's and the text's word spans of a step, and the spoken word count. */
function stepSpans(step) {
  const title = spansOf(String(step.title || "").trim());
  const last = title.at(-1);
  const body = spansOf(step.text, last ? last.start + last.count : 0);
  return { title, body, total: words(spokenText(step)).length };
}

/** "now" for the span holding spoken word `word`, "said" before it, "" after (or word -1). */
function spanState(span, word) {
  if (word < 0 || word < span.start) return "";
  return word < span.start + span.count ? "now" : "said";
}

// ---------- the word-count timeline ----------

/** Estimated length at 1x of `total` spoken words, in seconds. */
function duration(total) {
  return Math.max(MIN_DURATION, total * PACE);
}

/** The spoken word at `pos` seconds of `dur`. */
function wordAt(pos, dur, total) {
  if (total <= 0 || dur <= 0) return -1;
  return Math.max(0, Math.min(total - 1, Math.floor((pos / dur) * total)));
}

/** Where word `word` starts on the timeline, in seconds. */
function positionOf(word, dur, total) {
  return total > 0 ? (word / total) * dur : 0;
}

/** Words before `charIndex` in `text` (a boundary event's charIndex -> word number). */
function wordsBefore(text, charIndex) {
  return words(String(text).slice(0, charIndex)).length;
}

function fmtTime(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

// ---------- sources and voices ----------

/** What a step plays: your recording (maybe out of date with the text) or the browser voice. */
function source(step) {
  const rec = step.audio?.rec;
  return rec ? { kind: "rec", by: rec.by, stale: Boolean(rec.stale) } : { kind: "voice", stale: false };
}

const langKey = (lang) => String(lang || "").toLowerCase().replace(/_/g, "-");

/** The voices worth offering for `lang` (e.g. "en-GB"): same language first-part, plain voices
 *  before variants ("+Adam"); all of them if none match. Chromium + espeak-ng lists ~15000. */
function voiceChoices(voices, lang) {
  const prefix = langKey(lang).split("-")[0];
  const same = voices.filter((v) => langKey(v.lang).split("-")[0] === prefix);
  const list = same.length ? same : [...voices];
  const variant = (v) => (v.name.includes("+") ? 1 : 0);
  return list.map((v, i) => [v, i]).sort((a, b) => variant(a[0]) - variant(b[0]) || a[1] - b[1]).map(([v]) => v);
}

/** The voice to use: the chosen one by name, else the best match for `lang` (not the browser's
 *  "default", which Chromium + espeak-ng sets to the first voice: Afrikaans), else null. */
function pickVoice(voices, name, lang) {
  if (name) {
    const chosen = voices.find((v) => v.name === name);
    if (chosen) return chosen;
  }
  const choices = voiceChoices(voices, lang);
  return choices.find((v) => langKey(v.lang) === langKey(lang)) || choices[0] || null;
}

/** The recording format: Opus in WebM, else in Ogg, else plain WebM ("" = browser default). */
function recorderType(isTypeSupported) {
  return RECORD_TYPES.find((type) => isTypeSupported(type)) || "";
}

// ---------- the step player ----------

/**
 * Plays one step at a time. `env` gives the browser parts:
 *   synth (speechSynthesis-like or null), Utterance (constructor), makeAudio(url) -> audio element,
 *   now() ms, frame(fn)/cancelFrame(id) (animation frames), later(fn, ms)/cancelLater(id),
 *   onChange() after every move, onEnd() when a step played to its end, onNote(text) for notes.
 * The timeline is `pos` of `dur` seconds; for the voice it is a word count (seek by words).
 */
class StepPlayer {
  constructor(env) {
    this.env = env;
    this.track = null;
    this.playing = false;
    this.rate = 1;
    this.voice = null;
    this.lang = "";
    this.token = 0;      // bumps on every stop: stale clocks and timers do nothing
    this.utt = 0;        // bumps on every new utterance or stop: stale speech events do nothing
    this.utterance = null;  // kept referenced: Chromium drops events of collected utterances
  }

  /** Load a step (stops whatever plays); the timeline starts at 0. */
  load(step) {
    this.stop();
    const text = spokenText(step);
    const { total } = stepSpans(step);
    const rec = step.audio?.rec || null;
    const t = { mode: rec ? "rec" : "words", rec, text, words: words(text), total, pos: 0,
      dur: (rec && rec.dur) || duration(total), speaking: false, voiceFailed: false, audio: null, audioFailed: false };
    if (rec && this.env.makeAudio) {
      t.audio = this.env.makeAudio(rec.url);
      t.audio.onloadedmetadata = () => {  // WebM from MediaRecorder may say Infinity: keep the measured length
        if (Number.isFinite(t.audio.duration) && t.audio.duration > 0) { t.dur = t.audio.duration; this.changed(); }
      };
    }
    this.track = t;
    this.changed();
  }

  /** The spoken word at the slider (-1 before playing starts). */
  get word() {
    const t = this.track;
    if (!t || (!this.playing && t.pos <= 0)) return -1;
    return wordAt(t.pos, t.dur, t.total);
  }

  get usesVoice() {
    const t = this.track;
    return Boolean(t && this.playing && t.mode === "words" && !t.voiceFailed && this.voiceReady());
  }

  voiceReady() {
    return Boolean(this.env.synth && this.env.synth.getVoices().length);
  }

  play() {
    const t = this.track;
    if (!t || this.playing) return;
    this.playing = true;
    if (t.pos >= t.dur - 0.05) t.pos = 0;
    this.note("");
    this.start();
  }

  pause() {
    this.playing = false;
    this.stop();
    this.changed();
  }

  /** Move to `pos` seconds; the voice speaks again from the word there. */
  seek(pos) {
    const t = this.track;
    if (!t) return;
    t.pos = Math.max(0, Math.min(t.dur, pos));
    if (t.audio && !t.audioFailed) t.audio.currentTime = t.pos;
    if (this.usesVoice) this.speakFrom(t.pos);
    this.changed();
  }

  setRate(rate) {
    this.rate = rate;
    const t = this.track;
    if (t?.audio) t.audio.playbackRate = rate;
    if (this.usesVoice && t.speaking) this.speakFrom(t.pos);
  }

  setVoice(voice, lang) {
    this.voice = voice;
    this.lang = lang || "";
    if (this.usesVoice && this.track.speaking) this.speakFrom(this.track.pos);
  }

  /** Voices arrived (Chromium loads them late): a step playing on the clock starts speaking. */
  voicesChanged() {
    const t = this.track;
    if (this.usesVoice && !t.speaking && t.pos < t.dur * HOLD) { this.note(""); this.speakFrom(t.pos); }
  }

  // ---------- inside ----------

  start() {
    const t = this.track;
    const token = ++this.token;
    if (t.mode === "rec" && t.audio && !t.audioFailed) this.playAudio(token);
    else if (t.mode === "words") {
      if (this.usesVoice) this.speakFrom(t.pos);
      else this.noteLater(token);
    }
    this.startClock(token);
    this.changed();
  }

  playAudio(token) {
    const t = this.track, audio = t.audio;
    audio.playbackRate = this.rate;
    audio.currentTime = t.pos;
    audio.onended = () => this.finish(token);
    Promise.resolve(audio.play()).catch((error) => {
      if (token !== this.token) return;
      t.audioFailed = true;  // the clock carries on alone
      this.note(NOTES.noAudio(error?.message || error));
    });
  }

  /** Say "no voice" only if none arrived after a moment (Chromium lists them ~100 ms late). */
  noteLater(token) {
    this.env.cancelLater(this.noteTimer);
    this.noteTimer = this.env.later(() => {
      if (token === this.token && this.playing && !this.usesVoice) this.note(NOTES.noVoice);
    }, 1000);
  }

  /** Speak from the word at `pos` (seek by words): the slider jumps to that word's start. */
  speakFrom(pos) {
    const t = this.track, env = this.env;
    const first = Math.max(0, wordAt(pos, t.dur, t.total));
    t.pos = positionOf(first, t.dur, t.total);
    const text = t.words.slice(first).join(" ");
    const id = ++this.utt;
    const u = new env.Utterance(text);
    u.rate = this.rate;
    if (this.voice) u.voice = this.voice;
    if (this.lang) u.lang = this.lang;
    const live = () => id === this.utt;
    u.onstart = () => { if (live()) { t.speaking = true; t.started = true; } };
    u.onboundary = (event) => {  // each spoken word moves the slider to its word (some engines never send these)
      if (!live() || (event.name && event.name !== "word")) return;
      t.pos = positionOf(first + wordsBefore(text, event.charIndex), t.dur, t.total);
      this.changed();
    };
    u.onend = () => { if (live()) { t.speaking = false; this.finish(this.token); } };
    u.onerror = (event) => {
      if (!live() || event.error === "interrupted" || event.error === "canceled") return;
      t.speaking = false;
      t.voiceFailed = true;
      this.note(event.error === "not-allowed" ? NOTES.blocked : NOTES.failed(event.error));
    };
    env.synth.cancel();
    t.speaking = true;
    t.started = false;
    t.speakAt = env.now();
    this.utterance = u;
    env.synth.speak(u);
    env.cancelLater(this.startTimer);
    this.startTimer = env.later(() => {
      if (live() && !t.started && t.speaking) { t.speaking = false; this.note(NOTES.noStart); }
    }, START_TIMEOUT_MS);
  }

  startClock(token) {
    const t = this.track, env = this.env;
    let last = env.now();
    const tick = () => {
      if (token !== this.token) return;
      const now = env.now();
      if (t.mode === "rec" && t.audio && !t.audioFailed) {
        t.pos = Math.min(t.audio.currentTime || 0, t.dur);
      } else {
        t.pos = Math.min(t.dur, t.pos + ((now - last) / 1000) * this.rate);
        if (t.speaking) {
          const overrun = now - t.speakAt > ((t.dur / this.rate) * 2 + 5) * 1000;  // no end event ever came
          if (overrun) t.speaking = false;
          else t.pos = Math.min(t.pos, t.dur * HOLD);
        }
      }
      last = now;
      this.changed();
      if (!t.speaking && t.pos >= t.dur && !(t.mode === "rec" && t.audio && !t.audioFailed)) {
        this.finish(token);
        return;
      }
      this.frameId = env.frame(tick);
    };
    this.frameId = env.frame(tick);
  }

  finish(token) {
    if (token !== this.token || !this.playing) return;
    this.stop();
    this.track.pos = this.track.dur;
    this.changed();
    this.env.onEnd?.();
  }

  /** Stop sound and clock; keeps `pos`, so Play resumes where it stopped. */
  stop() {
    const env = this.env;
    this.token++;
    this.utt++;
    if (env.synth && this.track?.mode === "words") env.synth.cancel();
    env.cancelFrame?.(this.frameId);
    env.cancelLater?.(this.startTimer);
    env.cancelLater?.(this.noteTimer);
    if (this.track) {
      this.track.speaking = false;
      this.track.audio?.pause();
    }
  }

  changed() { this.env.onChange?.(); }
  note(text) { this.env.onNote?.(text); }
}

const api = { PACE, START_TIMEOUT_MS, HOLD, NOTES, linkWords, speakable, words, spokenText, spansOf,
  stepSpans, spanState, duration, wordAt, positionOf, wordsBefore, fmtTime, source, voiceChoices,
  pickVoice, recorderType, StepPlayer };
if (typeof module !== "undefined") module.exports = api;
else window.CoderTours = api;
})();
