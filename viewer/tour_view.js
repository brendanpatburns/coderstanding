// Tours tab (DESIGN 7c): tours list, player, step panel, code pane and tour editor, as in the
// approved mock-up (docs/mockups/tours.html). The graph is the graph tab's own canvas, moved here
// while the tab is open, showing the server's view with the step's targets as `focus` (the viewer
// computes no views). Player logic (word spans, seek by words, voice, recording) is tours.js.
"use strict";

const TourView = (() => {
const T = CoderTours;
const NEXT_DELAY_MS = 700;      // pause between steps while playing
const VOICE_KEY = "coderstanding-tour-voice";
const AUTOPLAY_KEY = "coderstanding-tour-autoplay";
const READ_NOTES_KEY = "coderstanding-tour-read-notes";
const VOICE_RETRIES = 20;     // looks for late voices, this many times ...
const VOICE_RETRY_MS = 250;   // ... this far apart
const MAX_RECORD_BYTES = 5 * 1024 * 1024;

const tv = {
  list: [],          // GET /api/tours entries
  tour: null,        // GET /api/tours/<id>
  cur: 0,            // shown step
  editing: false,
  draft: null,       // the editor's copy: {title, steps: [{key, title, text, targets, at, lines, mark, by, ai, audio}]}
  editIdx: 0,
  graph: null,       // the graph tab's level, crumbs and open classes, put back on leaving
  bound: false,
  rec: null,         // recording in progress: {mr, idx, start, timer}
  nextTimer: 0,
  moreVoices: false, // the picker lists every voice, not the best few
  voiceTries: 0,     // fillVoices retries while the browser lists no voices
  voiceTimer: 0,
  noteCues: [],      // the spoken word each line note of the shown step pops up at
  noteTexts: [],     // and their texts
  gliding: false,    // the camera and boxes are moving to a step's frame
  stepToken: 0,
};

const MORE_VOICES = "__more_voices__";
const enc = encodeURIComponent;
const tourPath = (id, rest = "") => `/api/tours/${enc(id)}${rest}`;
const steps = () => tv.tour?.steps || [];
const step = () => steps()[tv.cur] || null;
const editable = () => true;  // every page edits: the local server, or the hosted page through the helper

// ---------- the player ----------

const synth = "speechSynthesis" in window ? window.speechSynthesis : null;
const player = new T.StepPlayer({
  synth,
  Utterance: window.SpeechSynthesisUtterance,
  makeAudio: (url) => Object.assign(new Audio(CoderApps.url(url)), { preload: "auto" }),
  now: () => performance.now(),
  frame: (fn) => requestAnimationFrame(fn),
  cancelFrame: (id) => cancelAnimationFrame(id),
  later: (fn, ms) => setTimeout(fn, ms),
  storage: (() => { try { return window.localStorage; } catch { return null; } })(),  // the learned pace per voice
  cancelLater: (id) => clearTimeout(id),
  onChange: () => updateSeek(),
  onEnd: () => onStepEnd(),
  onNote: (text) => showNote(text),
});

function pageLang() {
  return document.documentElement.lang && document.documentElement.lang.includes("-")
    ? document.documentElement.lang : navigator.language || "en-US";
}

function savedVoice() { try { return localStorage.getItem(VOICE_KEY) || ""; } catch { return ""; } }

/** The voice picker: voices for the page's language (Chromium + espeak-ng lists thousands). */
function fillVoices() {
  const all = synth ? synth.getVoices() : [];
  const lang = pageLang();
  const chosen = T.pickVoice(all, savedVoice(), lang);
  if (chosen && !T.voiceChoices(all, lang).includes(chosen)) tv.moreVoices = true;  // a remembered voice under "More voices..."
  const choices = T.voiceChoices(all, lang, tv.moreVoices);
  const pick = $("tourVoice");
  const needsChoice = choices.length && !chosen;  // only online voices: none is picked for you
  const items = (needsChoice ? ["choose"] : []).concat(choices.length ? choices : [null]);
  if (!tv.moreVoices && T.hasMoreVoices(all, lang)) items.push("more");
  pick.replaceChildren(...items.map((v) => {
    const option = document.createElement("option");
    option.value = v && v !== "choose" && v !== "more" ? v.name : v === "more" ? MORE_VOICES : "";
    option.textContent = v === "choose" ? "Choose a voice (online)" : v === "more" ? "More voices..."
      : v ? `${v.name} (${v.lang})${T.isOnline(v) ? " - online" : ""}` : "No voices found";
    return option;
  }));
  $("voiceHint").hidden = !T.onlyRobotVoices(all, lang);
  pick.disabled = !choices.length;
  pick.title = choices.length ? `${choices.length} voices for ${pageLang()}` : T.NOTES.noVoice;
  if (chosen) pick.value = chosen.name;
  player.setVoice(chosen, chosen?.lang || pageLang());
  player.voicesChanged();
  if (!all.length) retryVoices();
}

/** Some browsers list voices late and not always with `voiceschanged`: look again a few times. */
function retryVoices() {
  if (tv.voiceTries++ >= VOICE_RETRIES) return;
  clearTimeout(tv.voiceTimer);
  tv.voiceTimer = setTimeout(fillVoices, VOICE_RETRY_MS);
}

function onVoice() {
  if ($("tourVoice").value === MORE_VOICES) {
    tv.moreVoices = true;
    fillVoices();
    return $("tourVoice").focus();
  }
  try { localStorage.setItem(VOICE_KEY, $("tourVoice").value); } catch { /* private window */ }
  fillVoices();
}

function showNote(text) {
  $("ttsNote").textContent = text;
  $("ttsNote").hidden = !text;
}

function updatePlayButton() {
  $("tourPlay").innerHTML = player.playing ? "&#10073;&#10073; Pause" : "&#9654; Play";
  $("tourPlay").setAttribute("aria-pressed", String(player.playing));
  $("stepText")?.classList.toggle("speaking", player.playing);
}

function updateSeek() {
  const t = player.track;
  const bar = $("seekBar");
  bar.disabled = !t;
  bar.value = t && t.dur > 0 ? Math.round((t.pos / t.dur) * 1000) : 0;
  $("seekNow").textContent = T.fmtTime(t ? t.pos : 0);
  $("seekDur").textContent = t ? (t.mode === "words" ? "~" : "") + T.fmtTime(t.dur) : "0:00";
  bar.title = t?.mode === "words" ? "Seek by words: the voice restarts at the word under the slider" : "Seek in this step";
  markWords();
}

/** The follow-along highlight: the span at the spoken word glows, earlier ones dim. */
function markWords() {
  const word = player.word;
  syncLineNotes(word);
  const holder = $("tourPanel");
  if (holder.dataset.word === String(word)) return;
  holder.dataset.word = String(word);
  for (const span of holder.querySelectorAll(".w")) {
    const mark = T.spanState({ start: Number(span.dataset.s), count: Number(span.dataset.n) }, word);
    span.classList.toggle("now", mark === "now");
    span.classList.toggle("said", mark === "said");
  }
}

function play() {
  if (!step()) return;
  clearTimeout(tv.nextTimer);
  player.play();
  updatePlayButton();
}

function pause() {
  clearTimeout(tv.nextTimer);
  player.pause();
  updatePlayButton();
}

function savedAutoplay() { try { return localStorage.getItem(AUTOPLAY_KEY) !== "0"; } catch { return true; } }

function savedReadNotes() { try { return localStorage.getItem(READ_NOTES_KEY) === "1"; } catch { return false; } }

function onReadNotes() {
  player.readNotes = $("tourReadNotes").checked;
  try { localStorage.setItem(READ_NOTES_KEY, player.readNotes ? "1" : "0"); } catch { /* private window */ }
}

function onAutoplay() {
  try { localStorage.setItem(AUTOPLAY_KEY, $("tourAutoplay").checked ? "1" : "0"); } catch { /* private window */ }
}

/** A step played to its end: with Auto-play on the next one starts; otherwise (and after the last) it stops. */
function onStepEnd() {
  if ($("tourAutoplay").checked && tv.cur < steps().length - 1) {
    tv.nextTimer = setTimeout(() => goTo(tv.cur + 1, true), NEXT_DELAY_MS);
  } else {
    pause();
  }
}

function onSeek() {
  const t = player.track;
  if (t) player.seek((Number($("seekBar").value) / 1000) * t.dur);
}

// ---------- graph: the server's view with the step's focus ----------

/** Show a step in the graph: the server's view at the step's level, focused on its targets (as
 *  view ids) with their functions' classes open, laid out compactly around the targets
 *  (layOutStep); the camera and the boxes glide there from the last step. The first focused
 *  function is selected; every focused function's lines are lit (fanout.js `hotFns`). */
async function showStepGraph(target) {
  const mine = ++tv.stepToken;
  const before = cameraNow();
  state.focus = target.focus?.length ? target.focus : null;
  state.tourStep = true;  // open boxes list the step's functions (fanout.js memberGroups)
  state.crumbs = [];
  state.expanded = new Set(target.expand || []);
  state.level = target.level || state.meta.max_level;
  const [tourView, stepView] = viewLayers();
  await useTourOptions(tourView, stepView);  // the tour's and step's view options (URL options still win)
  await loadView();
  if (mine !== tv.stepToken) return;
  if (state.current?.focus) layOutStep(before, mine);
  else fitFocus();
  const fn = (state.current?.focus?.functions || []).map((id) => fnById(id)).find(Boolean);
  if (fn) selectFn(fn);
}

const FRAME = { pad: 40, min: 0.4, max: 1.2, ms: 520 };  // READABLE_ZOOM (app.js): names still read

/** Where the camera and every box are now (null before anything is drawn). */
function cameraNow() {
  if (!cy || cy.nodes().empty()) return null;
  return { zoom: cy.zoom(), pan: { ...cy.pan() }, spots: new Map(cy.nodes().map((n) => [n.id(), { ...n.position() }])) };
}

/** A step's own compact layout (CoderLayout.focusLayout) and camera (focusFrame); nothing is
 *  saved, and leaving the tab puts every class back on its own spot. */
function layOutStep(before, token) {
  cy.resize();  // Cytoscape keeps the size it last measured; the tab's panes may have changed it since
  const focus = new Set(state.current.focus.nodes);
  const nodes = cy.nodes().map((n) => ({ id: n.id(), w: n.data("w"), h: n.data("h"), faded: Boolean(n.data("raw").faded) }));
  const edges = cy.edges().map((e) => ({ from: e.data("source"), to: e.data("target") }));
  const spots = CoderLayout.focusLayout(nodes, edges, { focus, height: cy.height() / READABLE_ZOOM });
  const rects = nodes.map((n) => ({ ...spots.get(n.id), w: n.w, h: n.h, focus: focus.has(n.id), core: focus.has(n.id) || !n.faded }));
  const camera = CoderLayout.focusFrame(rects.filter((r) => r.x !== undefined), { width: cy.width(), height: cy.height() },
    { pad: FRAME.pad, min: FRAME.min, max: FRAME.max, readAll: READABLE_ZOOM });
  glide(before, spots, camera, token);
}

/** Move the boxes from where the last step had them (new ones fade in) and the camera to its
 *  new frame, in one eased tween (a jump with reduced motion or nothing shown before); the line
 *  bends are worked out when it lands. A newer step stops it. `reducedMotion`, `ease` and `lerp`
 *  are domain_view.js's (the domain frames' tween). */
function glide(before, spots, camera, token) {
  const ms = before && !reducedMotion() ? FRAME.ms : 0;
  const moves = cy.nodes().map((n) => ({ n, to: spots.get(n.id()) || n.position(), from: before?.spots.get(n.id()) }));
  const startCam = before || camera;
  const place = (t) => cy.batch(() => {
    for (const m of moves) {
      m.n.position(m.from ? { x: lerp(m.from.x, m.to.x, t), y: lerp(m.from.y, m.to.y, t) } : m.to);
      if (!m.from) { if (t < 1) m.n.style("opacity", t); else m.n.removeStyle("opacity"); }
    }
    cy.viewport({ zoom: lerp(startCam.zoom, camera.zoom, t),
      pan: { x: lerp(startCam.pan.x, camera.pan.x, t), y: lerp(startCam.pan.y, camera.pan.y, t) } });
  });
  const start = performance.now();
  const land = () => { tv.gliding = false; routeEdges(); };
  const frame = (now) => {
    if (token !== tv.stepToken) { place(1); return; }
    const p = Math.min(1, (now - start) / ms);
    place(ease(p));
    if (p < 1) requestAnimationFrame(frame);
    else land();
  };
  if (!ms) { place(1); land(); return; }
  tv.gliding = true;
  place(0);
  requestAnimationFrame(frame);
}

/** Links and target chips select their target in the graph when it is on screen. */
function selectTarget(target) {
  if (!selectInGraph(target)) flash(`${target} is not in this step's view`);
}

// ---------- navigation ----------

async function goTo(i, keepPlaying = player.playing) {
  if (!steps().length) return;
  clearTimeout(tv.nextTimer);
  tv.cur = Math.max(0, Math.min(steps().length - 1, i));
  if (tv.editing) tv.editIdx = tv.cur;
  setUrlStep();
  render();
  player.load(step());  // stops anything playing
  giveNotesToPlayer();
  updatePlayButton();
  StartHere.show(tv.tour, tv.cur);
  const shown = showStepGraph(step());
  if (keepPlaying) play();  // Next/Back while playing: play the new step from its start
  await shown;
}

function setUrlStep() {
  const params = new URLSearchParams(location.search);
  params.set("tab", "tours");
  if (tv.tour) params.set("tour", tv.tour.id); else params.delete("tour");
  params.set("step", String(tv.cur + 1));
  history.replaceState(null, "", `${location.pathname}?${params}${location.hash}`);
}

async function openTour(id, stepNo = 0, { edit = false } = {}) {
  pause();
  try {
    tv.tour = await api(tourPath(id));
  } catch (error) {
    flash(`Could not load the tour: ${error.message}`);
    return;
  }
  tv.editing = false;
  if (edit && editable()) startEditing();
  await goTo(stepNo, false);
}

async function loadList() {
  const query = $("tourSearch").value.trim();
  try {
    tv.list = (await api(`/api/tours${query ? `?q=${enc(query)}` : ""}`)).tours;
  } catch (error) {
    tv.list = [];
    $("tourList").innerHTML = `<li class="muted">Could not load tours: ${esc(error.message)}</li>`;
    return;
  }
  renderList();
}

// ---------- rendering ----------

function authorChip(item) {
  return item.ai ? `<span class="chip" title="by ${esc(item.by)}">AI draft</span>`
    : `<span class="chip human" title="by ${esc(item.by)}">${esc(item.by)}</span>`;
}

function sourceChip(s) {
  const src = T.source(s);
  if (src.kind === "rec") {
    return `<span class="chip human" title="Recorded by ${esc(src.by)}">Your recording · ${esc(src.by)}</span>${src.stale
      ? '<span class="chip stale" title="The text changed after this was recorded; it still plays">Changed text: recording out of date</span>' : ""}`;
  }
  return '<span class="chip gen" title="The browser voice reads the step text; the seek bar moves and seeks word by word">Read aloud · seek by words</span>';
}

/** Word spans: data-s / data-n = the span's first spoken word and how many words it speaks as. */
function spansHtml(spans) {
  return spans.map((span) => `<span class="w" data-s="${span.start}" data-n="${span.count}">${wikiLinksHtml(span.text)}</span>`).join(" ");
}

/** The list as the server found it (`?q=` searches titles, step text and targets). */
function renderList() {
  const rows = tv.list.map((t) => `<li><button class="pick" type="button" data-tour="${esc(t.id)}" ${t.id === tv.tour?.id ? 'aria-current="true"' : ""}>
      <span class="t">${esc(t.title)}</span>
      <span class="m">${t.steps} steps · ${t.start ? '<span class="chip" title="A Start-here path">Start</span>' : ""}${authorChip(t)}${t.ai_steps && !t.ai ? `<span class="chip">${t.ai_steps} AI</span>` : ""}${t.changed ? `<span class="chip stale">${t.changed} changed</span>` : ""}</span></button>
      <a class="mdbtn" href="${esc(CoderApps.url(tourPath(t.id, ".md")))}" download="${esc(t.id)}.md" title="Save as Markdown (${esc(t.id)}.md)">.md</a></li>`);
  $("tourList").innerHTML = rows.join("") || `<li class="muted">${$("tourSearch").value.trim() ? "No tour matches." : "No tours yet."}</li>`;
}

function renderDots() {
  $("tourDots").innerHTML = steps().map((s, i) => `<button type="button" data-go="${i}" class="${i < tv.cur ? "done" : ""} ${s.stale ? "stale" : ""}" ${i === tv.cur ? 'aria-current="step"' : ""} title="Step ${i + 1}: ${esc(s.title)}${s.stale ? " (changed since written)" : ""}">${i + 1}</button>`).join("");
}

function renderPanel() {
  const panel = $("tourPanel");
  panel.dataset.word = "";
  if (!tv.tour) {
    panel.innerHTML = `<p class="muted">Pick a tour on the left${editable() ? ", or start one with + New" : ""}.</p>`;
    return;
  }
  const all = steps(), ai = all.filter((s) => s.ai).length, changed = all.filter((s) => s.stale).length;
  const head = `<div class="tourhead">
      <h2>${esc(tv.editing ? tv.draft.title : tv.tour.title)}</h2>
      <div class="row">${authorChip(tv.tour)}<span class="muted small">${all.length} steps · ${ai} AI-draft steps${changed ? ` · ${changed} changed` : ""}</span>
      <span class="head-actions">${tv.tour.isNew ? "" : `<a class="btn small" data-act="md" href="${esc(CoderApps.url(tourPath(tv.tour.id, ".md")))}" download="${esc(tv.tour.id)}.md" title="Download ${esc(tv.tour.id)}.md (same export as coderstanding tour --md)">Save as Markdown</a>`}
      ${editable() ? `<button class="btn small ${tv.editing ? "on" : ""}" data-act="edit" type="button">${tv.editing ? "Done" : "Edit tour"}</button>` : ""}</span></div>
    </div>`;
  panel.innerHTML = head + (tv.editing ? editorHtml() : stepHtml());
  updatePlayButton();
  markWords();
}

/** Refs here (CoderRefs: the one writer): the shown step, its line notes, the selected code lines. */
const tourRef = (target, hint = "") => CoderRefs.formatRef(CoderApps.current, target, hint);

function stepCopyButtons(s) {
  return CoderRefs.copyButtons(tourRef(CoderRefs.target.tour(tv.tour.id, tv.cur + 1), s.title), s.text);
}

function stepHtml() {
  const s = step();
  if (!s) return '<p class="muted">This tour has no steps yet.</p>';
  const { title, body } = T.stepSpans(s);
  const accept = s.ai && editable() ? '<button class="btn small" type="button" data-act="accept">Accept</button>' : "";
  return `<div class="stephead">
      <div class="row"><span class="stepno">Step ${tv.cur + 1}/${steps().length}</span>${authorChip(s)}${accept}${stepCopyButtons(s)}${s.stale ? '<span class="chip stale" title="The code under this step changed since it was written">changed since written</span>' : ""}</div>
      <h3 class="steptitle">${spansHtml(title)}</h3>
    </div>
    <div class="row"><span class="stepno">Voice</span>${sourceChip(s)}</div>
    <div class="steptext ${player.playing ? "speaking" : ""}" id="stepText">${spansHtml(body)}</div>
    <dl><dt>Shows</dt><dd class="chips-row">${s.targets.map((t) => `<span class="wl" data-target="${esc(t)}" tabindex="0" role="link">${esc(t)}</span>`).join("")}</dd>
      ${s.lost_targets?.length ? `<dt>Lost</dt><dd class="chips-row">${s.lost_targets.map((t) => `<span class="chip stale" title="Not in the code any more">${esc(t)}</span>`).join("")}</dd>` : ""}
      ${s.code ? `<dt>Code</dt><dd><code>${esc(s.code.at)}</code> ${s.code.from ? `<span class="muted small">lines ${s.code.from}-${s.code.to} now</span>` : ""}</dd>` : ""}
      ${viewSummary(s)}</dl>
    <section class="comments" id="tourComments" aria-label="Comments"></section>
    <div class="sec">All steps</div>
    <ol class="steplist">${steps().map((x, i) => `<li><button class="go" type="button" data-go="${i}" ${i === tv.cur ? 'aria-current="step"' : ""}><span class="n">${i + 1}</span><span class="nm">${esc(x.title)}</span>${x.ai ? '<span class="chip">AI</span>' : ""}${x.stale ? '<span class="chip stale">!</span>' : ""}</button></li>`).join("")}</ol>
    <details class="cli" id="aiText"><summary>Step as AI text</summary><pre class="md" id="aiTextBody">Loading…</pre><button class="btn small" type="button" data-act="copy-ai">Copy for AI</button> ${CoderRefs.copyButtons(tourRef(CoderRefs.target.tour(tv.tour.id, tv.cur + 1), s.title))} <span class="toast" id="aiToast"></span></details>
    <p class="muted small">Keys: <span class="kbd">←</span> <span class="kbd">→</span> step · <span class="kbd">Space</span> play/pause · <span class="kbd">Home</span> first step. Links select their target in the graph.</p>
    <span class="toast" id="tourToast" role="status"></span>`;
}

// ---------- code pane ----------

const KEYWORDS = /\b(def|class|return|for|in|if|not|and|or|is|None|True|False|null|true|false|while|with|as|try|except|catch|import|from|else|elif|lambda|func|var|const|let|function|new|public|private|protected|static|void|extends|signal|await|async|export|namespace|using|struct|enum|self|this)\b/;

/** Light keyword / string / comment colouring; `#` comments for Python and GDScript, `//` elsewhere. */
function highlightLine(line, hashComments) {
  const comment = hashComments ? "#.*$" : "\\/\\/.*$";
  const re = new RegExp(`(${comment})|("[^"]*"|'[^']*')|${KEYWORDS.source}`, "g");
  let out = "", last = 0, m;
  while ((m = re.exec(line))) {
    out += esc(line.slice(last, m.index));
    out += m[1] ? `<span class="c">${esc(m[1])}</span>` : m[2] ? `<span class="s">${esc(m[2])}</span>` : `<span class="k">${m[3]}</span>`;
    last = re.lastIndex;
  }
  return out + esc(line.slice(last));
}

function repoPath() {
  return CoderApps.list.find((app) => app.name === CoderApps.current)?.path || "";
}

// ---------- ?ref= link: the lines it names stay lit on that step ----------

/** `?hl=A-B` (a ref's lines) as [A, B], or null. */
function hlFromUrl(params) {
  const match = /^(\d+)-(\d+)$/.exec(params.get("hl") || "");
  return match ? [Number(match[1]), Number(match[2])] : null;
}

const isRefHit = (no) => tv.refHit?.step === tv.cur && no >= tv.refHit.from && no <= tv.refHit.to;

// ---------- selected code lines: Ctrl+C = the text (browser default), Ctrl+Shift+C = the location; the right-click menu and the head's buttons offer both ----------

/** True when `range` ends right where `ln` starts (a triple-click runs on to the next line's start). */
function endsAtLineStart(range, ln) {
  const no = ln.querySelector(".no");
  return range.endOffset === 0 && [ln, no, no.firstChild].includes(range.endContainer);
}

/** [first, last] file line numbers the selection in the code pane touches, or null. */
function selectedLines() {
  const sel = getSelection();
  if (!sel || sel.isCollapsed || !sel.rangeCount || !$("codeSrc").contains(sel.anchorNode)) return null;
  const range = sel.getRangeAt(0);
  const hit = [...$("codeSrc").querySelectorAll(".ln")].filter((ln) => range.intersectsNode(ln) && !endsAtLineStart(range, ln));
  if (!hit.length) return null;
  const numbers = hit.map((ln) => Number(ln.querySelector(".no").textContent));
  return CoderRefs.lineRange(numbers[0], numbers[numbers.length - 1]);
}

/** The code ref for the selection, with the step's code anchor as the enclosing-unit hint. */
function selectionRef() {
  const c = step()?.code, lines = selectedLines();
  if (!c?.path || !lines) return null;
  return tourRef(CoderRefs.target.lines(c.path, lines[0], lines[1]), CoderRefs.codeHint(c.at, c.first, lines[0], lines[1]));
}

/** The selected lines' own text, without line numbers or note pop-ups. */
function selectedCode() {
  const c = step().code, lines = selectedLines();
  return lines ? c.lines.slice(lines[0] - c.from, lines[1] - c.from + 1).join("\n") : "";
}

/** Ctrl+Shift+C on a selection in the code pane: copy its location (Ctrl+C stays the normal copy). */
function onCodeKey(event) {
  if (!(event.ctrlKey || event.metaKey) || !event.shiftKey || event.key.toLowerCase() !== "c") return;
  const ref = selectionRef();
  if (!ref) return;
  event.preventDefault();
  copyRefs([ref], (message) => flash(message));
}

function renderCode() {
  const s = step(), c = s?.code;
  $("tourCode").hidden = !c;
  tv.noteTexts = [];
  if (!c) return;
  const markFirst = c.mark?.[0] || c.from;
  $("openEditor").hidden = !c.path;
  $("codeCopy").innerHTML = c.path ? CoderRefs.copyButtons(tourRef(CoderRefs.target.lines(c.path, c.from, c.to), CoderRefs.codeHint(c.at, c.first, c.from, c.to)), c.lines.join("\n")) : "";
  $("codePath").textContent = c.path ? `${c.path}:${c.from}-${c.to}` : c.at;
  $("codeAnchor").textContent = `anchored on ${c.at}`;
  $("openEditor").href = CoderApps.editorUrl(repoPath(), c.path, markFirst);
  $("openEditor").title = `Open ${c.path} at line ${markFirst} in VS Code`;
  const hash = /\.(py|gd|sh|rb|toml|ya?ml)$/.test(c.path);
  const notes = shownLineNotes(s);
  $("codeSrc").innerHTML = (c.lines || []).map((line, i) => {
    const no = c.from + i;
    return `<span class="ln${(c.mark || []).includes(no) ? " hl" : ""}${isRefHit(no) ? " ref-hit" : ""}"><span class="no">${no}</span>${highlightLine(line, hash)}</span>`
      + notes.filter((n) => n.at === no).map(lineNoteHtml).join("");
  }).join("");
  tv.noteCues = T.lineNoteCues(s, notes);
  tv.noteTexts = notes.map((n) => n.text);
  giveNotesToPlayer();
  syncLineNotes(player.word);
  renderStaleBar(s);
}

// ---------- line notes: popups under their lines, in time with the step (tours.js times them) ----------

/** The shown step's notes and cues go to the player, which reads them aloud when asked ("Read notes aloud"). */
function giveNotesToPlayer() {
  player.setNotes((tv.noteTexts || []).map((text, i) => ({ at: tv.noteCues[i], text })));
}

/** The step's notes that fall on its code lines, each placed after the last of its lines (`at`). */
function shownLineNotes(s) {
  const c = s.code;
  return (s.notes || []).filter((n) => n.from != null && n.from <= c.to && n.to >= c.from)
    .map((n, i) => ({ ...n, i, at: Math.min(n.to, c.to), first: Math.max(n.from, c.from) }));
}

function lineNoteHtml(n) {
  const lines = n.first === n.at ? `line ${n.at}` : `lines ${n.first}-${n.at}`;
  return `<div class="lnote" data-note="${n.i}" data-from="${n.first}" data-to="${n.at}" role="note" aria-label="Note on ${lines}"><span class="lnote-text">${wikiLinksHtml(n.text)}</span>${CoderRefs.copyButtons(tourRef(CoderRefs.target.tour(tv.tour.id, tv.cur + 1, n.first, n.at)), n.text)}</div>`;
}

/** Show the notes the narration reached (all of them before playing) and mark their lines;
 *  a note that pops up while playing scrolls into view. */
function syncLineNotes(word) {
  const src = $("codeSrc");
  const cues = tv.noteCues || [];
  const shown = T.lineNotesShown(cues, word);
  const lit = new Set();
  src.querySelectorAll(".lnote").forEach((el) => {
    const on = Boolean(shown[Number(el.dataset.note)]);
    if (on && !el.classList.contains("on") && player.playing) el.scrollIntoView({ block: "nearest", behavior: "smooth" });
    el.classList.toggle("on", on);
    el.classList.toggle("reading", Number(el.dataset.note) === player.readingNote);
    if (on) for (let no = Number(el.dataset.from); no <= Number(el.dataset.to); no++) lit.add(no);
  });
  src.querySelectorAll(".ln").forEach((ln) => ln.classList.toggle("noted", lit.has(Number(ln.querySelector(".no")?.textContent))));
}

function renderStaleBar(s) {
  const bar = $("staleBar"), c = s.code;
  bar.hidden = !s.stale && !s.lost;
  if (s.lost) {
    bar.innerHTML = `<span class="chip stale">anchor gone</span><span><code>${esc(c.at)}</code> is no longer in the code; the step is listed with the lost notes. Edit the tour to anchor it again.</span>`;
    return;
  }
  if (!s.stale) return;
  const moved = c.was ? `the excerpt moved from lines ${esc(c.was)} to ${c.from}-${c.to} and its text changed` : "its text changed";
  const still = editable() ? '<button class="btn small" type="button" data-act="still-true" title="The step still explains this code: store today\'s fingerprint">Still true</button>' : "";
  bar.innerHTML = `<span class="chip stale">code changed since written</span><span>The anchor <code>${esc(c.at)}</code> still exists; ${moved}. Lines shown are today's.</span>${still}`;
}

function render() {
  const has = steps().length > 0;
  $("tourBack").disabled = !has || tv.cur === 0;
  $("tourNext").disabled = !has || tv.cur >= steps().length - 1;
  $("tourPlay").disabled = !has;
  $("seekBar").disabled = !has;
  $("tabTours").classList.toggle("editing", tv.editing);
  renderDots();
  renderPanel();
  renderList();
  if (has) renderCode(); else $("tourCode").hidden = true;
  $("seekChip").innerHTML = has ? sourceChip(step()) : "";
  CoderComments.update(tv);  // comments.js: the step's threads, line "+", open-count badges
}

let toastTimer = 0;
function flash(text, id = "tourToast") {
  const el = $(id) || $("ttsNote");
  if (el === $("ttsNote")) { showNote(text); return; }
  el.textContent = text;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.textContent = ""; }, 5000);
}

