// Specs | Notes sidebar (DESIGN 7, 7d): one component for both card kinds (specs and memos, which
// the UI calls Notes). Nested boxes with stage and priority chips, a level slider (or "follow
// graph"), folding with "+N finer", zoom with breadcrumbs, search, stage/priority filters and
// sorting, and editing (add, sub-item, title, stage, priorities, level, text, links, move, delete,
// Accept). The server folds, searches, filters and checks (cards.py); selecting an item asks the
// server's view with its links as `focus`. The tab's folder (`specs` | `memos`) is the API path.
"use strict";

const SpecView = (() => {
const FOLDERS = ["specs", "memos"];
const blank = () => ({
  data: null,          // last GET /api/<folder> answer
  level: null,         // the sidebar's own slider (null = every level)
  follow: false,       // tie the level to the graph slider
  zoom: null,          // id zoomed into
  query: "", stage: "", priority: "", importance: "", sort: "",
  selected: null,      // id
  editing: false,      // the selected item's edit form is open
  confirming: false,   // the selected item's delete confirm is shown
  picks: [],           // class picker results
});
const sv = {
  folder: "specs",
  tabs: Object.fromEntries(FOLDERS.map((f) => [f, blank()])),
  linked: new Map(),   // view id (class id or "A→B") -> [hit] | "loading"
  drag: null,          // id being dragged
  started: false,
};
const v = () => sv.tabs[sv.folder];
const enc = encodeURIComponent;
const tab = (folder = sv.folder) => document.querySelector(`.card-tabs [data-folder="${folder}"]`);
const oneWord = () => tab().dataset.one;  // "spec" | "note" (UI words)
const cardPath = (id, rest = "") => `/api/${sv.folder}/${enc(id)}${rest}`;
const items = () => v().data?.items || {};
const sendJson = (path, method, body) => api(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

// ---------- loading ----------

function shownLevel() {
  return v().follow ? state.level : v().level;
}

async function load() {
  const t = v();
  const params = new URLSearchParams();
  if (shownLevel() != null) params.set("level", shownLevel());
  for (const name of ["zoom", "query", "stage", "priority", "importance", "sort"]) {
    if (t[name]) params.set(name === "query" ? "q" : name, t[name]);
  }
  try {
    t.data = await api(`/api/${sv.folder}?${params}`);
  } catch (error) {
    if (t.zoom && /no (spec|memo)/.test(error.message)) { t.zoom = null; return load(); }
    message(`Could not load: ${error.message}`, true);
    return;
  }
  if (t.selected && !items()[t.selected]) t.selected = null;
  render();
}

let msgTimer = 0;
function message(text, isError = false) {
  const box = $("specMsg");
  box.textContent = text;
  box.classList.toggle("error", isError);
  clearTimeout(msgTimer);
  msgTimer = setTimeout(() => { box.textContent = ""; }, isError ? 8000 : 3000);
}

/** Every write answers with the item (or what a delete moved): show it, then the new tree. */
async function write(action, done = "Saved") {
  try {
    const answer = await action();
    message(done);
    sv.linked.clear();
    await load();
    return answer;
  } catch (error) {
    message(`Not saved: ${error.message}`, true);
    return null;
  }
}

const putCard = (id, body, done) => write(() => sendJson(cardPath(id), "PUT", body), done);

// ---------- rendering ----------

function stageLabel(stage, data = v().data) {
  return data?.stage_labels?.[stage] || stage;
}

function stageChip(stage, label = stageLabel(stage)) {
  return `<span class="chip spec-status st-${esc(stage)}">${esc(label)}</span>`;
}

/** Work priority and importance chips (only the ones set). */
function priorityChips(item) {
  return (item.priority ? `<span class="chip prio p-${esc(item.priority)}" title="Work priority (how soon): ${esc(item.priority)}">work: ${esc(item.priority)}</span>` : "")
    + (item.importance ? `<span class="chip prio p-${esc(item.importance)}" title="Importance (how much it matters): ${esc(item.importance)}">imp: ${esc(item.importance)}</span>` : "");
}

function aiChip(item) {
  return item.ai ? `<span class="chip" title="AI draft fields: ${esc(item.ai_fields.join(", "))}">AI draft</span>` : "";
}

function options(values, current, labelOf = (x) => x) {
  return values.map((x) => `<option value="${esc(x)}" ${x === (current || "") ? "selected" : ""}>${esc(labelOf(x))}</option>`).join("");
}

/** The quick stage picker on every box: a chip-sized select. */
function stagePicker(item) {
  return `<select class="stage-pick st-${esc(item.stage)}" data-stage-of="${esc(item.id)}" aria-label="Stage of ${esc(item.title)}" title="Stage">${options(v().data.stages, item.stage, (s) => stageLabel(s))}</select>`;
}

function boxHtml(box) {
  const item = items()[box.id];
  const selected = box.id === v().selected;
  const finer = box.finer ? `<button class="spec-finer" type="button" data-zoom="${esc(box.id)}" title="Zoom into ${esc(item.title)}: it and one level more">+${box.finer} finer</button>` : "";
  return `<div class="spec-box${selected ? " sel" : ""}" data-spec="${esc(box.id)}">
    <div class="spec-row" data-drop="${esc(box.id)}" draggable="true" title="Drag onto another item to move it there">
      <button class="spec-pick" type="button" data-pick="${esc(box.id)}" aria-pressed="${selected}" title="L${item.level} · ${esc(item.id)}">${esc(item.title)}</button>
      ${stagePicker(item)}${priorityChips(item)}${aiChip(item)}${item.problems.length ? `<span class="chip stale" title="${esc(item.problems.join("\n"))}">!</span>` : ""}${finer}
    </div>
    ${selected ? cardHtml(item, box) : ""}
    ${box.children.length ? `<div class="spec-kids">${box.children.map(boxHtml).join("")}</div>` : ""}
  </div>`;
}

function linkChips(item, box) {
  const lost = new Set(item.unknown_links);
  const own = item.links.map((c) => `<span class="cchip${lost.has(c) ? " lost" : ""}" title="${lost.has(c) ? "Not in the graph" : "Select in the graph"}">
      <span class="wl-like" data-target="${esc(c)}" role="link" tabindex="0">${esc(c.replace(/>|→/, " → "))}</span><button type="button" data-detach="${esc(c)}" aria-label="Unlink ${esc(c)}" title="Unlink">×</button></span>`);
  const folded = (box?.folded_links || []).map((c) => `<span class="cchip folded" title="From a folded finer item">${esc(c.replace(/>|→/, " → "))}</span>`);
  return [...own, ...folded].join("") || '<span class="muted small">No links yet.</span>';
}

/** The graph selection as a link target: a class, a line `A>B`, or a function `A#f`. */
function selectedTarget() {
  const sel = state.selected;
  if (!sel) return "";
  return sel.type === "edge" ? `${sel.raw.from}>${sel.raw.to}` : sel.raw.id;
}

function targetName(target) {
  return target.split(">").map((part) => {
    const [cls, fn] = part.split("#");
    return nodeName(cls) + (fn ? `#${fn}` : "");
  }).join(" → ");
}

function siblingsOf(item) {
  return item.parent && items()[item.parent] ? items()[item.parent].children : v().data.roots;
}

function moveButtons(item) {
  const sibs = siblingsOf(item), at = sibs.indexOf(item.id);
  const button = (act, label, title, off) => `<button class="mv" type="button" data-act="${act}" title="${title}" aria-label="${title}" ${off ? "disabled" : ""}>${label}</button>`;
  return button("up", "↑", "Move up", at <= 0) + button("down", "↓", "Move down", at < 0 || at >= sibs.length - 1)
    + button("out", "←", "Move out: make it a sibling of its parent", !item.parent)
    + button("in", "→", "Move in: put it under the one above", at <= 0);
}

function aiMark(item, field) {
  return item.ai_fields.includes(field) ? ' <span class="chip" title="Still an AI draft">AI</span>' : "";
}

function editHtml(item) {
  const parent = items()[item.parent];
  const least = parent ? parent.level + 1 : 1;
  return `<label class="field"><span>Title${aiMark(item, "title")}</span><input class="note-input" id="specTitle" value="${esc(item.title)}"></label>
    <label class="field"><span>Level${aiMark(item, "level")} <span class="small">(empty = parent + 1; at least ${least})</span></span><input class="note-input" id="specLevelSet" type="number" min="${least}" value="${item.level_set ?? ""}" placeholder="auto (${least})"></label>
    <label class="field"><span>Text${aiMark(item, "text")} <span class="small">(Markdown, [[Class]], [[spec:id]] and [[memo:id]] links)</span></span><textarea class="note-input" id="specText" rows="7">${esc(item.text)}</textarea></label>
    <div class="row"><button class="btn small primary" type="button" data-act="save">Save</button><button class="btn small" type="button" data-act="cancel">Cancel</button></div>`;
}

function confirmHtml(item) {
  const kids = item.children.length;
  const to = items()[item.parent]?.title || "the top level";
  return `<div class="spec-confirm" role="alert">Delete “${esc(item.title)}”?${kids ? ` Its ${kids} sub-item${kids > 1 ? "s" : ""} move up to ${esc(to)}.` : ""}
    <div class="row"><button class="btn small danger" type="button" data-act="delete-yes">Delete</button><button class="btn small" type="button" data-act="delete-no">Cancel</button></div></div>`;
}

function prioritySelect(id, label, item, field) {
  return `<label class="mini">${label}${aiMark(item, field)}<select class="select" id="${id}" data-field="${field}" aria-label="${label}">${options(["", ...v().data.priorities], item[field], (x) => x || "—")}</select></label>`;
}

function cardHtml(item, box) {
  const target = selectedTarget();
  const body = v().editing ? editHtml(item) : `<div class="spec-text">${item.text ? wikiLinksHtml(item.text).replace(/\n/g, "<br>") : '<span class="muted small">No text yet.</span>'}</div>`;
  const picks = v().picks.map((c) => `<li><button type="button" data-attach="${esc(c.id)}" title="${esc(c.file)}">${esc(c.name)} <span class="muted small">${esc(c.file)}</span></button></li>`).join("");
  return `<div class="spec-card">
    <div class="row spec-meta">
      ${prioritySelect("specPriority", "Work", item, "priority")}${prioritySelect("specImportance", "Importance", item, "importance")}
    </div>
    <div class="row spec-meta">
      <span class="muted small">L${item.level}${item.level_set ? "" : " (auto)"} · ${esc(stageLabel(item.stage))}${aiMark(item, "stage")} · by ${esc(item.ai ? "AI" : item.by)}</span>
      ${item.ai ? `<button class="btn small" type="button" data-act="accept" title="Make the AI draft fields (${esc(item.ai_fields.join(", "))}) yours">Accept</button>` : ""}
    </div>
    ${item.problems.map((p) => `<div class="spec-problem">${esc(p)}</div>`).join("")}
    ${body}
    <div class="sec">Links${aiMark(item, "links")}</div>
    <div class="spec-classes">${linkChips(item, box)}</div>
    <div class="row"><button class="btn small" type="button" data-act="attach-sel" ${target ? "" : "disabled"} title="${target ? "" : "Click a class, line or function in the graph first"}">+ Link selection${target ? `: ${esc(targetName(target))}` : ""}</button></div>
    <input class="note-input" id="specPicker" type="search" placeholder="Find a class to link…" aria-label="Find a class to link" autocomplete="off">
    ${picks ? `<ul class="spec-picks">${picks}</ul>` : ""}
    <div class="row spec-actions">
      ${v().editing ? "" : '<button class="btn small" type="button" data-act="edit">Edit</button>'}
      <button class="btn small" type="button" data-act="add-sub">+ Sub-${oneWord()}</button>
      ${moveButtons(item)}
      <button class="btn small" type="button" data-act="delete">Delete</button>
    </div>
    ${v().confirming ? confirmHtml(item) : ""}
  </div>`;
}

function renderCrumbs() {
  const crumbs = v().data.crumbs;
  $("specCrumbs").innerHTML = crumbs.length ? `<button type="button" data-crumb="">All</button>`
    + crumbs.map((c, i) => ` › ${i === crumbs.length - 1 ? `<span>${esc(c.title)}</span>` : `<button type="button" data-crumb="${esc(c.id)}">${esc(c.title)}</button>`}`).join("") : "";
}

function hitRow(h, attrs) {
  return `<li><button type="button" ${attrs}>${esc(h.path)} ${stageChip(h.stage, h.stage_label)}${priorityChips(h)}</button></li>`;
}

function renderHits() {
  const hits = v().data.hits;
  $("specHits").hidden = hits == null;
  $("specHits").innerHTML = hits == null ? "" : (hits.map((h) => hitRow(h, `data-hit="${esc(h.id)}"`)).join("")
    || `<li class="muted">No ${oneWord()} matches.</li>`);
}

function renderTools() {
  const t = v(), data = t.data;
  FOLDERS.forEach((f) => tab(f).setAttribute("aria-selected", String(f === sv.folder)));
  $("specAdd").textContent = `+ ${oneWord()[0].toUpperCase()}${oneWord().slice(1)}`;
  $("specStageFilter").innerHTML = `<option value="">All stages</option>${options(data.stages, t.stage, (s) => stageLabel(s))}`;
  $("specPriorityFilter").innerHTML = `<option value="">Work: any</option>${options(data.priorities, t.priority, (x) => `work: ${x}`)}`;
  $("specImportanceFilter").innerHTML = `<option value="">Imp.: any</option>${options(data.priorities, t.importance, (x) => `imp: ${x}`)}`;
  $("specSort").value = t.sort;
  if (document.activeElement !== $("specSearch")) $("specSearch").value = t.query;
  $("specFollow").checked = t.follow;
  $("specHint").innerHTML = `One Markdown file per ${oneWord()} in <code>${data.folder}/</code> of the app's data folder. The AI reads them with <code>coderstanding ${data.folder}</code> and <code>coderstanding ${data.kind}</code>.`;
}

function renderLevel() {
  const max = v().data.max_level;
  const slider = $("specLevel");
  slider.max = String(max);
  slider.value = String(Math.min(shownLevel() ?? max, max));
  slider.disabled = v().follow;
  $("specLevelOut").textContent = shownLevel() == null || shownLevel() >= max ? "All levels" : `Level ${shownLevel()}`;
}

function render() {
  if (!v().data) return;
  const focused = document.activeElement?.id;
  renderTools();
  renderLevel();
  renderCrumbs();
  renderHits();
  $("specTree").innerHTML = v().data.tree.map(boxHtml).join("")
    || `<p class="muted">Nothing yet. Start with ${esc($("specAdd").textContent)}.</p>`;
  if (focused && ["specPicker", "specTitle", "specText"].includes(focused)) $(focused)?.focus();
}

// ---------- the graph: an item's links as the view's focus ----------

function findBox(id, boxes = v().data?.tree || []) {
  for (const box of boxes) {
    if (box.id === id) return box;
    const found = findBox(id, box.children);
    if (found) return found;
  }
  return null;
}

/** The selected item's box focus (own and folded links), else the item's own. */
function graphFocus() {
  const id = v().selected;
  if (!id) return null;
  const focus = findBox(id)?.focus || items()[id]?.focus || [];
  return focus.length ? focus : null;
}

async function showFocus() {
  state.focus = graphFocus();
  state.crumbs = [];
  await loadView();
  fitFocus();
}

async function pick(id) {
  const t = v();
  t.selected = t.selected === id ? null : id;
  t.editing = false;
  t.confirming = false;
  t.picks = [];
  render();
  await showFocus();
}

async function switchTab(folder) {
  if (folder === sv.folder || !FOLDERS.includes(folder)) return;
  sv.folder = folder;
  if (!v().data) await load(); else render();
  await showFocus();
}

/** Select an item from anywhere (a hit, the details panel, a [[spec:id]] link): open the sidebar
 *  on its tab and zoom to its parent when it is folded at this level. */
async function open(id, folder = sv.folder) {
  toggleSide(true);
  if (!sv.started) await start();
  if (folder !== sv.folder) { sv.folder = folder; await load(); }
  const t = v();
  if (!findBox(id) && items()[id]) {
    t.zoom = items()[id].parent && items()[items()[id].parent] ? items()[id].parent : null;
    if (!t.zoom) t.level = null;
    await load();
  }
  t.selected = null;
  await pick(id);
  document.querySelector(`#specTree [data-spec="${CSS.escape(id)}"]`)?.scrollIntoView({ block: "nearest" });
}

// ---------- editing ----------

async function addCard(parent) {
  const t = v();
  const answer = await write(() => sendJson(`/api/${sv.folder}`, "POST", { title: `New ${oneWord()}`, ...(parent ? { parent } : {}) }), "Added");
  if (!answer) return;
  if (parent && !findBox(answer.id)) { t.zoom = parent; await load(); }
  t.selected = answer.id;
  t.editing = true;
  render();
  $("specTitle")?.select();
  await showFocus();
}

async function saveEdit(item) {
  const level = $("specLevelSet").value.trim();
  const answer = await putCard(item.id, { title: $("specTitle").value, text: $("specText").value,
    level: level ? Number(level) : null });
  if (answer) { v().editing = false; render(); await showFocus(); }
}

async function setLinks(item, links) {
  if (await putCard(item.id, { links })) { v().picks = []; render(); await showFocus(); }
}

function move(item, act) {
  const sibs = siblingsOf(item), at = sibs.indexOf(item.id);
  const parent = items()[item.parent];
  const body = {
    up: { position: at - 1 },
    down: { position: at + 1 },
    out: { parent: parent?.parent && items()[parent.parent] ? parent.parent : "", position: parent ? siblingsOf(parent).indexOf(parent.id) + 1 : null },
    in: { parent: sibs[at - 1] },
  }[act];
  return putCard(item.id, body, "Moved");
}

async function remove(item) {
  const answer = await write(() => api(cardPath(item.id), { method: "DELETE" }), "Deleted");
  if (answer) { v().selected = null; v().confirming = false; render(); await showFocus(); }
}

let pickTimer = 0;
function findClasses(query) {
  clearTimeout(pickTimer);
  pickTimer = setTimeout(async () => {
    try {
      v().picks = query.trim() ? (await api(`/api/find?q=${enc(query)}`)).classes.slice(0, 8) : [];
    } catch { v().picks = []; }
    render();
    const box = $("specPicker");
    if (box) { box.value = query; box.focus(); }
  }, 180);
}

// ---------- events ----------

function onTreeClick(event) {
  const el = event.target.closest("button, [data-target]");
  if (!el) return;
  const d = el.dataset;
  if (d.folder) { switchTab(d.folder); return; }
  if (d.pick) { pick(d.pick); return; }
  if (d.zoom) { v().zoom = d.zoom; load(); return; }
  if (d.target) { onLink(d.target); return; }
  const item = items()[v().selected];
  if (!item) return;
  if (d.detach) { setLinks(item, item.links.filter((c) => c !== d.detach)); return; }
  if (d.attach) { setLinks(item, [...item.links, d.attach]); return; }
  const actions = {
    edit: () => { v().editing = true; render(); $("specTitle")?.focus(); },
    cancel: () => { v().editing = false; render(); },
    save: () => saveEdit(item),
    accept: () => write(() => sendJson(cardPath(item.id, "/accept"), "POST", {}), "Accepted"),
    "add-sub": () => addCard(item.id),
    "attach-sel": () => selectedTarget() && setLinks(item, [...item.links, selectedTarget()]),
    delete: () => { v().confirming = true; render(); },
    "delete-no": () => { v().confirming = false; render(); },
    "delete-yes": () => remove(item),
    up: () => move(item, "up"), down: () => move(item, "down"), out: () => move(item, "out"), in: () => move(item, "in"),
  };
  actions[d.act]?.();
}

/** A [[link]] or a link chip: `spec:id` / `memo:id` opens that item, else select it in the graph. */
function onLink(target) {
  const [prefix, rest] = target.split(/:(.*)/s);
  if (rest && ["spec", "memo"].includes(prefix)) { open(rest, `${prefix}s`); return; }
  if (!selectInGraph(target)) message(`${target} is not on the graph at this level`);
}

function onTreeChange(event) {
  const el = event.target;
  if (el.dataset.stageOf) { putCard(el.dataset.stageOf, { stage: el.value }); return; }
  const item = items()[v().selected];
  if (item && el.dataset.field) putCard(item.id, { [el.dataset.field]: el.value || null });
}

function onTreeInput(event) {
  if (event.target.id === "specPicker") findClasses(event.target.value);
}

function onTreeKey(event) {
  if (event.key === "Enter" && event.target.dataset?.target) onLink(event.target.dataset.target);
  if (event.key === "Enter" && event.target.id === "specTitle") saveEdit(items()[v().selected]);
  if (event.key === "Escape" && v().editing) { v().editing = false; render(); }
}

// drag a box onto another box (it becomes its last sub-item) or onto "Top level"
function onDragStart(event) {
  const box = event.target.closest?.("[data-spec]");
  if (!box) return;
  sv.drag = box.dataset.spec;
  event.dataTransfer.effectAllowed = "move";
  event.dataTransfer.setData("text/plain", sv.drag);
  $("specTop").hidden = false;
}

function dropTarget(event) {
  return event.target.closest?.("[data-drop]")?.dataset.drop;
}

function onDragOver(event) {
  const target = dropTarget(event);
  if (!sv.drag || target === undefined || target === sv.drag) return;
  event.preventDefault();
  document.querySelectorAll(".spec-drop").forEach((el) => el.classList.remove("spec-drop"));
  event.target.closest("[data-drop]").classList.add("spec-drop");
}

function onDrop(event) {
  const target = dropTarget(event);
  const id = sv.drag;
  endDrag();
  if (!id || target === undefined || target === id) return;
  event.preventDefault();
  putCard(id, { parent: target, position: null }, "Moved");
}

function endDrag() {
  sv.drag = null;
  $("specTop").hidden = true;
  document.querySelectorAll(".spec-drop").forEach((el) => el.classList.remove("spec-drop"));
}

/** A filter or sort control: keep it on the tab's state and reload. */
function bindFilter(id, name) {
  $(id).addEventListener("change", () => { v()[name] = $(id).value; load(); });
}

function bind() {
  const side = $("sideBody");
  side.addEventListener("click", onTreeClick);
  side.addEventListener("change", onTreeChange);
  side.addEventListener("input", onTreeInput);
  side.addEventListener("keydown", onTreeKey);
  side.addEventListener("dragstart", onDragStart);
  side.addEventListener("dragover", onDragOver);
  side.addEventListener("drop", onDrop);
  side.addEventListener("dragend", endDrag);
  $("specAdd").addEventListener("click", () => { v().zoom = null; addCard(null); });
  let searchTimer = 0;
  $("specSearch").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { v().query = $("specSearch").value.trim(); load(); }, 200);
  });
  bindFilter("specStageFilter", "stage");
  bindFilter("specPriorityFilter", "priority");
  bindFilter("specImportanceFilter", "importance");
  bindFilter("specSort", "sort");
  $("specHits").addEventListener("click", (event) => { const b = event.target.closest("[data-hit]"); if (b) open(b.dataset.hit); });
  $("specCrumbs").addEventListener("click", (event) => {
    const crumb = event.target.closest("[data-crumb]");
    if (crumb) { v().zoom = crumb.dataset.crumb || null; load(); }
  });
  $("specLevel").addEventListener("input", () => { v().level = Number($("specLevel").value); load(); });
  $("specFollow").addEventListener("change", () => { v().follow = $("specFollow").checked; load(); });
  $("level").addEventListener("input", () => { if (v().follow) load(); });
  document.addEventListener("coder:select", () => { if (v().selected && !v().editing) render(); });
}

