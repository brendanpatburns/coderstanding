// Source of truth for what each note field means: the "?" tips in the details panel and the
// "Note fields" block of the help text read this map. docs/AI_GUIDE.md and docs/DESIGN.md
// repeat the same wording (tests/js/test_field_help.mjs checks they stay in sync).
(function () {
const FIELD_HELP = {
  hint: { text: "One line on the box or line: what it is or does.", example: "Turns Net events into world changes" },
  description: { text: "What the class is and what its functions do; for a connection, what it represents and why it exists. No problems here.", example: "Owns the player list and spawns avatars. join() adds one, leave() removes it." },
  review: { text: "Findings from a code review: smells, risks, suggested splits with concrete targets.", example: "3800 lines; move request_chat* into net_chat.gd" },
  rules: { text: "Things never to do here; shown to anyone (and any AI) changing this code. One rule per line.", example: "Never call Net from here" },
  status: { text: "stable / wip / fragile / deprecated: how safe it is to change.", example: "fragile" },
  tags: { text: "Free labels for search and grouping, comma separated.", example: "net, audio" },
  level: { text: "How high up it shows: 1 is the top picture, higher numbers are more detail.", example: "2" },
  domain: { text: "The broad area it belongs to.", example: "World, Chat, Networking" },
};

const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

/** A "?" button plus its tip; hover/focus shows it (CSS), click toggles it (touch). "" for unknown names. */
function helpButton(name) {
  const help = FIELD_HELP[name];
  if (!help) return "";
  const id = `help-${name}`;
  return `<span class="help"><button type="button" class="help-btn" data-act="help" aria-label="What is ${esc(name)}?" aria-describedby="${id}">?</button>`
    + `<span class="help-tip" role="tooltip" id="${id}">${esc(help.text)} <em>e.g. ${esc(help.example)}</em></span></span>`;
}

/** The "Note fields" block for the empty details panel. */
function helpList() {
  return `<dl class="help-list">${Object.entries(FIELD_HELP).map(([name, h]) =>
    `<dt>${esc(name)}</dt><dd>${esc(h.text)} <em>e.g. ${esc(h.example)}</em></dd>`).join("")}</dl>`;
}

const api = { FIELD_HELP, helpButton, helpList };
if (typeof module !== "undefined") module.exports = api;
else window.FieldHelp = api;
})();