// ---------- AI text: `coderstanding tour <app> <id> --step N`, from the server ----------

function aiCommand() {
  return `coderstanding tour ${CoderApps.current} ${tv.tour.id} --step ${tv.cur + 1}`;
}

/** Fill "Step as AI text" from GET /api/tours/<id>.txt?step=N (the CLI's text) when opened. */
async function loadAiText() {
  const body = $("aiTextBody");
  if (!body || body.dataset.loaded) return;
  try {
    const response = await fetch(CoderApps.url(tourPath(tv.tour.id, `.txt?step=${tv.cur + 1}`)));
    const text = await response.text();
    if (!response.ok) throw new Error(text);
    body.textContent = text;
    body.dataset.loaded = "1";
  } catch (error) {
    body.textContent = `Could not get the step text (${error.message}). On the command line: ${aiCommand()}`;
  }
}

async function copyAiText() {
  try {
    await navigator.clipboard.writeText($("aiTextBody").textContent);
    flash("Copied", "aiToast");
  } catch { flash("Copy blocked: select the text and copy", "aiToast"); }
}

// ---------- writes: accept, still true, save, audio ----------

const sendJson = (path, method, body) => api(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

/** Show the tour a write answered with (every tour write returns it, as GET does). */
async function reloadTour(keepEditing = false, tour = null) {
  const at = tv.cur;
  tv.tour = tour || await api(tourPath(tv.tour.id));
  if (keepEditing) startEditing(); else tv.editing = false;
  await loadList();
  await goTo(Math.min(at, steps().length - 1), false);
}

async function write(action, keepEditing = false) {
  try {
    await reloadTour(keepEditing, await action());
  } catch (error) {
    flash(`Not saved: ${error.message}`);
  }
}

const acceptStep = (key) => write(() => sendJson(tourPath(tv.tour.id, "/accept"), "POST", { keys: [key] }), tv.editing);
const stillTrue = (key) => write(() => sendJson(tourPath(tv.tour.id, "/still-true"), "POST", { keys: [key] }));

// ---------- editor ----------

/** The editor's code fields: `lines` as offsets from the anchor's first line (`code.first`),
 *  `mark` as today's line numbers. A lost anchor has no `first`: `lines` stays empty and the step
 *  cannot be saved until it is filled in (saving a guess would move the excerpt). */
function codeFields(c) {
  if (!c) return { at: "", lines: "", mark: "" };
  const known = c.first != null && c.from != null;
  return { at: c.at, lines: known ? `+${c.from - c.first}..+${c.to - c.first}` : "", mark: (c.mark || []).join(", ") };
}

function draftStep(s) {
  return { key: s.key, title: s.title, text: s.text, targets: [...s.targets], ...codeFields(s.code), view: { ...(s.view || {}) },
    notes: T.lineNotesText((s.notes || []).filter((n) => n.from != null)), by: s.by, ai: s.ai, audio: s.audio };
}

/** Steps whose code anchor has no line offsets (see codeFields): 1-based numbers. */
function missingLines() {
  return tv.draft.steps.map((s, i) => (s.at && !s.lines ? i + 1 : 0)).filter(Boolean);
}

function startEditing() {
  tv.editing = true;
  tv.editIdx = tv.cur;
  tv.draft = { title: tv.tour.title, view: { ...(tv.tour.view || {}) }, steps: steps().map(draftStep) };
  tv.viewScope = "tour";
}

function draftBody() {
  return { title: tv.draft.title, view: tv.draft.view, steps: tv.draft.steps.map((s) => ({ view: s.view || {},
    ...(s.key ? { key: s.key } : {}), title: s.title, text: s.text, targets: s.targets,
    code: s.at ? { at: s.at, lines: s.lines, mark: s.mark.split(",").map((m) => parseInt(m, 10)).filter(Number.isFinite) } : null,
    notes: s.at ? T.parseLineNotes(s.notes).notes : [],
    ...(s.by ? { by: s.by } : {}), ...(s.ai ? { ai: true } : {}) })) };
}

async function saveDraft(keepEditing) {
  readEditor();
  const missing = missingLines();
  if (missing.length) {
    flash(`Fill in the code lines (+a..+b inside the anchor) of step ${missing.join(", ")} first.`, "edToast");
    return;
  }
  const bad = tv.draft.steps.findIndex((s) => T.parseLineNotes(s.notes).error);
  if (bad >= 0) {
    flash(`Step ${bad + 1}: ${T.parseLineNotes(tv.draft.steps[bad].notes).error}`, "edToast");
    return;
  }
  try {
    const tour = await sendJson(tourPath(tv.tour.id), "PUT", draftBody());
    await reloadTour(keepEditing, tour);
  } catch (error) {
    flash(`Not saved: ${error.message}`, "edToast");
  }
}

function readEditor() {
  if (!tv.editing || !$("edTitle")) return;
  const s = tv.draft.steps[tv.editIdx];
  tv.draft.title = $("edTourTitle").value;
  if (!s) return;
  s.title = $("edTitle").value;
  s.text = $("edText").value;
  s.targets = $("edTargets").value.split(",").map((x) => x.trim()).filter(Boolean);
  s.at = $("edAt").value.trim();
  s.lines = $("edLines").value.trim();
  s.mark = $("edMark").value.trim();
  s.notes = $("edNotes").value;
  readViewBox();
}

/** The graph selection as a target in CLI syntax: `Class`, `A>B`, `Class#function`. */
function selectionTarget() {
  const sel = state.selected;
  if (!sel) return "";
  if (sel.type === "edge") return `${sel.raw.from}>${sel.raw.to}`;
  return sel.raw.id;
}

function addStepFromSelection() {
  readEditor();
  const target = selectionTarget();
  if (!target) return;
  const at = target.includes(">") ? target.split(">")[0] : target;
  tv.draft.steps.splice(tv.editIdx + 1, 0, { title: `New step: ${target}`, text: `Explain [[${target}]] here.`,
    targets: [target], at, lines: "+0..+9", mark: "", notes: "", by: "", ai: false, audio: null, view: {} });
  tv.editIdx += 1;
  renderPanel();
}

function moveStep(i, j) {
  readEditor();
  const list = tv.draft.steps;
  [list[i], list[j]] = [list[j], list[i]];
  tv.editIdx = j;
  renderPanel();
}

function deleteStep(i) {
  readEditor();
  tv.draft.steps.splice(i, 1);
  tv.editIdx = Math.max(0, Math.min(tv.editIdx, tv.draft.steps.length - 1));
  renderPanel();
}

function editorHtml() {
  const list = tv.draft.steps, s = list[tv.editIdx];
  const target = selectionTarget();
  const rows = list.map((x, i) => `<li>
      <button class="mv" type="button" data-up="${i}" ${i === 0 ? "disabled" : ""} aria-label="Move up">↑</button>
      <button class="mv" type="button" data-down="${i}" ${i === list.length - 1 ? "disabled" : ""} aria-label="Move down">↓</button>
      <button class="go" type="button" data-edit="${i}" ${i === tv.editIdx ? 'aria-current="step"' : ""}><span class="n">${i + 1}</span><span class="nm">${esc(x.title)}</span>${x.ai ? '<span class="chip">AI</span>' : ""}</button>
      <button class="mv" type="button" data-del="${i}" aria-label="Delete step" title="Delete step">×</button></li>`).join("");
  const fields = s ? `<div class="sec">Step ${tv.editIdx + 1}</div>
    <label class="field">Title<input class="note-input" id="edTitle" value="${esc(s.title)}"></label>
    <label class="field">Targets (CLI syntax: Name, A&gt;B, Name#function)<input class="note-input mono" id="edTargets" value="${esc(s.targets.join(", "))}"></label>
    <label class="field">Code: anchor, lines inside it (+a..+b), marked line numbers<span class="field-row"><input class="note-input mono" id="edAt" value="${esc(s.at)}" aria-label="Anchor" placeholder="no code"><input class="note-input mono" id="edLines" value="${esc(s.lines)}" aria-label="Line offsets" placeholder="+0..+9"><input class="note-input mono" id="edMark" value="${esc(s.mark)}" aria-label="Marked line numbers" placeholder="32, 33"></span></label>
    <label class="field">Line notes (pop up on the code as the step plays; one per line: <code>50-51: text</code>, add <code>@ words</code> to show it when the text reaches those words)<textarea class="note-input mono" id="edNotes" rows="3" placeholder="50-51: Why these lines matter @ builds the graph">${esc(s.notes || "")}</textarea></label>
    <label class="field">Text (spoken by Play; [[links]] work)<textarea class="note-input" id="edText" rows="6">${esc(s.text)}</textarea></label>
    ${voiceBoxHtml(s)}
    <div class="row">${s.ai && s.key ? '<button class="btn small" type="button" data-act="accept-draft">Accept AI draft</button>' : ""}<button class="btn small" type="button" data-act="ask-ai" title="Copy a request for your AI chat">Ask AI to draft text</button></div>` : "";
  return `<div class="editbar tour-editbar">Editing tour <span>· saves to .coderstanding/tours/${esc(tv.tour.id)}.md</span><span class="grow"></span><button class="btn small" type="button" data-act="save">Save</button><button class="btn small" type="button" data-act="discard">Discard</button></div>
    <label class="field">Tour title<input class="note-input" id="edTourTitle" value="${esc(tv.draft.title)}"></label>
    <button class="btn" type="button" data-act="add-sel" ${target ? "" : "disabled"}>+ Add step from selection${target ? `: ${esc(target)}` : ""}</button>
    ${target ? "" : '<span class="muted small">Click a class, line or function row in the graph first.</span>'}
    <div class="sec">Steps (order)</div>
    <ol class="steplist">${rows}</ol>
    ${fields}
    ${viewBoxHtml()}
    <span class="toast" id="edToast" role="status"></span>`;
}

// ---------- the editor's View section: the tour's (or this step's) view options (view_options.js) ----------

const VO = ViewOptions;

/** The stored `view:` the View section edits: the tour's, or the edited step's. */
function editedView() {
  if (!tv.draft.steps[tv.editIdx]) tv.viewScope = "tour";
  return tv.viewScope === "step" ? (tv.draft.steps[tv.editIdx].view ||= {}) : tv.draft.view;
}

function viewValueText(opt, value) {
  if (value === undefined) return "";
  return opt.type === "bool" ? (value ? "on" : "off") : opt.type === "list" ? value.join(", ") : opt.names?.[value] || String(value);
}

/** One control per tour option; "not set" leaves it to the tour (for a step) or to the viewer's own settings. */
function viewFieldHtml(opt, view, under) {
  const value = VO.fromStored(view)[opt.key];
  const inherited = under[opt.key];
  const unset = `not set${inherited !== undefined ? ` (tour: ${esc(viewValueText(opt, inherited))})` : ""}`;
  const id = `edView-${opt.key}`;
  if (opt.type === "list" || opt.type === "string") {
    const text = value === undefined ? "" : opt.type === "list" ? value.join(", ") : value;
    const hint = opt.example || "";
    return `<label class="field view-opt">${esc(opt.label)}<input class="note-input mono" id="${id}" data-view-opt="${opt.key}" value="${esc(text)}" placeholder="${esc(inherited !== undefined ? `tour: ${viewValueText(opt, inherited)}` : hint)}"></label>`;
  }
  const values = opt.type === "bool" ? [true, false] : opt.values;
  const options = [`<option value="">${unset}</option>`, ...values.map((v) =>
    `<option value="${esc(String(v))}"${value === v ? " selected" : ""}>${esc(viewValueText(opt, v))}</option>`)];
  return `<label class="field view-opt">${esc(opt.label)}<select class="select" id="${id}" data-view-opt="${opt.key}">${options.join("")}</select></label>`;
}

function viewBoxHtml() {
  editedView();  // no step: the tour's
  const step = tv.viewScope === "step";
  const under = step ? VO.fromStored(tv.draft.view, { tourOnly: true }) : {};
  const fields = VO.TOUR_OPTIONS.map((opt) => viewFieldHtml(opt, editedView(), under)).join("");
  return `<details class="view-box" id="edViewBox"${tv.viewOpen ? " open" : ""}><summary>View <span class="muted small">(what the graph shows while this tour plays)</span></summary>
    <div class="seg small" role="group" aria-label="View options for">
      <button type="button" data-view-scope="tour" aria-pressed="${!step}">Whole tour</button>
      <button type="button" data-view-scope="step" aria-pressed="${step}"${tv.draft.steps[tv.editIdx] ? "" : " disabled"}>This step only</button>
    </div>
    <div class="view-grid">${fields}</div>
    <div class="row"><button class="btn small" type="button" data-act="view-current" title="Copy the graph tab's switches (the ones that differ from the defaults)">Use current graph settings</button>
    <button class="btn small" type="button" data-act="view-clear">Clear</button></div></details>`;
}

/** "View: Lines: All equal · Self calls: off" for a played step (the tour's options under its own). */
function viewSummary(s) {
  const values = { ...VO.fromStored(tv.tour.view, { tourOnly: true }), ...VO.fromStored(s.view, { tourOnly: true }) };
  const parts = Object.entries(values).map(([key, value]) => VO.valueText(key, value));
  return parts.length ? `<dt>View</dt><dd class="muted small" id="stepView">${esc(parts.join(" · "))}</dd>` : "";
}

/** The View section's controls -> the edited `view:` (URL names, only the set ones). */
function readViewBox() {
  if (!$("edViewBox")) return;
  const view = {};
  $("edViewBox").querySelectorAll("[data-view-opt]").forEach((el) => {
    const opt = VO.option(el.dataset.viewOpt);
    const value = el.value.trim() === "" ? undefined : VO.parse(opt, el.value);
    if (value !== undefined && !(opt.type === "list" && !value.length)) Object.assign(view, VO.toStored({ [opt.key]: value }));
  });
  setEditedView(view);
}

function setEditedView(view) {
  if (tv.viewScope === "step") tv.draft.steps[tv.editIdx].view = view; else tv.draft.view = view;
}

/** Re-show the step with the edited options (a live preview; nothing saved until Save). */
function previewView() {
  if (step()) showStepGraph(step());
}

function useCurrentView() {
  const keys = VO.TOUR_OPTIONS.map((o) => o.key);
  setEditedView(VO.toStored(VO.diff(graphOptions(), VO.defaults(), keys)));
  renderPanel();
  previewView();
}

function voiceBoxHtml(s) {
  const rec = s.audio?.rec;
  let row;
  if (tv.rec && tv.rec.idx === tv.editIdx) {
    row = '<span class="recdot">● Recording</span><span class="mono" id="recTime">0:00</span><button class="btn small primary" type="button" data-act="rec-stop">Stop</button>';
  } else if (!s.key) {
    row = '<span class="muted small">Save the tour first to record this step.</span>';
  } else if (rec) {
    row = `<button class="btn small" type="button" data-act="rec" ${tv.rec ? "disabled" : ""}>Re-record</button><button class="btn small" type="button" data-act="rec-del">Delete recording</button><span class="muted small">${T.fmtTime(rec.dur)}</span>`;
  } else {
    row = `<button class="btn small" type="button" data-act="rec" ${tv.rec ? "disabled" : ""}>● Record</button>`;
  }
  return `<div class="voicebox"><div class="row"><span class="stepno">Voice</span>${sourceChip(s)}</div>
    <div class="row">${row}</div>
    <span class="muted small">Optional. Your recording plays instead of the read-aloud voice; saved as .coderstanding/tours/${esc(tv.tour.id)}/${esc(s.key || "<key>")}.rec.webm (named by the step's key, so reordering keeps it). Without one, the browser voice reads the text and the seek bar counts words.</span></div>`;
}

function askAiForStep() {
  readEditor();
  const text = `Draft the text for step ${tv.editIdx + 1} of tour ${tv.tour.id} in ${CoderApps.current}: read it with \`coderstanding tour ${CoderApps.current} ${tv.tour.id} --step ${tv.editIdx + 1}\`, then write 2-4 plain sentences that explain the code it shows.`;
  askAi(text, (message) => flash(message, "edToast"));  // app.js: the one offline Ask AI helper
}

// ---------- recording (MediaRecorder, Opus in WebM; uploaded through the API) ----------

async function startRecording() {
  const s = tv.draft.steps[tv.editIdx];
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) { flash("This browser cannot record audio.", "edToast"); return; }
  readEditor();
  pause();
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 } });
  } catch (error) {
    flash(`No microphone (${error.name}). Allow it in the browser and try again.`, "edToast");
    return;
  }
  const type = T.recorderType((x) => MediaRecorder.isTypeSupported(x));
  const mr = new MediaRecorder(stream, type ? { mimeType: type, audioBitsPerSecond: 32000 } : undefined);
  const chunks = [];
  const rec = { mr, idx: tv.editIdx, key: s.key, start: performance.now() };
  mr.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
  mr.onstop = () => {
    stream.getTracks().forEach((track) => track.stop());
    clearInterval(rec.timer);
    tv.rec = null;
    // MediaRecorder WebM often has no duration header: send the measured length
    const seconds = Math.max(0.5, (performance.now() - rec.start) / 1000);
    uploadRecording(rec.key, new Blob(chunks, { type: mr.mimeType || type || "audio/webm" }), seconds);
  };
  tv.rec = rec;
  rec.timer = setInterval(() => { const el = $("recTime"); if (el) el.textContent = T.fmtTime((performance.now() - rec.start) / 1000); }, 250);
  mr.start(250);
  renderPanel();
}

