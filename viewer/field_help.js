// The "?" tips and the "Note fields" block of the help text. They show the effective field rules
// (built-in docs/field_rules.json plus this project's overrides) that the server answers at
// GET /api/fields; app.js hands them to setRules(). The same rules feed `coderstanding guide`/`fields`.
// The same answer carries `words` (plain words for line kinds and glossary terms, not note fields):
// the legend, the line sentence and the dotted-underline glossary read them here. An older helper sends
// no `words`; the legend then falls back to the kind names it always showed.
(function () {
let rules = { rule: "", house: "", fields: {}, words: { kinds: {}, terms: {} } };
const KIND_ORDER = ["composition", "association", "dependency", "signal", "loaded-by", "manual", "planned", "generalization", "realization", "derived"];
const OLD_LABELS = { "loaded-by": "loaded by (by name)", derived: "through hidden classes" };  // today's legend text

const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const TEXT_KEYS = [["what", "What goes in"], ["who", "Who it is for"], ["not", "What does not"], ["good", "Good example"], ["bad", "Bad example"]];

function setRules(next) { rules = { ...next, words: { kinds: {}, terms: {}, ...next.words } }; }

/** The plain words of a line kind; without the helper's `words` the kind name itself (no UML name, no tip). */
function kindWords(kind) {
  return rules.words.kinds[kind] || { word: OLD_LABELS[kind] || kind, uml: "", tip: "" };
}

/** Legend entries: the kinds in the view plus the hidden ones (so they can come back), in a fixed order. */
function legendEntries(present, hidden) {
  const all = new Set([...present, ...hidden]);
  const order = [...KIND_ORDER, ...[...all].filter((k) => !KIND_ORDER.includes(k)).sort()];
  return order.filter((k) => all.has(k)).map((kind) => ({ kind, ...kindWords(kind), hidden: hidden.includes(kind) }));
}

/** One sentence for a line, "A uses B: hint", with the kind's tip and the call names below it. */
function lineTipHtml({ from, to, kind, hint, calls }) {
  const w = kindWords(kind);
  const note = hint ? `: ${esc(hint)}` : ` <span class="muted">(no note yet)</span>`;
  return `<b>${esc(from)}</b> ${esc(w.word)} <b>${esc(to)}</b>${note}`
    + (w.tip ? `<span class="k">${esc(w.word)} = ${esc(w.tip)}</span>` : "")
    + (calls ? `<span class="k">calls: ${esc(calls)}</span>` : "");
}

/** What goes in, who it is for, what does not, with a good and a bad example. */
function tipHtml(h) {
  return `${esc(h.what)}<br><b>For:</b> ${esc(h.who)}<br><b>Not:</b> ${esc(h.not)}`
    + `<br><em>Good: ${esc(h.good)}</em><br><em>Bad: ${esc(h.bad)}</em>`;
}

/** A "?" button plus its tip; hover/focus shows it (CSS), click toggles it (touch). "" for unknown names. */
function helpButton(name) {
  const help = rules.fields[name];
  if (!help) return "";
  const id = `help-${name}`;
  return `<span class="help"><button type="button" class="help-btn" data-act="help" aria-label="What is ${esc(name)}?" aria-describedby="${id}">?</button>`
    + `<span class="help-tip" role="tooltip" id="${id}">${tipHtml(help)}</span></span>`;
}

/** The "Words" list of the empty details panel: line kinds, then terms. */
function wordsList() {
  const { kinds, terms } = rules.words;
  const lines = KIND_ORDER.filter((k) => kinds[k]).map((k) => `<dt>${esc(kinds[k].word)} <span class="muted">${esc(kinds[k].uml)}</span></dt><dd>${esc(kinds[k].tip)}</dd>`)
    .concat(Object.entries(terms).map(([term, text]) => `<dt>${esc(term)}</dt><dd>${esc(text)}</dd>`));
  return lines.length ? `<dl class="help-list words-list">${lines.join("")}</dl>` : "";
}

// ---- glossary: dotted underline on the first use of a term in the page's own text ----

/** Text that is someone's own writing or code, a form or a control: never underlined. */
const NO_TERMS = "textarea, input, select, button, code, pre, kbd, svg, script, style, .term, .help-tip, .word-tip, [data-field]:not(dt), .note, .spec-body, .card-body";
const termPattern = () => {
  const names = Object.keys(rules.words.terms).sort((a, b) => b.length - a.length);
  const part = (n) => (n === "via" ? "via(?: \\d+)?" : n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return names.length ? new RegExp(`\\b(${names.map(part).join("|")})(?:s)?\\b`, "gi") : null;
};
const termKey = (text) => {
  const t = text.toLowerCase().replace(/s$/, "");
  return t.startsWith("via") ? "via" : (t in rules.words.terms ? t : text.toLowerCase());
};

/** Wrap the first use of each known term below `root` (once per term per root; text nodes only, so notes and code stay as written). */
function explainTerms(root) {
  const pattern = termPattern();
  if (!pattern) return;
  const done = new Set([...root.querySelectorAll(".term")].map((el) => el.dataset.term));
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode()) if (!n.parentElement.closest(NO_TERMS)) nodes.push(n);
  for (let i = 0; i < nodes.length; i++) {
    const node = nodes[i];
    pattern.lastIndex = 0;
    let m = pattern.exec(node.nodeValue);
    while (m && done.has(termKey(m[1]))) m = pattern.exec(node.nodeValue);  // a repeat: look for another term
    if (!m) continue;
    const key = termKey(m[1]);
    done.add(key);
    const span = document.createElement("span");
    span.className = "term";
    span.tabIndex = 0;
    span.dataset.term = key;
    span.textContent = m[0];
    const after = node.splitText(m.index);
    after.nodeValue = after.nodeValue.slice(m[0].length);
    node.parentNode.insertBefore(span, after);
    nodes.splice(i + 1, 0, after);  // the rest of the text may hold another term
  }
}

let tipEl = null;
/** The floating sentence box (lines and terms share it); x/y are viewport pixels. */
function showTip(html, x, y) {
  if (!tipEl) {
    tipEl = document.createElement("div");
    tipEl.className = "word-tip";
    tipEl.setAttribute("role", "tooltip");
    document.body.appendChild(tipEl);
  }
  tipEl.innerHTML = html;
  tipEl.hidden = false;
  tipEl.style.left = `${Math.max(8, Math.min(x + 14, innerWidth - tipEl.offsetWidth - 8))}px`;
  tipEl.style.top = `${Math.max(8, Math.min(y + 14, innerHeight - tipEl.offsetHeight - 8))}px`;
}
function hideTip() { if (tipEl) tipEl.hidden = true; }

/** Keep the roots (selectors) underlined as the app rewrites them, and show a term's definition on hover, tap and focus. */
function watchWords(selectors) {
  let queued = false;
  const run = () => {
    queued = false;
    selectors.forEach((sel) => document.querySelectorAll(sel).forEach(explainTerms));
  };
  const queue = () => { if (!queued) { queued = true; requestAnimationFrame(run); } };
  const observer = new MutationObserver(queue);
  selectors.forEach((sel) => document.querySelectorAll(sel).forEach((el) => observer.observe(el, { childList: true, subtree: true, characterData: true })));
  queue();
  const show = (event) => {
    const el = event.target.closest?.(".term");
    if (!el) return;
    const box = el.getBoundingClientRect();
    const at = event.clientX ? { x: event.clientX, y: event.clientY } : { x: box.left, y: box.bottom };
    showTip(`<b>${esc(el.textContent)}</b><br>${esc(rules.words.terms[el.dataset.term] || "")}`, at.x, at.y);
  };
  document.addEventListener("pointerover", show);
  document.addEventListener("focusin", show);
  document.addEventListener("click", (event) => { if (event.target.closest(".term")) show(event); else hideTip(); });
  document.addEventListener("pointerout", (event) => { if (event.target.closest?.(".term")) hideTip(); });
  document.addEventListener("focusout", hideTip);
}

/** The "Note fields" block for the empty details panel. */
function helpList() {
  const house = rules.house ? `<p><b>House rules for AI notes:</b> ${esc(rules.house)}</p>` : "";
  return `<p class="muted">${esc(rules.rule)}</p>${house}<dl class="help-list">${Object.entries(rules.fields).map(([name, h]) =>
    `<dt>${esc(name)}${h.custom.length ? ' <span class="muted">(this project)</span>' : ""}</dt><dd>${tipHtml(h)}</dd>`).join("")}</dl>`
    + `<button type="button" class="btn" data-act="edit-fields">Edit field rules</button>`;
}

/** A form per field (and the house rules) with "reset to default"; read back by readForm. */
function editorHtml() {
  const box = ([key, label], name, h) => `<label>${label}<textarea rows="2" class="fr-input" data-field="${name}" data-key="${key}">${esc(h[key])}</textarea></label>`;
  const fields = Object.entries(rules.fields).map(([name, h]) => `<fieldset><legend>${esc(name)}</legend>`
    + TEXT_KEYS.map((k) => box(k, name, h)).join("")
    + `<label>Max characters (0 = no limit) <input type="number" min="0" class="fr-input" data-field="${name}" data-key="max" value="${h.max}"></label>`
    + `<label><input type="checkbox" class="fr-input" data-field="${name}" data-key="code_check"${h.code_check ? " checked" : ""}> Remind AI when the text reads like code</label>`
    + `<button type="button" class="btn" data-act="fields-reset" data-field="${esc(name)}">Reset ${esc(name)} to default</button></fieldset>`).join("");
  return `<h3>Field rules</h3><p class="muted">Saved with the project (shared through git). Anything left as the default is not stored.</p>`
    + `<label>House rules for AI notes<textarea rows="3" class="fr-input" data-field="house" data-key="house">${esc(rules.house)}</textarea></label>`
    + `${fields}<button type="button" class="btn" data-act="fields-save">Save</button> <button type="button" class="btn" data-act="fields-cancel">Cancel</button>`
    + `<span class="note-error" id="fieldsError" hidden></span>`;
}

/** The PUT /api/fields body from the form: only values that differ from the shown rules, so untouched ones stay default. */
function readForm(root) {
  const set = {};
  let house;
  root.querySelectorAll(".fr-input").forEach((el) => {
    const { field, key } = el.dataset;
    const value = el.type === "checkbox" ? (el.checked ? "on" : "off") : el.value;
    if (field === "house") { if (value !== rules.house) house = value; return; }
    if (String(value) !== String(el.type === "checkbox" ? (rules.fields[field][key] ? "on" : "off") : rules.fields[field][key])) set[`${field}.${key}`] = value;
  });
  return { set, house };
}

// ---------- "In plain words": the top block of the details panel for a selected class or line ----------

const lists = () => (typeof Lists !== "undefined" ? Lists : require("./lists.js"));  // lists.js folds long lists
const ASK_LINE = "Copy a request for the AI to write what this line is for";

/** A class's lines in plain words, [usedBy, uses], explained ones first; each {key, other, hint, word, call}.
 *  `nameOf` (id -> shown name), `kindOf` (line -> kind for kindWords), `hintOf` (line -> its hint or ""). */
function plainLines(id, edges, { nameOf = (x) => x, kindOf = (e) => e.kinds?.[0], hintOf = (e) => e.hint || "" } = {}) {
  const item = (e, other) => ({ key: e.key, other: nameOf(other), hint: hintOf(e) || "", word: kindWords(kindOf(e)).word, call: e.calls?.[0]?.member || "" });
  const explainedFirst = (items) => items.sort((a, b) => Number(!a.hint) - Number(!b.hint));
  const real = edges.filter((e) => e.from !== e.to);
  return [explainedFirst(real.filter((e) => e.to === id).map((e) => item(e, e.from))),
    explainedFirst(real.filter((e) => e.from === id).map((e) => item(e, e.to)))];
}

/** One line of a Used by / Uses list: "Other — hint", or the kind's word + first call + (no note yet) and Ask AI. */
function plainLineItem(line) {
  if (line.hint) return `<li><b>${esc(line.other)}</b> — ${esc(line.hint)}</li>`;
  const what = `${line.word}${line.call ? ` ${line.call}` : ""}`;
  return `<li><b>${esc(line.other)}</b> <span class="nonote">— ${esc(what)} (no note yet)</span>`
    + `<button class="btn ask" type="button" data-act="ask-line" data-key="${esc(line.key)}" title="${ASK_LINE}">Ask AI</button></li>`;
}

function plainList(label, lines, none) {
  const items = lines.map(plainLineItem);
  return `<div class="grp">${esc(label)} (${lines.length})</div>${items.length ? lists().collapsedList(items) : `<ul><li class="nonote">${esc(none)}</li></ul>`}`;
}

/** The plain-words block of a class: name, where it sits, what it does (or Ask AI), who uses it, what it uses.
 *  `where` = plain text parts ("Viewer", "level 1 · Systems", "12 functions"); `warnings` = how many. */
function classPlainHtml({ name, where = [], warnings = 0, what = "", usedBy = [], uses = [] }) {
  const warn = warnings ? `${where.length ? " · " : ""}<span class="warn">${warnings} warning${warnings === 1 ? "" : "s"}</span>` : "";
  const lead = what ? `<p class="lead">${esc(what)}</p>`
    : `<p class="lead nonote">No description yet <button class="btn ask" type="button" data-act="ask-class" title="Copy a request for the AI to write what this class does">Ask AI</button></p>`;
  return `<section class="plain" aria-label="In plain words">`
    + `<h3>${esc(name)}</h3><div class="where">${where.map(esc).join(" · ")}${warn}</div>${lead}`
    + plainList("Used by", usedBy, "Nothing at this level: it starts things.")
    + plainList("Uses", uses, "Nothing at this level.") + `</section>`;
}

/** The plain-words block of a line: "A uses B: hint" (the line sentence), Ask AI when it has no note. */
function linePlainHtml({ from, to, kind, hint, calls, key }) {
  const ask = hint ? "" : ` <button class="btn ask" type="button" data-act="ask-line" data-key="${esc(key)}" title="${ASK_LINE}">Ask AI</button>`;
  return `<section class="plain" aria-label="In plain words">`
    + `<h3>${esc(from)} → ${esc(to)}</h3><p class="lead line-sentence">${lineTipHtml({ from, to, kind, hint, calls })}${ask}</p></section>`;
}

const api = { setRules, helpButton, helpList, editorHtml, readForm, kindWords, legendEntries, lineTipHtml, wordsList, explainTerms, watchWords, showTip, hideTip,
  plainLines, classPlainHtml, linePlainHtml };
if (typeof module !== "undefined") module.exports = api;
else window.FieldHelp = api;
})();
