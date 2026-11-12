// Planned work (DESIGN 7f, mock-up docs/mockups/unbuilt.html): planned and added-manually boxes,
// planned function rows and stubs as the view engine sends them (`made`, `stub`, stage...), the
// Planned work switch (Hide / Show / Only planned work), the toolbar count, the "built now?"
// banner, the details panel's Plan block and the Add box form. No graph logic here: the server
// decides what is planned, built or a stub (planned.py). Loaded before app.js; uses its state,
// cy and helpers at call time.
"use strict";

const PlanView = (() => {
const LABELS = { planned: "Planned", manual: "Added manually" };
const NOT_IN_CODE = "not in the code yet";
const later = new Set();  // "built now?" banners put off with Later (this page only)

const priorities = () => state.meta?.plan?.priorities || ["high", "medium", "low"];
const stages = () => state.meta?.plan?.stages || ["idea", "todo", "in-progress", "review", "implemented", "dropped"];

/** The box's tab: "Planned" (pink "Planned · AI draft" until a person accepts it) or "Added manually". */
function badges(node) {
  if (!node.made) return [];
  const draft = node.made_ai ? " · AI draft" : "";
  return [{ flag: "made", text: LABELS[node.made] + (node.made === "planned" ? draft : ""), look: node.made_ai && node.made === "planned" ? "draft" : node.made }];
}

/** Classes on a box face's SVG group: its look (dashed planned, double manual, AI draft pink). */
function faceClass(node) {
  return node.made ? ` made-${node.made}${node.made_ai ? " made-ai" : ""}` : "";
}

/** "todo · work: high · imp: high" of a planned item (its stage marked "(spec)" when it comes from its spec). */
function planWords(item) {
  if (!item.stage) return [];
  return [item.stage + (item.stage_from === "spec" ? " (spec)" : ""),
    item.priority && `work: ${item.priority}`, item.importance && `imp: ${item.importance}`].filter(Boolean);
}

/** A hand-made box's footer: its plan and "not in the code yet", or where an added-manually thing lives. */
function footText(node) {
  if (node.made === "manual") return `where: ${node.where || "unknown"}`;
  return [...planWords(node), NOT_IN_CODE].join(" · ");
}

/** After a function row's name: "  planned" / "  stub" ("" for built code). */
function fnMark(f) {
  return f.made === "planned" ? "  planned" : f.stub ? "  stub" : "";
}

// ---------- the details panel ----------

function chip(text, cls = "") {
  return `<span class="chip ${cls}">${esc(text)}</span>`;
}

function pickHtml(name, label, values, current, empty) {
  const options = (empty ? [""] : []).concat(values).map((v) =>
    `<option value="${esc(v)}"${v === (current || "") ? " selected" : ""}>${esc(v || empty)}</option>`);
  return `<div class="plan-row"><span class="k">${esc(label)}</span><select class="note-input" data-note="${name}" data-orig="${esc(current || "")}" aria-label="${esc(label)}">${options.join("")}</select></div>`;
}

/** Stage, work priority and importance pickers (saved as notes, like every field), and the spec. */
function planPickers(item) {
  const stageNote = item.stage_from === "own" ? item.stage : "";
  const from = item.stage_from === "spec" ? ` <span class="muted">(from its spec: ${esc(item.stage)})</span>` : "";
  return pickHtml("stage", "Stage", stages(), stageNote, item.stage_from === "spec" ? "as its spec" : "idea") + from
    + pickHtml("priority", "Work", priorities(), item.priority, "—")
    + pickHtml("importance", "Importance", priorities(), item.importance, "—")
    + (item.spec ? `<div class="plan-row"><span class="k">Spec</span><button class="linkish" data-act="open-card" data-folder="specs" data-id="${esc(item.spec)}">§ ${esc(item.spec)}</button></div>` : "");
}

/** Buttons that link a planned item to the real code it matches (exact: built now; close: "is it this one?"). */
function matchHtml(target, matches, owner = "") {
  const rows = [...(matches?.exact || []).map((m) => [m, true]), ...(matches?.close || []).map((m) => [m, false])];
  if (!rows.length) return "";
  return `<div class="plan-matches">${rows.map(([m, exact]) => `<div>${exact ? "Built now? The code has" : "Is it"} <code>${esc(owner ? `${m}()` : nodeName(m))}</code>${exact ? "" : "?"}
    <button class="btn small primary" data-act="plan-link" data-from="${esc(target)}" data-to="${esc(owner + m)}">Link to it</button></div>`).join("")}</div>`;
}

function authorLine(item) {
  if (!item.made_by) return "";
  return `<div class="plan-by">Added by ${esc(item.made_by)}${item.made_ai ? " — an AI draft until someone accepts it" : ""}</div>`;
}

/** The top of a planned or added-manually class's details (above its plain words): what it is, and
 *  "built now?" links. */
function nodeHtml(node) {
  if (!node.made) return "";
  const draft = node.made_ai ? chip("AI draft", "ai") : "";
  const where = node.made === "planned" ? NOT_IN_CODE : "real, but the scanner can't see it";
  const head = `<div class="plan-head"><span class="chip made-${node.made}">${LABELS[node.made]}</span>${draft}<span class="plan-where">${where}</span></div>`;
  if (node.made === "manual") {
    return `<section class="plan-box manual">${head}<div class="plan-warn m"><b>Added by hand.</b> It exists, but outside the scanned code: <code>${esc(node.where || "where: unknown")}</code>.</div></section>`;
  }
  return `<section class="plan-box">${head}<div class="plan-warn"><b>Not built yet.</b> There is no file to open; this box is a plan.</div>${matchHtml(node.id, node.matches)}</section>`;
}

/** Under a hand-made class's plain words: its Plan group (stage, work, importance, spec), who
 *  added it, and the actions. */
function planHtml(node) {
  if (!node.made) return "";
  const actions = [node.made_ai && `<button class="btn small primary" data-act="plan-accept">Accept</button>`,
    node.made === "planned" && `<button class="btn small" data-act="plan-add-fn">+ Planned function</button>`,
    node.made === "planned" && node.matches?.exact?.length && `<button class="btn small" data-act="plan-keep">Keep both</button>`,
    `<button class="btn small danger" data-act="plan-drop">Delete</button>`].filter(Boolean).join("");
  const plan = node.made === "planned" ? `<div class="plan-grp">Plan</div><div class="plan-group">${planPickers(node)}</div>` : "";
  return `<section class="plan-box">${plan}${authorLine(node)}<div class="actions">${actions}</div><div class="plan-confirm" hidden></div></section>`;
}

/** The top of a function's details when it is planned or a stub. */
function functionHtml(fn) {
  if (fn.made === "planned") {
    return `<section class="plan-box"><div class="plan-head"><span class="chip made-planned">Planned function</span>${fn.made_ai ? chip("AI draft", "ai") : ""}</div>
      <div class="plan-warn">Not in the code yet.</div>${matchHtml(fn.id, fn.matches, `${fn.cls}#`)}
      <div class="plan-group">${planPickers(fn)}</div>${authorLine(fn)}
      <div class="actions">${fn.made_ai ? `<button class="btn small primary" data-act="plan-accept">Accept</button>` : ""}<button class="btn small danger" data-act="plan-drop">Delete</button></div><div class="plan-confirm" hidden></div></section>`;
  }
  if (fn.stub) {
    return `<section class="plan-box stub"><div class="plan-head"><span class="chip stub">Stub</span></div>
      <div class="plan-warn s">It has no real body yet (empty, <code>pass</code>, a TODO, or "not implemented"), so it counts as not built.</div></section>`;
  }
  return "";
}

// ---------- writes ----------

async function post(path, body) {
  return api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

async function write(path, body, done) {
  try {
    await post(path, body);
  } catch (error) {
    flashNote(error.message);
    return;
  }
  if (done) done();
  await loadView({ inPlace: true });
}

function keyOf(item) {
  return `node:${item.raw.id}`;
}

/** A details-panel action (data-act="plan-…"). */
function act(name, button, item) {
  if (name === "plan-link") return write("/api/plan/link", { from: button.dataset.from, to: button.dataset.to }, () => select(null));
  if (name === "plan-add-fn") return openForm({ on: item.raw.id });
  if (name === "plan-form-kind") return setFormKind(button.dataset.kind);
  if (name === "plan-form-cancel") return closeForm();
  if (name === "plan-form-anyway") return submitForm(true);
  if (name === "plan-form-real") { closeForm(); return showReal(button.dataset.id); }
  if (name === "plan-banner-later") { later.add(button.dataset.id); return afterDraw(state.current); }
  if (!item) return undefined;
  if (name === "plan-accept") return write("/api/plan/accept", { key: keyOf(item) });
  if (name === "plan-keep") return write("/api/plan/keep", { id: item.raw.id });
  if (name === "plan-drop") return confirmDrop(button, item);
  if (name === "plan-drop-yes") return write("/api/plan/drop", { key: keyOf(item) }, () => select(null));
  if (name === "plan-drop-no") { button.closest(".plan-confirm").hidden = true; return undefined; }
  return undefined;
}

function confirmDrop(button, item) {
  const box = button.closest(".plan-box")?.querySelector(".plan-confirm");
  if (!box) return;
  const what = item.type === "fn" ? "this planned function" : "this box, its planned functions and its lines";
  box.innerHTML = `Delete ${what}? <button class="btn small danger" data-act="plan-drop-yes">Delete</button> <button class="btn small" data-act="plan-drop-no">Cancel</button>`;
  box.hidden = false;
}

function showReal(id) {
  const node = cy.getElementById(id);
  if (node.nonempty()) select({ type: "node", raw: node.data("raw") });
  else flashNote(`${id} is not on the map at this level: find it with the search box.`);
}

// ---------- the Add box form (Edit mode; a class's "+ Planned function") ----------

let form = null;  // {on, kind}

function classOptions(selected = "") {
  const nodes = (state.current?.nodes || []).filter((n) => n.kind !== "domain").sort((a, b) => nodeName(a.id).localeCompare(nodeName(b.id)));
  return `<option value="">none</option>` + nodes.map((n) => `<option value="${esc(n.id)}"${n.id === selected ? " selected" : ""}>${esc(nodeName(n.id))}</option>`).join("");
}

function formHtml() {
  const fn = Boolean(form.on);
  const kindSeg = fn ? "" : `<label>Kind</label><div class="seg small" role="group" aria-label="Kind">
      <button type="button" data-act="plan-form-kind" data-kind="planned" aria-pressed="${form.kind === "planned"}">Planned</button>
      <button type="button" data-act="plan-form-kind" data-kind="manual" aria-pressed="${form.kind === "manual"}">Added manually</button></div>
    <span class="help">${form.kind === "planned" ? "Something we intend to build. It is not in the code yet." : "Real, but the scanner can't see it: loaded by name, generated code, an outside service."}</span>`;
  const planned = form.kind === "planned";
  const pick = (id, label, values, empty) => `<select id="${id}" aria-label="${label}"><option value="">${empty}</option>${values.map((v) => `<option>${esc(v)}</option>`).join("")}</select>`;
  return `<form class="plan-form" id="planForm" autocomplete="off">
    <h3>${fn ? `Planned function on ${esc(nodeName(form.on))}` : "Add box"}</h3>
    <div class="plan-grid">${kindSeg}
      <label for="planName">Name</label><input id="planName" required>
      <span class="help">Use the name the code will have, so the scan can match it when it is built.</span>
      <label for="planHint">What it's for</label><textarea id="planHint" rows="2"></textarea>
      <label for="planTags">Tags</label><input id="planTags" placeholder="planning, map (comma separated)">
      ${planned ? "" : `<label for="planWhere">Where it lives</label><input id="planWhere" placeholder="file, folder or service; empty = unknown">`}
      ${planned ? `<label>Plan</label><div class="plan-inl">${pick("planStage", "Stage", stages(), "stage: idea")}${pick("planWork", "Work priority", priorities(), "work: —")}${pick("planImp", "Importance", priorities(), "imp: —")}</div>
      <label for="planSpec">Spec</label><input id="planSpec" placeholder="spec id (optional)">` : ""}
      ${fn ? "" : `<label for="planFrom">Line from</label><select id="planFrom">${classOptions()}</select>
      <label for="planTo">Line to</label><select id="planTo">${classOptions()}</select>`}
    </div>
    <div class="plan-ask" id="planAsk" hidden></div>
    <div class="actions"><button class="btn primary" type="submit">Add</button><button class="btn" type="button" data-act="plan-form-cancel">Cancel</button></div>
  </form>`;
}

function renderForm() {
  $("details").innerHTML = formHtml();
  $("planForm").addEventListener("submit", (event) => { event.preventDefault(); submitForm(false); });
}

function openForm({ on = null } = {}) {
  form = { on, kind: "planned" };
  $("details").dataset.planForm = "1";
  renderForm();
  $("planName").focus();
}

/** Planned / Added manually in the form: the fields change, what was typed stays. */
function setFormKind(kind) {
  const typed = { planName: value("planName"), planHint: value("planHint"), planTags: value("planTags") };
  form.kind = kind;
  renderForm();
  for (const [id, text] of Object.entries(typed)) $(id).value = text;
}

function closeForm() {
  form = null;
  delete $("details").dataset.planForm;
  renderSelected();
}

function value(id) {
  return ($(id)?.value || "").trim();
}

function formBody(anyway) {
  const name = value("planName");
  const fields = { hint: value("planHint"), tags: value("planTags"), where: value("planWhere"), stage: value("planStage"),
    priority: value("planWork"), importance: value("planImp"), spec: value("planSpec") };
  const links = [[value("planFrom"), name], [name, value("planTo")]].filter(([a, b]) => a && b);
  return { name, on: form.on, made: form.kind, anyway,
    fields: Object.fromEntries(Object.entries(fields).filter(([, v]) => v)), links };
}

/** Add it; when its name matches real code, ask first (the real one, or add anyway). */
async function submitForm(anyway) {
  const body = formBody(anyway);
  if (!body.name) { $("planName").focus(); return; }
  let result;
  try {
    result = await post("/api/plan", body);
  } catch (error) {
    showAsk(`<span class="note-error">${esc(error.message)}</span>`);
    return;
  }
  if (result.ask) {
    const owner = form.on ? `${nodeName(form.on)}#` : "";
    const found = [...result.ask.exact, ...result.ask.close];
    const real = found.map((id) => form.on ? `<code>${esc(owner + id)}()</code>`
      : `<button class="linkish" type="button" data-act="plan-form-real" data-id="${esc(id)}">${esc(nodeName(id))}</button>`).join(", ");
    showAsk(`<b>${esc(body.name)}</b> ${result.ask.exact.length ? "is" : "looks like"} real code: ${real}. Put your notes on the real one instead, or add it anyway.
      <div class="actions"><button class="btn small" type="button" data-act="plan-form-anyway">Add anyway</button></div>`);
    return;
  }
  closeForm();
  await loadView({ inPlace: true });
  flashNote(`Added ${body.name}`);
}

function showAsk(html) {
  const box = $("planAsk");
  box.innerHTML = html;
  box.hidden = false;
}

// ---------- the switch, the count, the banner, Only planned work ----------

function renderControls() {
  if ($("planCtl")) $("planCtl").value = state.show.planned;
}

function countText(plan) {
  if (!plan) return "";
  const parts = [plan.planned && `${plan.planned} planned`, plan.functions && `${plan.functions} planned function${plan.functions === 1 ? "" : "s"}`,
    plan.manual && `${plan.manual} manual`, plan.stubs && `${plan.stubs} stub${plan.stubs === 1 ? "" : "s"}`].filter(Boolean);
  return parts.join(" · ");
}

function renderCount(view) {
  const button = $("planCount");
  if (!button) return;
  const text = countText(view?.plan);
  button.hidden = !text;
  button.textContent = text;
  button.setAttribute("aria-pressed", String(state.show.planned === "only"));
}

/** The "built now?" banner: a planned box whose name is real code now (first one not put off). */
function renderBanner(view) {
  const box = $("planBanner");
  if (!box) return;
  const node = (view?.nodes || []).find((n) => n.made === "planned" && n.matches?.exact?.length && !later.has(n.id));
  box.hidden = !node;
  if (!node) return;
  const real = node.matches.exact[0];
  box.innerHTML = `<span class="t"><b>${esc(node.name)} is built now</b>: the scan found <code>${esc(real)}</code>. Move its notes and lines to the real class?</span>
    <button class="btn small primary" data-act="plan-link" data-from="${esc(node.id)}" data-to="${esc(real)}">Merge</button>
    <button class="btn small" data-act="plan-keep" data-id="${esc(node.id)}">Keep both</button>
    <button class="btn small" data-act="plan-banner-later" data-id="${esc(node.id)}">Later</button>`;
}

/** Only planned work: everything else fades (the planned boxes, rows and their lines stay full). */
function applyOnly() {
  const only = state.show.planned === "only";
  $("app")?.classList.toggle("plan-only", only);
  if (!cy) return;
  cy.batch(() => {
    cy.elements().removeClass("plan-dim");
    if (!only) return;
    cy.nodes().filter((n) => n.data("raw")?.made !== "planned").addClass("plan-dim");
    cy.edges().filter((e) => !e.data("raw")?.kinds?.includes("planned")).addClass("plan-dim");
  });
}

function afterDraw(view) {
  renderCount(view);
  renderBanner(view);
  applyOnly();
}

function onBannerClick(event) {
  const button = event.target.closest("[data-act]");
  if (!button) return;
  if (button.dataset.act === "plan-keep") write("/api/plan/keep", { id: button.dataset.id });
  else act(button.dataset.act, button, null);
}

function bind() {
  $("planCtl")?.addEventListener("change", (event) => setOption("planned", event.target.value));
  $("planCount")?.addEventListener("click", () => setOption("planned", state.show.planned === "only" ? "show" : "only"));
  $("planBanner")?.addEventListener("click", onBannerClick);
  $("addBoxBtn")?.addEventListener("click", () => openForm());
}

return { badges, faceClass, footText, fnMark, planWords, nodeHtml, planHtml, functionHtml, act, renderControls, afterDraw, bind, openForm };
})();