function stopRecording() {
  tv.rec?.mr.stop();
}

async function uploadRecording(key, blob, seconds) {
  if (blob.size > MAX_RECORD_BYTES) { flash("Recording too long (over 5 MB, about 20 minutes).", "edToast"); renderPanel(); return; }
  await write(() => api(tourPath(tv.tour.id, `/audio/${enc(key)}?dur=${seconds.toFixed(2)}`),
    { method: "PUT", headers: { "Content-Type": blob.type || "audio/webm" }, body: blob }), true);
}

const deleteRecording = (key) => write(() => api(tourPath(tv.tour.id, `/audio/${enc(key)}`), { method: "DELETE" }), true);

// ---------- new tour ----------

function newTourId(title) {
  const base = title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "tour";
  let id = base;
  for (let n = 2; tv.list.some((t) => t.id === id); n++) id = `${base}-${n}`;
  return id;
}

function newTour() {
  pause();
  const title = "New tour";
  tv.tour = { id: newTourId(title), title, by: "you", ai: false, steps: [], isNew: true };
  tv.cur = 0;
  startEditing();
  const target = selectionTarget() || state.current?.nodes?.[0]?.id || "";
  if (target) {
    tv.draft.steps.push({ title: "First step", text: `Say what [[${target}]] does.`, targets: [target],
      at: target.split(">")[0], lines: "+0..+9", mark: "", notes: "", by: "", ai: false, audio: null, view: {} });
  }
  player.load({ title: "", text: "" });
  render();
}

