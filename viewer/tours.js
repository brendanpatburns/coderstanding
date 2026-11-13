// Tours player logic (DESIGN 7c Audio): spoken words, word spans for the follow-along highlight,
// speech chunks, the learned speaking pace and the step player (your recording, else a
// voice in chunks (a built-in Piper voice from the local server, or the browser's), else a silent
// clock). No DOM here: the page hands in the browser parts (speech, audio, clock, storage), so
// `node --test tests/js/test_tours.mjs` drives the player with fakes.
(() => {
const DEFAULT_PACE = 0.07;       // seconds per character at 1x until a voice's own pace is learned
const PACE_LIMITS = [0.02, 0.3]; // a chunk timed outside this is not speech (cut short, stuck)
const PACE_WEIGHT = 0.3;         // moving average: weight of the newest chunk
const PACE_KEY = "coderstanding-tour-pace";
const MIN_SENTENCE = 12;         // characters: a shorter sentence joins the next chunk
const MIN_CLAUSE = 40;           // characters before a comma, semicolon or colon may end a chunk
const START_TIMEOUT_MS = 2500;   // no "start" from the voice by then: it is not coming, run on the clock
const BUILT_IN_START_MS = 15000; // the same for a built-in voice: its first clip may wait for the model to load
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

// ---------- line notes: popups on the code lines, in time with the step ----------

/** The spoken word each line note pops up at: its `cue` (words of the step text, first match,
 *  any case), else spread in order over the step's text (the first at the text's start). */
function lineNoteCues(step, notes) {
  const { body, total } = stepSpans(step);
  const textStart = body.length ? body[0].start : total;
  const text = String(step.text || "");
  const plain = (notes || []).filter((n) => !cueAt(text, n.cue));
  return (notes || []).map((n) => {
    const at = cueAt(text, n.cue);
    if (at) return textStart + words(speakable(text.slice(0, at - 1))).length;
    return textStart + Math.floor(((total - textStart) * plain.indexOf(n)) / Math.max(1, plain.length));
  });
}

/** 1 + where `cue` starts in `text` (case-insensitive), 0 when there is no cue or no match. */
function cueAt(text, cue) {
  const wanted = String(cue || "").trim().toLowerCase();
  return wanted ? text.toLowerCase().indexOf(wanted) + 1 : 0;
}

/** Which notes show at spoken word `word`: all of them before playing starts (-1), else the
 *  ones whose cue word was reached. */
function lineNotesShown(cues, word) {
  return cues.map((at) => word < 0 || word >= at);
}

/** The editor's notes box: one note per line, `50-51: text` or `50: text @ cue words`. */
function lineNotesText(notes) {
  return (notes || []).map((n) => `${n.from === n.to ? n.from : `${n.from}-${n.to}`}: ${n.text}${n.cue ? ` @ ${n.cue}` : ""}`).join("\n");
}

/** The editor's notes box read back: {notes: [{from, to, text, cue}], error} (error = the first bad line). */
function parseLineNotes(text) {
  const notes = [];
  for (const line of String(text || "").split("\n").map((l) => l.trim()).filter(Boolean)) {
    const m = line.match(/^(\d+)(?:\s*-\s*(\d+))?\s*:\s*(.+?)(?:\s+@\s+(.+))?$/);
    if (!m || (m[2] && Number(m[2]) < Number(m[1]))) return { notes, error: `Notes: write each as "50-51: text" or "50: text @ cue words", not "${line}"` };
    notes.push({ from: Number(m[1]), to: Number(m[2] || m[1]), text: m[3].trim(), cue: m[4] ? m[4].trim() : null });
  }
  return { notes, error: "" };
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

/** Looks like a network voice: "Online" in the name (Edge's "Microsoft … Online (Natural)"),
 *  Chrome's "Google …", WaveNet. Only these are labelled "online" and never auto-picked, and
 *  only when the browser does not say `localService`. espeak-ng voices through speech-dispatcher
 *  are reported with localService false by Chromium but run on the device: not online. */
const NETWORK_VOICE = /\bonline\b|^google\b|wavenet/i;
const isOnline = (voice) => voice.localService !== true && NETWORK_VOICE.test(`${voice.name} ${voice.voiceURI || ""}`);

/** On-device voices only (DESIGN 18): everything that does not look like a network voice. */
function localVoices(voices) {
  return voices.filter((v) => !isOnline(v));
}

/** What the picker lists: the on-device voices; only if there are none, all of them, so the
 *  picker is not empty. An online voice is never picked for you: you choose it by name. */
function offeredVoices(voices) {
  const local = localVoices(voices);
  return local.length ? local : voices;
}

const SHORT_LIST = 8;
const NATURAL_VOICE = /natural|neural|piper|wavenet|siri|enhanced|premium|^google\b|^[a-z]{2,3}_[A-Z]{2}-[\w]+-(x_low|low|medium|high)$/i;
const ROBOT_VOICE = /klatt|robosoft|whisper|croak/i;
const ESPEAK_BASE = /espeak|^[A-Za-z ]+ \([^)]*\)$/;
const SHORT_TIERS = 3;  // tiers below this make the short list; espeak variants and robots only under "More voices"

/** How good a voice probably sounds, 0 best: 0 built-in (Piper through the local server) and
 *  natural/neural/Piper/Siri/Google, 1 other plain voices, 2 espeak-ng base voices, 3 espeak
 *  variants ("+Adam"), 4 robot variants (klatt, Robosoft, whisper, croak). */
function voiceTier(voice) {
  if (voice.builtIn) return 0;
  const name = voice.name;
  if (ROBOT_VOICE.test(name)) return 4;
  if (name.includes("+")) return 3;
  if (NATURAL_VOICE.test(name)) return 0;
  return ESPEAK_BASE.test(name) ? 2 : 1;
}

/** `voices` for `lang` (e.g. "en-GB"), best first: the page language (else all languages), then
 *  built-in voices before the browser's, then by tier, then the exact language ("en-GB") before
 *  its dialects ("en-GB-X-GBCWMD", "en-029"), then the browser's order. */
function rankedVoices(voices, lang) {
  const want = langKey(lang);
  const prefix = want.split("-")[0];
  const same = voices.filter((v) => langKey(v.lang).split("-")[0] === prefix);
  const exact = (v) => (langKey(v.lang) === want ? 0 : 1);
  const browser = (v) => (v.builtIn ? 0 : 1);
  return (same.length ? same : [...voices]).map((v, i) => [v, i])
    .sort((a, b) => browser(a[0]) - browser(b[0]) || voiceTier(a[0]) - voiceTier(b[0])
      || exact(a[0]) - exact(b[0]) || a[1] - b[1]).map(([v]) => v);
}

/** The voices worth offering for `lang`: on-device ones (see offeredVoices), at most 8 of the
 *  best, or with `all` every one, ranked. If only variants and robots exist, the best 8 of them. */
function voiceChoices(voices, lang, all = false) {
  const ranked = rankedVoices(offeredVoices(voices), lang);
  if (all) return ranked;
  const good = ranked.filter((v) => voiceTier(v) < SHORT_TIERS);
  return (good.length ? good : ranked).slice(0, SHORT_LIST);
}

/** Does the full list hold voices the short one does not ("More voices...")? */
const hasMoreVoices = (voices, lang) => voiceChoices(voices, lang, true).length > voiceChoices(voices, lang).length;

/** True when the best voice for `lang` is only an espeak voice: the picker then hints at better ones. */
function onlyRobotVoices(voices, lang) {
  const best = voiceChoices(localVoices(voices), lang)[0];
  return Boolean(best) && voiceTier(best) >= 2;
}

/** The voice to use: the one chosen by name if still there, else the top-ranked on-device voice
 *  for `lang` (never the browser's "default", which Chromium + espeak-ng sets to the first voice:
 *  Afrikaans), else null (only online voices: none is picked for you). */
function pickVoice(voices, name, lang) {
  const chosen = name && offeredVoices(voices).find((v) => v.name === name);
  return chosen || voiceChoices(localVoices(voices), lang)[0] || null;
}

// ---------- built-in voices (Piper on the local server, DESIGN 7c Audio) ----------

const BUILT_IN_PREFIX = "built-in:";  // keeps a built-in voice's name apart from a browser voice of the same name

/** A voice for the picker and the player from one GET /api/voices entry {name, lang}. */
function builtInVoice(entry) {
  return { name: BUILT_IN_PREFIX + entry.name, label: entry.name, piper: entry.name, lang: entry.lang,
    builtIn: true, localService: true, voiceURI: BUILT_IN_PREFIX + entry.name, default: false };
}

/** The picker's text for a voice. */
function voiceLabel(voice) {
  if (voice.builtIn) return `${voice.label} (${voice.lang}) - built-in`;
  return `${voice.name} (${voice.lang})${isOnline(voice) ? " - online" : ""}`;
}

/** Where built-in voices come from, for the picker's hint (GET /api/voices `listing`), or "". */
function builtInHint(listing) {
  if (!listing) return "";
  if (listing.found && !listing.installed) {
    return `${listing.found} Piper voices found in ${listing.folder}, but Piper is not installed: ${listing.install}.`;
  }
  if (listing.voices?.length) return `Built-in voices run on this PC, from ${listing.folder}. ${listing.licence}`;
  return `For natural built-in voices, add Piper voices to ${listing.folder} (${listing.add}). ${listing.licence}`;
}

const ROBOT_HINT = "Only robot voices found.";
const SYSTEM_VOICE_HINT = "Install a better voice (e.g. Piper for speech-dispatcher on Linux).";

/** The line under the player about voices, or "" for none: shown when only robot voices are
 *  found, or when Piper voice files wait for Piper to be installed. `listing` = GET /api/voices
 *  (null: the server has no built-in voices). */
function voiceHint(voices, lang, listing) {
  const robot = onlyRobotVoices(voices, lang);
  if (!robot && !(listing?.found && !listing.installed)) return "";
  return [robot ? ROBOT_HINT : "", listing ? builtInHint(listing) : SYSTEM_VOICE_HINT].filter(Boolean).join(" ");
}

/** What the player hands the speech engine: the text and how to say it, with utterance-style
 *  handlers. A plain object, so a built-in voice can take it; the browser gets a real utterance. */
class SpeechRequest {
  constructor(text) {
    this.text = text;
    this.rate = 1;
    this.voice = null;
    this.lang = "";
  }
}

const SPEECH_EVENTS = ["onstart", "onend", "onerror", "onboundary"];

/**
 * speechSynthesis-like speech in the built-in voices. `env`: load(voiceName, text) -> Promise of
 * an audio URL (one clip, made once by the server), release(url) when a clip is done,
 * makeAudio(url) -> audio element. Every request's clip is asked for at once (the next ones are
 * ready when the current one ends), then the clips play one after another at the request's rate;
 * each sends start, end and error like an utterance (no word events), so the player's chunk
 * timing works as with a browser voice.
 */
class ClipSynth {
  constructor(env) {
    this.env = env;
    this.voices = [];      // builtInVoice() entries, set by the page
    this.queue = [];
    this.current = null;
  }

  getVoices() { return this.voices; }

  speak(request) {
    this.queue.push({ request, clip: Promise.resolve(this.env.load(request.voice.piper, request.text)) });
    if (!this.current) this.next();
  }

  cancel() {
    const dropped = [this.current, ...this.queue].filter(Boolean);
    this.current = null;
    this.queue = [];
    dropped.forEach((item) => {
      item.canceled = true;
      item.audio?.pause();
      item.clip.then((url) => this.env.release?.(url), () => {});
    });
  }

  next() {
    const item = this.current = this.queue.shift() || null;
    if (!item) return;
    item.clip.then((url) => {
      if (!item.canceled) this.play(item, url);
    }, (error) => this.done(item, { error: error?.message || String(error) }));
  }

  play(item, url) {
    const audio = item.audio = this.env.makeAudio(url);
    item.url = url;
    audio.playbackRate = item.request.rate || 1;
    audio.onplaying = () => {
      if (item.canceled || item.started) return;
      item.started = true;
      item.request.onstart?.({});
    };
    audio.onended = () => this.done(item, null);
    audio.onerror = () => this.done(item, { error: "audio-failed" });
    Promise.resolve(audio.play()).catch((error) =>
      this.done(item, { error: error?.name === "NotAllowedError" ? "not-allowed" : error?.message || String(error) }));
  }

  /** A clip ended (`failure` null) or failed: tell its request, then play the next one. */
  done(item, failure) {
    if (item.canceled || item !== this.current) return;
    if (item.url) this.env.release?.(item.url);
    this.current = null;
    if (failure) item.request.onerror?.(failure);
    else item.request.onend?.({});
    this.next();
  }
}

/** The player's speech engine (speechSynthesis-like): requests in a built-in voice go to `clips`
 *  (a ClipSynth), the rest to the browser's `browser` speechSynthesis as a real `Utterance` made
 *  from the request, its events passed back. Either part may be null. */
class VoiceRouter {
  constructor({ browser, clips, Utterance }) {
    this.browser = browser;
    this.clips = clips;
    this.Utterance = Utterance;
  }

  /** Built-in voices first, then the browser's. */
  getVoices() { return [...(this.clips?.getVoices() || []), ...(this.browser?.getVoices() || [])]; }

  speak(request) {
    if (request.voice?.builtIn) this.clips.speak(request);
    else this.browser?.speak(this.utterance(request));
  }

  cancel() {
    this.clips?.cancel();
    this.browser?.cancel();
  }

  /** The browser utterance for a request; kept on it (Chromium drops events of collected ones). */
  utterance(request) {
    const u = new this.Utterance(request.text);
    u.rate = request.rate;
    if (request.voice) u.voice = request.voice;
    if (request.lang) u.lang = request.lang;
    SPEECH_EVENTS.forEach((name) => { u[name] = (event) => request[name]?.(event); });
    request.native = u;
    return u;
  }
}

/** The recording format: Opus in WebM, else in Ogg, else plain WebM ("" = browser default). */
function recorderType(isTypeSupported) {
  return RECORD_TYPES.find((type) => isTypeSupported(type)) || "";
}

// ---------- the step player ----------

/**
 * Plays one step at a time. `env` gives the browser parts:
 *   synth (speechSynthesis-like or null; the page's is a VoiceRouter over the built-in and the
 *   browser voices), Utterance (constructor; the page's is SpeechRequest), makeAudio(url) -> audio element,
 *   now() ms, frame(fn)/cancelFrame(id) (animation frames), later(fn, ms)/cancelLater(id),
 *   storage (localStorage-like, for the learned pace), onChange() after every move,
 *   onEnd() when a step played to its end, onNote(text) for notes.
 * With `readNotes` on (and the notes given by setNotes), the voice reads each line note when the
 * narration reaches its cue word: narration (or the recording) waits, the clock is held, the
 * note is spoken, then the step goes on from the same word. `readingNote` is its index, else -1.
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
    this.readNotes = false;  // read line notes aloud when they pop up
  }

  /** The step's line notes as [{at, text}]: `at` is the spoken word each pops up at (lineNoteCues). */
  setNotes(list) {
    if (this.track) this.track.notes = list.map((n, i) => ({ i, at: n.at, text: speakable(n.text) }))
      .sort((a, b) => a.at - b.at || a.i - b.i);
  }

  /** Index of the line note being read, -1 when none. */
  get readingNote() { return this.track?.reading?.i ?? -1; }

  get pace() { return this.paces.get(this.voice?.name); }

  /** Load a step (stops whatever plays); the timeline starts at 0. */
  load(step) {
    this.stop();
    this.playing = false;  // a new step starts stopped; the caller plays it (else play() would return early)
    const text = spokenText(step);
    const list = words(text);
    const { starts, total } = charStarts(list);
    const rec = step.audio?.rec || null;
    const t = { mode: rec ? "rec" : "words", rec, words: list, starts, total, chunks: chunksOf(list),
      at: 0, pos: 0, dur: rec?.dur || 0, speaking: false, started: false, exact: false, current: null,
      voiceFailed: false, audio: null, audioFailed: false,
      notes: [], noteDone: new Set(), reading: null };
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
    return Boolean(this.env.synth && (this.voice || localVoices(this.env.synth.getVoices()).length));
  }

  play() {
    const t = this.track;
    if (!t || this.playing) return;
    this.playing = true;
    if (t.at >= t.total - 0.5) { t.at = 0; t.noteDone.clear(); if (t.mode === "rec") t.pos = 0; }
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
    const wasReading = Boolean(t.reading);
    this.seekNotes();
    if (this.usesVoice) this.speakFrom(t.at);
    else if (wasReading && this.playing && t.mode === "rec" && t.audio && !t.audioFailed) this.playAudio(this.token);
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
    if (this.usesVoice && !t.reading && !t.speaking && t.at < t.total - 0.5) { this.note(""); this.speakFrom(t.at); }
  }

  // ---------- inside ----------

  start() {
    const t = this.track;
    const token = ++this.token;
    if (t.reading) this.speakNote(token);  // paused in the middle of a note: its rest, then the step
    else this.narrate(token);
    this.startClock(token);
    this.changed();
  }

  /** Start the step's sound from where it is: the recording, or the voice (else the silent clock). */
  narrate(token) {
    const t = this.track;
    if (t.mode === "rec" && t.audio && !t.audioFailed) this.playAudio(token);
    else if (t.mode === "words") {
      if (this.usesVoice) this.speakFrom(t.at);
      else this.noteLater(token);
    }
  }

  // ---------- reading line notes ----------

  /** Can the voice read a note now? Narration by voice must be running; a recording needs any voice. */
  canReadNotes() {
    const t = this.track;
    if (!this.readNotes || !this.playing || t.voiceFailed || !this.env.synth || !this.voiceReady()) return false;
    return t.mode === "words" ? t.speaking || Boolean(t.reading) : !t.audioFailed;
  }

  /** The next unread note whose cue word was reached, -1 if none. Reached notes the voice cannot
   *  read now (option off, no voice) count as read: they are not read late. */
  dueNote() {
    const t = this.track, word = wordAtChar(t.starts, t.at);
    const due = t.notes.filter((n) => n.at <= word && !t.noteDone.has(n.i));
    if (!due.length) return -1;
    if (this.canReadNotes()) return due[0].i;
    due.forEach((n) => t.noteDone.add(n.i));
    return -1;
  }

  /** After a seek: notes before the new word count as read, later ones can be read again. */
  seekNotes() {
    const t = this.track, word = wordAtChar(t.starts, t.at);
    t.notes.forEach((n) => (n.at <= word && t.at > 0 ? t.noteDone.add(n.i) : t.noteDone.delete(n.i)));
    this.dropNote();
  }

  /** Give up a note being read (seek, load): its speech is cancelled; the caller restarts the step. */
  dropNote() {
    const t = this.track;
    if (!t?.reading) return;
    t.reading = null;
    this.utt++;
    this.env.synth?.cancel();
  }

  /** Pause the narration where it is and read note `i`. */
  beginNote(i, token) {
    const t = this.track;
    this.utt++;  // the narration's speech events are stale now
    t.speaking = false;
    t.current = null;
    if (t.mode === "words") this.env.synth.cancel();
    else t.audio?.pause();
    t.reading = { i, text: t.notes.find((n) => n.i === i).text, spoken: 0 };
    this.speakNote(token);
    this.changed();
  }

  /** Speak the unread rest of the note being read (all of it, or from the last word event). */
  speakNote(token) {
    const t = this.track, env = this.env, r = t.reading;
    const id = ++this.utt;
    const live = () => id === this.utt;
    const offset = r.spoken;
    const u = new env.Utterance(r.text.slice(offset));
    u.rate = this.rate;
    if (this.voice) u.voice = this.voice;
    if (this.lang) u.lang = this.lang;
    u.onboundary = (event) => {
      if (live() && (!event.name || event.name === "word")) r.spoken = offset + event.charIndex;
    };
    u.onend = () => { if (live()) this.noteEnded(token); };
    u.onerror = (event) => {
      if (!live() || event.error === "interrupted" || event.error === "canceled") return;
      t.voiceFailed = true;
      this.note(event.error === "not-allowed" ? NOTES.blocked : NOTES.failed(event.error));
      this.noteEnded(token);
    };
    this.queue = [u];
    if (t.mode !== "words") env.synth.cancel();
    env.synth.speak(u);
  }

  /** A note was read: the next note at this word, else the step goes on from where it waited. */
  noteEnded(token) {
    const t = this.track;
    t.noteDone.add(t.reading.i);
    const next = this.dueNote();  // (still "reading", so a waiting voice counts as running)
    t.reading = null;
    if (next >= 0) { this.beginNote(next, token); return; }
    this.queue = [];
    if (t.mode === "words" ? this.usesVoice : t.audio && !t.audioFailed) this.narrate(token);
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
    }, this.voice?.builtIn ? BUILT_IN_START_MS : START_TIMEOUT_MS);
  }

  startClock(token) {
    const t = this.track, env = this.env;
    let last = env.now();
    const tick = () => {
      if (token !== this.token) return;
      const now = env.now();
      if (t.reading) {
        t.lastEventAt = now;  // a note is being read: the clock holds
      } else if (t.mode === "rec" && t.audio && !t.audioFailed) {
        t.pos = Math.min(t.audio.currentTime || 0, t.dur);
        t.at = t.dur > 0 ? (t.pos / t.dur) * t.total : 0;
      } else if (t.mode === "rec") {
        t.pos = Math.min(t.dur, t.pos + ((now - last) / 1000) * this.rate);
        t.at = t.dur > 0 ? (t.pos / t.dur) * t.total : 0;
      } else {
        this.tickVoice(now, last);
      }
      last = now;
      if (!t.reading) {
        const due = this.dueNote();
        if (due >= 0) this.beginNote(due, token);
      }
      this.changed();
      const clockEnds = t.mode === "rec" ? t.audioFailed || !t.audio : !t.speaking;
      if (!t.reading && clockEnds && t.at >= t.total) {
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
    if (env.synth && (this.track?.mode === "words" || this.track?.reading)) env.synth.cancel();
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

const api = { DEFAULT_PACE, START_TIMEOUT_MS, BUILT_IN_START_MS, MIN_SENTENCE, MIN_CLAUSE, NOTES, linkWords, speakable, words,
  spokenText, spansOf, stepSpans, spanState, charStarts, wordAtChar, chunksOf, wordsBefore, fmtTime, source,
  lineNoteCues, lineNotesShown, lineNotesText, parseLineNotes,
  localVoices, isOnline, offeredVoices, voiceChoices, hasMoreVoices, onlyRobotVoices, voiceTier, pickVoice, recorderType, PaceBook, StepPlayer,
  builtInVoice, voiceLabel, builtInHint, voiceHint, SpeechRequest, ClipSynth, VoiceRouter };
if (typeof module !== "undefined") module.exports = api;
else window.CoderTours = api;
})();
