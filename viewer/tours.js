// Tours player logic (DESIGN 7c Audio): spoken words, word spans for the follow-along highlight,
// speech chunks, the learned speaking pace and the step player (your recording, else the
// browser voice in chunks, else a silent clock). No DOM here: the page hands in the browser
// parts (speech, audio, clock, storage), so `node --test tests/js/test_tours.mjs` drives the
// player with fakes.
(() => {
const DEFAULT_PACE = 0.07;       // seconds per character at 1x until a voice's own pace is learned
const PACE_LIMITS = [0.02, 0.3]; // a chunk timed outside this is not speech (cut short, stuck)
const PACE_WEIGHT = 0.3;         // moving average: weight of the newest chunk
const PACE_KEY = "coderstanding-tour-pace";
const MIN_SENTENCE = 12;         // characters: a shorter sentence joins the next chunk
const MIN_CLAUSE = 40;           // characters before a comma, semicolon or colon may end a chunk
const START_TIMEOUT_MS = 2500;   // no "start" from the voice by then: it is not coming, run on the clock
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
  return target.replace(/^tour:/, "").replace(/>|→/g, " to ").replace(/[#_./:]/g, " ");
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

// ---------- the character timeline and speech chunks ----------

/** Where each spoken word starts in the spoken text (words joined by one space), and its length. */
function charStarts(list) {
  const starts = [];
  let at = 0;
  for (const word of list) { starts.push(at); at += word.length + 1; }
  return { starts, total: Math.max(0, at - 1) };
}

/** The spoken word at character `at` (-1 for an empty text). */
function wordAtChar(starts, at) {
  let lo = 0, hi = starts.length - 1;
  if (hi < 0) return -1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (starts[mid] <= at) lo = mid; else hi = mid - 1;
  }
  return lo;
}

/**
 * Speech chunks over the spoken words: a chunk ends after a sentence end (. ! ?) once it has
 * MIN_SENTENCE characters, or after , ; : once it has MIN_CLAUSE; a short tail joins the chunk
 * before it. [{first, last}] (word indices, inclusive). One utterance per chunk: its start and
 * end events keep the highlight in step with the voice (most engines send no word events).
 */
function chunksOf(list) {
  const chunks = [];
  let first = 0, chars = 0;
  list.forEach((word, i) => {
    chars += word.length + (i > first ? 1 : 0);
    const sentence = /[.!?]["')\]]*$/.test(word), clause = /[,;:]["')\]]*$/.test(word);
    if ((sentence && chars >= MIN_SENTENCE) || (clause && chars >= MIN_CLAUSE)) {
      chunks.push({ first, last: i });
      first = i + 1;
      chars = 0;
    }
  });
  if (first < list.length) {
    const tail = list.slice(first).join(" ").length;
    if (chunks.length && tail < MIN_SENTENCE) chunks.at(-1).last = list.length - 1;
    else chunks.push({ first, last: list.length - 1 });
  }
  return chunks;
}

/** Words before `charIndex` in `text` (a boundary event's charIndex -> word number). */
function wordsBefore(text, charIndex) {
  return words(String(text).slice(0, charIndex)).length;
}

// ---------- learned speaking pace, per voice ----------

/** Seconds per character at rate 1 for each voice, a moving average of the chunks it spoke,
 *  kept in `storage` (localStorage-like; any failure just means nothing is remembered). */
class PaceBook {
  constructor(storage) {
    this.storage = storage;
    this.paces = {};
    try { this.paces = JSON.parse(storage?.getItem(PACE_KEY) || "{}") || {}; } catch { this.paces = {}; }
  }

  get(name) {
    return this.paces[name || ""]?.pace || DEFAULT_PACE;
  }

  /** Add one chunk's measured seconds per character; returns the voice's pace. */
  learn(name, perChar) {
    if (!(perChar >= PACE_LIMITS[0] && perChar <= PACE_LIMITS[1])) return this.get(name);
    const key = name || "";
    const old = this.paces[key];
    const pace = old ? old.pace * (1 - PACE_WEIGHT) + perChar * PACE_WEIGHT : perChar;
    this.paces[key] = { pace, n: (old?.n || 0) + 1 };
    try { this.storage?.setItem(PACE_KEY, JSON.stringify(this.paces)); } catch { /* private window */ }
    return pace;
  }
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

/** On-device voices only (DESIGN 18): an online voice (Chrome's "Google …", Edge's "Microsoft …
 *  Online (Natural)") sends the spoken text to its vendor, so `localService` must be true. */
function localVoices(voices) {
  return voices.filter((v) => v.localService === true);
}

/** The voices worth offering for `lang` (e.g. "en-GB"): on-device only, same language
 *  first-part, plain voices before variants ("+Adam"); all local ones if none match.
 *  Chromium + espeak-ng lists ~15000. */
function voiceChoices(voices, lang) {
  voices = localVoices(voices);
  const prefix = langKey(lang).split("-")[0];
  const same = voices.filter((v) => langKey(v.lang).split("-")[0] === prefix);
  const list = same.length ? same : [...voices];
  const variant = (v) => (v.name.includes("+") ? 1 : 0);
  return list.map((v, i) => [v, i]).sort((a, b) => variant(a[0]) - variant(b[0]) || a[1] - b[1]).map(([v]) => v);
}

/** The voice to use: the chosen one by name, else the best match for `lang` (not the browser's
 *  "default", which Chromium + espeak-ng sets to the first voice: Afrikaans), else null. */
function pickVoice(voices, name, lang) {
  voices = localVoices(voices);
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
 *   storage (localStorage-like, for the learned pace), onChange() after every move,
 *   onEnd() when a step played to its end, onNote(text) for notes.
 * The voice reads the step as chunks (chunksOf), all queued at once: each chunk's start and end
 * put the highlight on its first and last word, and in between it moves by characters at the
 * voice's learned pace. Word boundary events, where an engine sends them, place it exactly.
 * `track.at` is the character position in the spoken text; `pos`/`dur` are seconds for the UI.
 */
class StepPlayer {
  constructor(env) {
    this.env = env;
    this.paces = new PaceBook(env.storage);
    this.track = null;
    this.playing = false;
    this.rate = 1;
    this.voice = null;
    this.lang = "";
    this.token = 0;      // bumps on every stop: stale clocks and timers do nothing
    this.utt = 0;        // bumps on every new queue or stop: stale speech events do nothing
    this.queue = [];     // the queued utterances, kept referenced (Chromium drops events of collected ones)
  }

  get pace() { return this.paces.get(this.voice?.name); }

  /** Load a step (stops whatever plays); the timeline starts at 0. */
  load(step) {
    this.stop();
    const text = spokenText(step);
    const list = words(text);
    const { starts, total } = charStarts(list);
    const rec = step.audio?.rec || null;
    const t = { mode: rec ? "rec" : "words", rec, words: list, starts, total, chunks: chunksOf(list),
      at: 0, pos: 0, dur: rec?.dur || 0, speaking: false, started: false, exact: false, current: null,
      voiceFailed: false, audio: null, audioFailed: false };
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
    if (!t || (!this.playing && t.at <= 0)) return -1;
    return wordAtChar(t.starts, t.at);
  }

  get usesVoice() {
    const t = this.track;
    return Boolean(t && this.playing && t.mode === "words" && !t.voiceFailed && this.voiceReady());
  }

  voiceReady() {
    return Boolean(this.env.synth && localVoices(this.env.synth.getVoices()).length);
  }

  play() {
    const t = this.track;
    if (!t || this.playing) return;
    this.playing = true;
    if (t.at >= t.total - 0.5) { t.at = 0; if (t.mode === "rec") t.pos = 0; }
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
    const fraction = t.dur > 0 ? Math.max(0, Math.min(1, pos / t.dur)) : 0;
    if (t.mode === "rec") {
      t.pos = fraction * t.dur;
      if (t.audio && !t.audioFailed) t.audio.currentTime = t.pos;
    }
    t.at = fraction * t.total;
    if (this.usesVoice) this.speakFrom(t.at);
    this.changed();
  }

  setRate(rate) {
    this.rate = rate;
    const t = this.track;
    if (t?.audio) t.audio.playbackRate = rate;
    if (this.usesVoice && t.speaking) this.speakFrom(t.at);
  }

  setVoice(voice, lang) {
    this.voice = voice;
    this.lang = lang || "";
    if (this.usesVoice && this.track.speaking) this.speakFrom(this.track.at);
    else this.changed();  // another voice, another pace: another length
  }

  /** Voices arrived (Chromium loads them late): a step playing on the clock starts speaking. */
  voicesChanged() {
    const t = this.track;
    if (this.usesVoice && !t.speaking && t.at < t.total - 0.5) { this.note(""); this.speakFrom(t.at); }
  }

  // ---------- inside ----------

  start() {
    const t = this.track;
    const token = ++this.token;
    if (t.mode === "rec" && t.audio && !t.audioFailed) this.playAudio(token);
    else if (t.mode === "words") {
      if (this.usesVoice) this.speakFrom(t.at);
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

  /** Character span of chunk `c`: [start of its first word, end of its last word). */
  chunkSpan(c) {
    const t = this.track;
    return [t.starts[c.first], t.starts[c.last] + t.words[c.last].length];
  }

  /** Queue the chunks from the word at character `at` on (that chunk trimmed to start there). */
  speakFrom(at) {
    const t = this.track, env = this.env;
    const word = Math.max(0, wordAtChar(t.starts, at));
    const from = t.chunks.findIndex((c) => c.last >= word);
    if (from < 0) return;
    const id = ++this.utt;
    const live = () => id === this.utt;
    env.synth.cancel();
    t.at = t.starts[word];
    t.speaking = true;
    t.started = false;
    t.current = null;
    t.lastEventAt = env.now();
    this.queue = t.chunks.slice(from).map((c, i) => {
      const first = i === 0 ? word : c.first;
      const part = { first, last: c.last, startChar: t.starts[first], endChar: this.chunkSpan(c)[1] };
      const text = t.words.slice(first, c.last + 1).join(" ");
      const u = new env.Utterance(text);
      u.rate = this.rate;
      if (this.voice) u.voice = this.voice;
      if (this.lang) u.lang = this.lang;
      u.onstart = () => {
        if (!live()) return;
        Object.assign(t, { speaking: true, started: true, current: part, lastEventAt: env.now() });
        part.startedAt = env.now();
        if (!t.exact || t.at < part.startChar) t.at = part.startChar;  // the chunk's first word, now
        this.changed();
      };
      u.onboundary = (event) => {  // exact word positions where the engine sends them
        if (!live() || (event.name && event.name !== "word")) return;
        t.exact = true;
        t.at = part.startChar + event.charIndex;
        t.lastEventAt = env.now();
        this.changed();
      };
      u.onend = () => {
        if (!live()) return;
        if (part.startedAt != null) {  // learn the pace: this chunk's seconds per character at 1x
          const seconds = ((env.now() - part.startedAt) / 1000) * this.rate;
          this.paces.learn(this.voice?.name, seconds / Math.max(1, part.endChar - part.startChar));
        }
        t.at = Math.min(Math.max(t.at, t.starts[part.last]), part.endChar);  // its last word
        t.lastEventAt = env.now();
        if (i === this.queue.length - 1) { t.speaking = false; this.finish(this.token); return; }
        t.current = null;
        this.changed();
      };
      u.onerror = (event) => {
        if (!live() || event.error === "interrupted" || event.error === "canceled") return;
        t.speaking = false;
        t.voiceFailed = true;
        this.note(event.error === "not-allowed" ? NOTES.blocked : NOTES.failed(event.error));
      };
      return u;
    });
    this.queue.forEach((u) => env.synth.speak(u));
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
        t.at = t.dur > 0 ? (t.pos / t.dur) * t.total : 0;
      } else if (t.mode === "rec") {
        t.pos = Math.min(t.dur, t.pos + ((now - last) / 1000) * this.rate);
        t.at = t.dur > 0 ? (t.pos / t.dur) * t.total : 0;
      } else {
        this.tickVoice(now, last);
      }
      last = now;
      this.changed();
      const clockEnds = t.mode === "rec" ? t.audioFailed || !t.audio : !t.speaking;
      if (clockEnds && t.at >= t.total) {
        this.finish(token);
        return;
      }
      this.frameId = env.frame(tick);
    };
    this.frameId = env.frame(tick);
  }

  /** Between voice events: move by characters at the learned pace, never past the chunk being
   *  spoken (its end event moves on); nothing before the first start; exact engines wait for
   *  their next word event. A voice silent far too long is given up: the clock runs alone. */
  tickVoice(now, last) {
    const t = this.track;
    const step = ((now - last) / 1000) * this.rate / this.pace;
    if (!t.speaking) { t.at = Math.min(t.total, t.at + step); return; }
    const expected = (t.current ? t.current.endChar - t.current.startChar : t.total) * this.pace / this.rate;
    if (now - t.lastEventAt > (expected * 2 + 5) * 1000) { t.speaking = false; return; }
    if (!t.started || !t.current || t.exact) return;
    t.at = Math.min(t.at + step, t.current.endChar - 0.5);
  }

  finish(token) {
    if (token !== this.token || !this.playing) return;
    this.stop();
    this.track.at = this.track.total;
    if (this.track.mode === "rec") this.track.pos = this.track.dur;
    this.changed();
    this.env.onEnd?.();
  }

  /** Stop sound and clock; keeps the position, so Play resumes where it stopped. */
  stop() {
    const env = this.env;
    this.token++;
    this.utt++;
    if (env.synth && this.track?.mode === "words") env.synth.cancel();
    env.cancelFrame?.(this.frameId);
    env.cancelLater?.(this.startTimer);
    env.cancelLater?.(this.noteTimer);
    this.queue = [];
    if (this.track) {
      this.track.speaking = false;
      this.track.current = null;
      this.track.audio?.pause();
    }
  }

  /** Seconds for the UI: the voice's timeline is characters at its learned pace. */
  changed() {
    const t = this.track;
    if (t && t.mode === "words") { t.pos = t.at * this.pace; t.dur = Math.max(t.total * this.pace, 0.1); }
    this.env.onChange?.();
  }

  note(text) { this.env.onNote?.(text); }
}

const api = { DEFAULT_PACE, START_TIMEOUT_MS, MIN_SENTENCE, MIN_CLAUSE, NOTES, linkWords, speakable, words,
  spokenText, spansOf, stepSpans, spanState, charStarts, wordAtChar, chunksOf, wordsBefore, fmtTime, source,
  localVoices, voiceChoices, pickVoice, recorderType, PaceBook, StepPlayer };
if (typeof module !== "undefined") module.exports = api;
else window.CoderTours = api;
})();