// ---------- events ----------

function onPanelClick(event) {
  const el = event.target.closest("button, .wl, a[data-act]");
  if (!el) return;
  const d = el.dataset;
  if (d.target) { selectTarget(d.target); return; }
  if (d.go) { goTo(Number(d.go)); return; }
  if (d.edit) { readEditor(); tv.editIdx = Number(d.edit); renderPanel(); return; }
  if (d.up) { moveStep(Number(d.up), Number(d.up) - 1); return; }
  if (d.down) { moveStep(Number(d.down), Number(d.down) + 1); return; }
  if (d.del) { deleteStep(Number(d.del)); return; }
  if (d.viewScope) { readEditor(); tv.viewScope = d.viewScope; renderPanel(); return; }
  const actions = {
    edit: () => {
      if (!tv.editing) { startEditing(); render(); return; }
      saveDraft(false);
    },
    save: () => saveDraft(true),
    discard: () => {
      if (tv.tour.isNew) { tv.tour = null; tv.editing = false; render(); return; }
      tv.editing = false; render();
    },
    accept: () => acceptStep(step().key),
    "accept-draft": () => acceptStep(tv.draft.steps[tv.editIdx].key),
    "add-sel": addStepFromSelection,
    "ask-ai": askAiForStep,
    "copy-ai": copyAiText,
    rec: startRecording,
    "rec-stop": stopRecording,
    "rec-del": () => deleteRecording(tv.draft.steps[tv.editIdx].key),
    "view-current": useCurrentView,
    "view-clear": () => { setEditedView({}); renderPanel(); previewView(); },
  };
  actions[d.act]?.();
}