// ---------- the details panel: the specs and notes of a class or line ----------

const KIND_WORD = { spec: "Spec", memo: "Note" };

/** The "Specs & notes" field of a class's or line's details (fetched once per id, server's
 *  reverse index): each item with its kind, path and stage; clicking opens it in the sidebar. */
function linkedHtml(id) {
  const hits = sv.linked.get(id);
  if (hits === undefined) { fetchLinked(id); return ""; }
  if (hits === "loading" || !hits.length) return "";
  return `<ul class="spec-of">${hits.map((h) => `<li><button class="linkbtn" type="button" data-act="open-card" data-folder="${h.kind}s" data-id="${esc(h.id)}"><span class="muted small">${KIND_WORD[h.kind] || h.kind}</span> ${esc(h.path)}</button> ${stageChip(h.stage, h.stage_label)}${priorityChips(h)}</li>`).join("")}</ul>`;
}

async function fetchLinked(id) {
  sv.linked.set(id, "loading");
  try {
    sv.linked.set(id, (await api(`/api/linked?at=${enc(id)}`)).items || []);
  } catch {
    sv.linked.set(id, []);
    return;
  }
  const showing = $("details").dataset.showing || "";
  if (!sv.linked.get(id).length) return;
  if (showing === `node:${id}`) {
    const node = cy.getElementById(id);
    if (node.nonempty()) renderNode(node.data("raw"));
  } else if (showing === `edge:${id}`) {
    const edge = cy.getElementById(`e:${id}`);
    if (edge.nonempty()) renderEdge(edge.data("raw"));
  }
}

// ---------- start ----------

/** First use: bind and load. `params`: `?specs=1` (or `notes=1`) opens the sidebar on that tab,
 *  `&spec=<id>` / `&note=<id>` selects one. */
async function start(params = null) {
  if (!sv.started) {
    sv.started = true;
    bind();
    if (params?.get("notes") === "1" || params?.get("note")) sv.folder = "memos";
    await load();
  }
  const id = params?.get("spec") || params?.get("note");
  if (id) await open(id);
  else if (params?.get("specs") === "1" || params?.get("notes") === "1") toggleSide(true);
}

return { start, open, graphFocus, linkedHtml, state: sv, reload: load };
})();
