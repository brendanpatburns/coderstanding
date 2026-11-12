// Progress tracks, viewer side (DESIGN 7g): the Progress picker in the legend row (view option
// `track`), the whole project's number beside it, a ring pill on each box that counts something,
// a bar on each overview domain card, a chip on each spec, a line in the details panel. No
// counting here: the server sends the numbers (progress.py). Loaded before app.js; uses its
// state, setOption and the box-face helpers (svgEl, textWidth, token) at call time. The pure part
// (percentText, ringDash, pillText, barHtml, chipHtml) is tested with node --test.
"use strict";

const ProgressView = (() => {
const RING = { r: 5.5, width: 2.6, pad: 6, height: 16 };  // the pill on a box's bottom right corner
const cards = { track: null, folder: null, numbers: {} };  // the spec / note chips' numbers

/** "33%": rounded down below 100, so nothing reads 100% before it is all done. */
function percentText(numbers) {
  if (!numbers || !numbers.total) return "-";
  const value = (100 * numbers.done) / numbers.total;
  return `${numbers.done === numbers.total ? 100 : Math.min(99, Math.floor(value))}%`;
}

/** The done arc of a ring of radius r: SVG stroke-dasharray "done rest". */
function ringDash(numbers, r = RING.r) {
  const around = 2 * Math.PI * r;
  const done = numbers.total ? (around * numbers.done) / numbers.total : 0;
  return `${done.toFixed(2)} ${(around - done).toFixed(2)}`;
}

/** The tooltip and screen reader text: "Proven: 1 of 3 done (33%)". */
function pillText(track, numbers) {
  return `${track}: ${numbers.done} of ${numbers.total} done (${percentText(numbers)})`;
}

const track = () => state.current?.progress?.track || null;

// ---------- the legend row: picker and the project's number ----------

function deltaText(delta) {
  if (delta === null || delta === undefined) return "";
  return delta === 0 ? " · no change since last scan" : ` · ${delta > 0 ? "+" : ""}${delta} since last scan`;
}

function renderControls() {
  const select = $("trackCtl");
  if (!select) return;
  const tracks = state.meta?.tracks || [];
  const names = tracks.map((t) => t.name);
  const wanted = state.track && !names.some((n) => n.toLowerCase() === state.track.toLowerCase()) ? [state.track] : [];
  select.innerHTML = [`<option value="">none</option>`,
    ...tracks.map((t) => `<option value="${esc(t.name)}" title="${esc(`${t.of} · done: ${t.done_rule}`)}">${esc(t.name)}</option>`),
    ...wanted.map((n) => `<option value="${esc(n)}">${esc(n)} (not in this project)</option>`)].join("");
  const match = names.find((n) => n.toLowerCase() === (state.track || "").toLowerCase());
  select.value = match || state.track || "";
}

/** "27.7% (67/242) · +3 since last scan" beside the picker (the view's `progress`). */
function renderSummary(view) {
  const out = $("trackSum");
  if (!out) return;
  const numbers = view?.progress;
  out.hidden = !numbers;
  if (!numbers) return;
  out.textContent = `${percentText(numbers)} (${numbers.done}/${numbers.total})${deltaText(numbers.delta)}`;
  const trend = (numbers.trend || []).slice(-8).map((t) => percentText(t));
  out.title = pillText(numbers.track, numbers) + (trend.length > 1 ? `\nLast ${trend.length} scans: ${trend.join(" → ")}` : "");
}

// ---------- a box: the ring pill (bottom right corner) ----------

function pillWidth(text) {
  return RING.pad + RING.r * 2 + 4 + textWidth(text, `600 10.5px ${token("f-body")}`) + RING.pad;
}

/** The pill on the box's bottom edge: a ring (done arc) and the percent. */
function drawRing(group, box, raw) {
  const numbers = raw.progress;
  if (!numbers || !track()) return;
  const text = percentText(numbers);
  const w = pillWidth(text);
  const x = box.x2 - w - 8, y = box.y2 - RING.height / 2;
  const pill = svgEl("g", { class: `prog-pill${numbers.done === numbers.total ? " full" : ""}`, role: "img", "data-id": raw.id,
    "aria-label": pillText(track(), numbers) }, group);
  svgEl("rect", { x, y, width: w, height: RING.height, rx: RING.height / 2 }, pill);
  const cx = x + RING.pad + RING.r, cy = y + RING.height / 2;
  svgEl("circle", { cx, cy, r: RING.r, class: "prog-track", "stroke-width": RING.width }, pill);
  if (numbers.done) {
    svgEl("circle", { cx, cy, r: RING.r, class: "prog-done", "stroke-width": RING.width, "stroke-dasharray": ringDash(numbers),
      transform: `rotate(-90 ${cx} ${cy})` }, pill);
  }
  svgEl("text", { x: cx + RING.r + 4, y: cy + 3.8 }, pill).textContent = text;
  svgEl("title", {}, pill).textContent = pillText(track(), numbers);
  pill.addEventListener("click", (event) => { event.stopPropagation(); select({ type: "node", raw }); });  // = a click on the box
}

// ---------- overview cards, spec chips, details ----------

/** A bar under an overview domain card's header ("" without numbers). Its width is set through
 *  the CSSOM by paintBars: the page's CSP allows no style attributes. */
function barHtml(trackName, numbers) {
  if (!trackName || !numbers || !numbers.total) return "";
  return `<div class="prog-bar" title="${esc(pillText(trackName, numbers))}"><span class="prog-meter" data-pct="${(100 * numbers.done) / numbers.total}"><i></i></span>
    <span class="prog-words">${esc(trackName)} ${percentText(numbers)} · ${numbers.done}/${numbers.total}</span></div>`;
}

function overviewBar(domain) {
  return barHtml(track(), domain.progress);
}

function paintBars(root) {
  root.querySelectorAll(".prog-meter[data-pct]").forEach((el) => el.style.setProperty("--p", `${el.dataset.pct}%`));
}

function chipHtml(trackName, numbers) {
  if (!trackName || !numbers || !numbers.total) return "";
  return `<span class="chip prog${numbers.done === numbers.total ? " full" : ""}" title="${esc(`${trackName}: ${numbers.done} of ${numbers.total} linked done`)}">${percentText(numbers)} ${esc(trackName)}</span>`;
}

/** A spec's or note's chip: share of its (and its finer cards') linked classes/functions done. */
function cardChip(cardId, folder) {
  return cards.folder === folder && cards.track === track() ? chipHtml(cards.track, cards.numbers[cardId]) : "";
}

/** Fetch the chips' numbers for a card folder (before its list draws); none without a track. */
async function loadCards(folder) {
  const name = state.track && track();
  if (!name) { Object.assign(cards, { track: null, folder, numbers: {} }); return; }
  try {
    const answer = await api(`/api/progress?${new URLSearchParams({ track: name, cards: folder })}`);
    Object.assign(cards, { track: answer.track, folder, numbers: answer.cards || {} });
  } catch {
    Object.assign(cards, { track: null, folder, numbers: {} });  // an older helper: no chips
  }
}

function detailsHtml(node) {
  const numbers = node?.progress;
  if (!numbers || !track()) return "";
  return `<div class="prog-detail">${barHtml(track(), numbers)}</div>`;
}

function bind() {
  $("trackCtl")?.addEventListener("change", (event) => setOption("track", event.target.value));
}

/** After a view is drawn: the summary, and the spec chips when the track changed. */
function afterLoad(view) {
  renderSummary(view);
  if (typeof SpecView !== "undefined" && SpecView.state.started && cards.track !== track()) SpecView.reload();
}

const api_ = { percentText, ringDash, pillText, barHtml, chipHtml, renderControls, renderSummary, drawRing,
  overviewBar, paintBars, cardChip, loadCards, detailsHtml, bind, afterLoad };
if (typeof module !== "undefined") module.exports = api_;
return api_;
})();