function onPanelInput(event) {
  if (!tv.editing || !event.target.closest(".field")) return;
  readEditor();
  if (event.target.id === "edTourTitle") $("tourPanel").querySelector(".tourhead h2").textContent = tv.draft.title;
}

function onListClick(event) {
  const pick = event.target.closest("[data-tour]");
  if (pick) openTour(pick.dataset.tour);
}

function onKey(event) {
  if ($("tabTours").hidden || event.ctrlKey || event.metaKey || event.altKey) return;
  if (event.target.closest?.("input, textarea, select, [contenteditable], [role=separator]")) return;
  if (event.key === "ArrowRight") { event.preventDefault(); goTo(tv.cur + 1); }
  else if (event.key === "ArrowLeft") { event.preventDefault(); goTo(tv.cur - 1); }
  else if (event.key === "Home") { event.preventDefault(); goTo(0); }
  else if (event.key === " " && !event.target.closest("button, a")) { event.preventDefault(); togglePlay(); }
}

function togglePlay() {
  if (player.playing) pause(); else play();
}

function bind() {
  tv.bound = true;
  $("tourList").addEventListener("click", onListClick);
  let searchTimer = 0;
  $("tourSearch").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadList, 200); });
  $("newTourBtn").addEventListener("click", newTour);
  $("newTourBtn").hidden = !editable();
  $("tourPanel").addEventListener("click", onPanelClick);
  $("tourPanel").addEventListener("input", onPanelInput);
  $("tourPanel").addEventListener("change", (event) => {
    if (!tv.editing || !event.target.closest("#edViewBox [data-view-opt]")) return;
    readViewBox();
    previewView();
  });
  $("tourPanel").addEventListener("toggle", (event) => {
    if (event.target.id === "aiText" && event.target.open) loadAiText();
    if (event.target.id === "edViewBox") tv.viewOpen = event.target.open;
  }, true);
  $("tourPanel").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && event.target.classList.contains("wl")) selectTarget(event.target.dataset.target);
  });
  document.addEventListener("keydown", onCodeKey);
  CopyMenu.bind($("codeSrc"), () => { const ref = selectionRef(); return ref && { ref, text: selectedCode() }; });
  $("codeSrc").addEventListener("click", (event) => { const link = event.target.closest(".lnote .wl"); if (link) selectTarget(link.dataset.target); });
  $("staleBar").addEventListener("click", (event) => { if (event.target.dataset.act === "still-true") stillTrue(step().key); });
  $("tourDots").addEventListener("click", (event) => { const b = event.target.closest("[data-go]"); if (b) goTo(Number(b.dataset.go)); });
  $("tourBack").addEventListener("click", () => goTo(tv.cur - 1));
  $("tourNext").addEventListener("click", () => goTo(tv.cur + 1));
  $("tourPlay").addEventListener("click", togglePlay);
  $("seekBar").addEventListener("input", onSeek);
  $("tourSpeed").addEventListener("change", () => player.setRate(Number($("tourSpeed").value) || 1));
  $("tourVoice").addEventListener("change", onVoice);
  $("tourAutoplay").checked = savedAutoplay();
  $("tourAutoplay").addEventListener("change", onAutoplay);
  $("tourReadNotes").checked = savedReadNotes();
  player.readNotes = $("tourReadNotes").checked;
  $("tourReadNotes").addEventListener("change", onReadNotes);
  document.addEventListener("keydown", onKey);
  document.addEventListener("coder:select", () => { if (tv.editing && !$("tabTours").hidden) { readEditor(); renderPanel(); } });
  bindResizer($("tourResizer"), { edge: "left", cssVar: "--details-w", element: () => $("tourPanel") },
    () => requestAnimationFrame(() => { cy.resize(); scheduleFan(); }));
  if (synth) synth.addEventListener("voiceschanged", fillVoices);
  fillVoices();
}

