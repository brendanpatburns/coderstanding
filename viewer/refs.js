// Location refs: one text line that names a place (code lines, tour step, comment, spec, class,
// function) so a person or an AI can paste it and the viewer can open it again. The backend
// (refs.py, GET /api/ref) owns resolving; this is only the writing side: format a ref, make its
// link, and make the clipboard contents. Runs under `node --test` too.
"use strict";

(() => {
const enc = encodeURIComponent;
const escHtml = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

/** The hint for a code-lines ref `path:from-to`: the enclosing unit and the lines' offsets below
 *  the unit's first line (`first`), e.g. `Peer#send_voice +4..+18`, so the lines are found again
 *  when the unit moved. No unit: no hint. The only place that decides how that hint looks. */
function codeHint(unit, first = null, from = null, to = null) {
  if (!unit) return "";
  if (first == null || from == null || from < first) return unit;
  return `${unit} +${from - first}..+${to - first}`;
}

/** `@<app> <target> [(<hint>)]` */
function formatRef(app, target, hint = "") {
  return `@${app} ${target}${hint ? ` (${hint})` : ""}`;
}

/** The target part of a ref, one builder per kind. */
const span = (from, to) => (to == null || to === from ? `${from}` : `${from}-${to}`);  // "5" or "5-9"
const target = {
  lines: (path, from, to) => `${path}:${span(from, to)}`,
  member: (cls, fn) => (fn ? `${cls}#${fn}` : cls),
  tour: (id, step = null, from = null, to = null) => {
    const base = step == null ? `tour ${id}` : `tour ${id} step ${step}`;
    return from == null ? base : `${base} lines ${span(from, to)}`;
  },
  comment: (id) => `comment ${id}`,
  card: (kind, id) => `${kind} ${id}`,  // kind: "spec" | "memo"
};

/** Sorted [first, last] of two line numbers (a selection can run backwards). */
function lineRange(a, b) {
  return a <= b ? [a, b] : [b, a];
}

/** The link that opens `ref` in the viewer: `base` is the page's own origin + path. */
function refLink(base, app, ref) {
  return `${base}?app=${enc(app)}&ref=${enc(ref)}`;
}

/** What goes on the clipboard for one or more refs: the ref lines as text, a link per line as HTML. */
function clipboardParts(app, refs, base) {
  const html = refs.map((ref) => `<a href="${escHtml(refLink(base, app, ref))}">${escHtml(ref)}</a>`).join("<br>");
  return { text: refs.join("\n"), html };
}

// The two icons, defined once (stroke style, currentColor so every theme works).
const svg = (paths) => `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
const ICON_TEXT = svg('<rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>');  // two overlapping squares
const ICON_LOCATION = svg('<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>');  // chain link
const COPY_TEXT = { label: "Copy text", icon: ICON_TEXT, title: "Copy this item's text" };
const COPY_LOCATION = { label: "Copy location", icon: ICON_LOCATION, title: "Copy a location ref to this place: paste it to an AI or a person, or open the link" };

/** One copy button: an icon button (`menu` false) or a labelled menu item (`menu` true). */
function oneButton(kind, attr, value, menu) {
  const open = `<button type="button" ${attr}="${escHtml(value)}"`;
  if (menu) return `${open} class="copy-item" role="menuitem">${kind.icon}<span>${kind.label}</span></button>`;
  return `${open} class="btn small ref-btn" title="${kind.title}" aria-label="${kind.label}">${kind.icon}</button>`;
}

/** The one control for every copy point: Copy text (the item's own text, plain) and Copy location
 *  (the ref line + link) side by side, as icon buttons or (menu) as the right-click menu's items.
 *  app.js copies `data-copy-text` / `data-copy-ref` on click. No text (`""`) = no Copy text. */
function copyButtons(ref, text = "", menu = false) {
  const buttons = (text ? oneButton(COPY_TEXT, "data-copy-text", text, menu) : "") + oneButton(COPY_LOCATION, "data-copy-ref", ref, menu);
  return menu ? `<div class="copy-menu" role="menu">${buttons}</div>` : `<span class="copy-pair">${buttons}</span>`;
}

const api = { codeHint, formatRef, target, lineRange, refLink, clipboardParts, copyButtons };
if (typeof module !== "undefined") module.exports = api;
else window.CoderRefs = api;
})();
