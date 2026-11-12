// The 30-second overview of level 0 (approved mock-up docs/mockups/readability.html idea 5): one
// flow sentence with a clickable chip per domain, then a card per domain (what it is for, key
// classes, who it talks to). The server builds it (views.py `overview`, overview.py); this file
// only draws it. The chips and tints are the domain frames' (domains.js `hueOf`, style.css).
// `chunks` is pure (tests/js/test_overview.mjs).
const CoderOverview = (() => {
const MAP = "map";  // the saved choice: show level 0 as today's map
const ASK = (app) => `Write the flow sentence of ${app}'s 30-second overview: run \`coderstanding graph ${app} 0\`, then save one or two plain sentences that tell a newcomer how the parts work together, naming each domain as written and in the order the work flows, with \`coderstanding set ${app} "domain:*" description="..."\`.`;

const escapeRe = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** The sentence cut into [{text}] and [{domain}] pieces: every domain name (whole words, longest first). */
function chunks(sentence, names) {
  if (!sentence) return [];
  if (!names.length) return [{ text: sentence }];
  const found = new RegExp(`(?<![\\w])(${[...names].sort((a, b) => b.length - a.length).map(escapeRe).join("|")})(?![\\w])`, "g");
  const out = [];
  let last = 0;
  for (const m of sentence.matchAll(found)) {
    if (m.index > last) out.push({ text: sentence.slice(last, m.index) });
    out.push({ domain: m[1] });
    last = m.index + m[1].length;
  }
  if (last < sentence.length) out.push({ text: sentence.slice(last) });
  return out;
}

/** The sentence to show: the written note, else the generated one (`written` says which). */
function sentenceOf(overview) {
  return { text: overview.sentence || overview.generated || "", written: Boolean(overview.sentence) };
}

function chipHtml(name) {
  return `<span class="dchip" data-hue="${esc(name)}" data-d="${esc(name)}" role="button" tabindex="0">${esc(name)}</span>`;
}

function storyHtml(overview) {
  const names = overview.domains.map((d) => d.name);
  const { text, written } = sentenceOf(overview);
  const body = chunks(text, names).map((c) => c.domain ? chipHtml(c.domain) : esc(c.text)).join("");
  const note = written ? (overview.ai ? `<span class="muted ov-by">AI draft</span>` : "")
    : `<div class="ov-ask muted">A plain sentence made from the lines. <button type="button" class="btn small" data-ask="1" title="Copy a request that asks the AI to write a better one">Ask AI to write it</button></div>`;
  return `<div class="story" id="ovStory" aria-label="How the parts work together">${body || "No lines between the domains yet."} ${note}</div>`;
}

function talkHtml(row) {
  return `<span class="ov-talk"><span class="dchip small" data-hue="${esc(row.to)}" data-d="${esc(row.to)}" role="button" tabindex="0">${esc(row.to)}</span>${row.hint ? ` <span class="muted">${esc(row.hint)}</span>` : ""}</span>`;
}

function cardHtml(d) {
  const what = d.description || d.hint;
  const bar = typeof ProgressView === "undefined" ? "" : ProgressView.overviewBar(d);
  return `<div class="dcard" data-hue="${esc(d.name)}" data-d="${esc(d.name)}"><div class="dh">${esc(d.name)}<span>${d.classes} class${d.classes === 1 ? "" : "es"}</span></div>${bar}
    <div class="db">${what ? esc(what) : `<span class="muted">No description yet</span>`}
    ${d.talks.length ? `<div class="ov-talks"><span class="muted">talks to</span> ${d.talks.map(talkHtml).join(" ")}</div>` : ""}
    ${d.keys.length ? `<div class="keys">${d.keys.map((k) => `<code title="${k.title ? esc(k.name) + " · " : ""}used by ${k.used_by}">${esc(k.title || k.name)}</code>`).join("")}</div>` : ""}</div></div>`;
}

function html(overview) {
  return `<div class="ov-bar"><h3>What ${esc(CoderApps.current || "this app")} is made of</h3><button type="button" class="btn small" id="ovMap" title="Today's level 0: the domains as boxes with lines between them">Show on map</button></div>
    ${storyHtml(overview)}<div class="dcards" id="ovCards">${overview.domains.map(cardHtml).join("")}</div>`;
}

let picked = null;  // the domain whose card is lit

/** Light one domain's card (its chips too), dim the rest; the same pick again clears. */
function pick(name) {
  picked = picked === name ? null : name;
  const root = document.getElementById("overview");
  root.querySelectorAll(".dchip").forEach((c) => c.classList.toggle("on", c.dataset.d === picked));
  root.querySelectorAll(".dcard").forEach((c) => c.classList.toggle("dim", Boolean(picked) && c.dataset.d !== picked));
  if (picked) root.querySelector(`.dcard[data-d="${CSS.escape(picked)}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

/** Draw the overview into #overview (tints through the CSSOM: the page's CSP allows no style attributes). */
function render(overview) {
  const root = document.getElementById("overview");
  root.innerHTML = html(overview);
  root.querySelectorAll("[data-hue]").forEach((el) => el.style.setProperty("--h", CoderDomains.hueOf(el.dataset.hue)));
  if (typeof ProgressView !== "undefined") ProgressView.paintBars(root);
  picked = null;
}

function bind(onMap, ask) {
  const root = document.getElementById("overview");
  root.addEventListener("click", (event) => {
    if (event.target.closest("#ovMap")) onMap();
    else if (event.target.closest("[data-ask]")) ask(ASK(CoderApps.current));
    else { const chip = event.target.closest(".dchip"); if (chip) pick(chip.dataset.d); }
  });
  root.addEventListener("keydown", (event) => {
    const chip = event.target.closest(".dchip");
    if (chip && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); pick(chip.dataset.d); }
  });
}

const api = { MAP, chunks, sentenceOf, render, bind, pick };
if (typeof module !== "undefined") module.exports = api;
return api;
})();
