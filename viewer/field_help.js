// The "?" tips and the "Note fields" block of the help text. They show the effective field rules
// (built-in docs/field_rules.json plus this project's overrides) that the server answers at
// GET /api/fields; app.js hands them to setRules(). The same rules feed `coderstanding guide`/`fields`.
(function () {
let rules = { rule: "", house: "", fields: {} };

const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const TEXT_KEYS = [["what", "What goes in"], ["who", "Who it is for"], ["not", "What does not"], ["good", "Good example"], ["bad", "Bad example"]];

function setRules(next) { rules = next; }

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

const api = { setRules, helpButton, helpList, editorHtml, readForm };
if (typeof module !== "undefined") module.exports = api;
else window.FieldHelp = api;
})();