// ---------- entering and leaving the tab ----------

/** Move the graph tab's canvas (Cytoscape and the fan-out layers) to `parent`. */
function moveCanvas(parent, before = null) {
  parent.insertBefore($("canvas"), before);
  cy.resize();  // before the next fit, which reads the new size
  requestAnimationFrame(() => { cy.resize(); scheduleFan(); });
}

async function enter() {
  const first = !tv.bound;
  if (first) bind();
  tv.graph = { level: state.level, crumbs: state.crumbs, expanded: new Set(state.expanded), selected: state.selected };
  moveCanvas($("tourGraph"));
  render();
  if (first) {
    await loadList();
    const params = new URLSearchParams(location.search);
    const id = params.get("tour") || tv.list[0]?.id;
    if (id) {
      const stepIdx = (Number(params.get("step")) || 1) - 1, hl = hlFromUrl(params);
      if (hl) tv.refHit = { step: stepIdx, from: hl[0], to: hl[1] };  // the ref's lines, lit on that step
      await openTour(id, stepIdx, { edit: params.get("edit") === "1" });
      $("codeSrc").querySelector(".ref-hit")?.scrollIntoView({ block: "nearest" });
      if (params.get("comment")) CoderComments.reveal(params.get("comment"));
      return;
    }
  }
  if (step()) await showStepGraph(step());
  else { state.focus = null; state.tourStep = false; await useTourOptions(null); await loadView(); }
}

/** The `view:` options in play: the editor's draft while editing (a live preview), else the tour's. */
function viewLayers() {
  if (tv.editing && tv.draft) return [tv.draft.view, tv.draft.steps[tv.editIdx]?.view];
  return [tv.tour?.view, step()?.view];
}

async function leave() {
  pause();
  StartHere.show(null);
  tv.stepToken++;
  moveCanvas(document.querySelector("#tabGraph .stage"), $("detailsResizer"));
  state.focus = SpecView.graphFocus();  // back to the selected spec's highlight, if any
  state.tourStep = false;  // open boxes list everything again; the next draw puts classes on their own spots
  for (const key of [...state.openGroups]) if (key.endsWith("#more")) state.openGroups.delete(key);
  Object.assign(state, { level: tv.graph.level, crumbs: tv.graph.crumbs, expanded: tv.graph.expanded, selected: tv.graph.selected });
  const changed = await useTourOptions(null);  // the user's own options again
  if (changed.has("spots")) await reseedSpots(); else await loadView();
}

return { enter, leave, open: openTour, player, state: tv };
})();
