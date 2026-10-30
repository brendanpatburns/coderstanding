// Comments (DESIGN 7d): threads on a tour and its steps, shown in the Tours tab. tour_view.js
// leaves a `#tourComments` box in the step panel and calls `CoderComments.update(tv)` after each
// render; this module fills the box (the step's threads, the whole tour's list), adds the "+" to
// the code pane's line gutter and puts open counts on the progress dots, the step list and the
// tours list. The pure part (threads, counts, Markdown) runs under `node --test` too.
"use strict";

(() => {
const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

/** [{root, replies}] in the server's (thread, time) order. */
function threadsOf(list) {
  const out = [], byId = new Map();
  for (const c of list) {
    if (c.id === c.thread) { const t = { root: c, replies: [] }; byId.set(c.id, t); out.push(t); }
    else byId.get(c.thread)?.replies.push(c);
  }
  return out;
}

const isOpen = (t) => !t.root.resolved;

/** {target: open threads} (a thread counts on its first comment's target). */
function openCounts(list) {
  const counts = {};
  for (const t of threadsOf(list)) if (isOpen(t)) counts[t.root.target] = (counts[t.root.target] || 0) + 1;
  return counts;
}

const stepTarget = (tourId, key) => `tour:${tourId}#${key}`;

/** {line: open threads} of one target's line comments (today's line numbers). */
function lineCounts(list, target) {
  const counts = {};
  for (const t of threadsOf(list)) {
    if (isOpen(t) && t.root.target === target && t.root.line) counts[t.root.line] = (counts[t.root.line] || 0) + 1;
  }
  return counts;
}

/** A small, safe Markdown: paragraphs, line breaks, `code`, **bold**, *italic*, [[links]]. */
function mdHtml(text) {
  return esc(text).split(/\n{2,}/).map((para) => `<p>${para
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<i>$2</i>")
    .replace(/\[\[([^\]\n]+)\]\]/g, (_, t) => `<span class="wl" data-target="${t}" role="link" tabindex="0" title="Select ${t}">${t}</span>`)
    .replace(/\n/g, "<br>")}</p>`).join("");
}

const pure = { threadsOf, openCounts, stepTarget, lineCounts, mdHtml };
if (typeof module !== "undefined") { module.exports = pure; return; }

// ---------- the page part ----------

const cs = {
  tourId: null,     // whose comments `list` holds
  list: [],         // GET /api/comments?target=tour:<id>
  me: "",           // the name the helper writes under: own comments can be edited and deleted
  view: null,       // tour_view's state (tour, cur, list)
  all: false,       // the whole tour's threads shown
  line: null,       // the new comment is about this code line
  reply: null,      // thread id with an open reply box
  edit: null,       // comment id being edited
  drafts: {},       // unsent texts, kept across re-renders
  bound: false,
};

const tour = () => (cs.view?.tour && !cs.view.tour.isNew ? cs.view.tour : null);
const curStep = () => tour()?.steps?.[cs.view.cur] || null;
const curTarget = () => (curStep() ? stepTarget(tour().id, curStep().key) : null);
const send = (path, method, body) => api(path, body === undefined ? { method }
  : { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

async function load(id) {
  cs.tourId = id;
  cs.list = [];
  try {
    const got = await api(`/api/comments?target=${encodeURIComponent(`tour:${id}`)}`);
    if (cs.tourId !== id) return;
    cs.list = got.comments;
    cs.me = got.me;
  } catch (error) {
    note(`Could not load comments: ${error.message}`);
  }
  draw();
}

/** tour_view.js calls this after every render. */
function update(view) {
  cs.view = view;
  if (!cs.bound) bind();
  const id = tour()?.id || null;
  if (id !== cs.tourId) {
    Object.assign(cs, { line: null, reply: null, edit: null, all: false });
    if (id) load(id); else { cs.tourId = null; cs.list = []; }
  }
  draw();
}

function draw() {
  drawPanel();
  drawGutter();
  drawBadges();
}

// ---------- rendering ----------

function when(c) {
  const d = new Date(c.created);
  const text = Number.isNaN(+d) ? c.created : d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  return `${esc(text)}${c.edited ? " · edited" : ""}`;
}

function commentHtml(c) {
  const mine = c.by === cs.me && !c.deleted;
  const head = `<div class="chead"><b>${esc(c.by)}</b>${c.ai ? '<span class="chip" title="Written by an AI">AI</span>' : ""}<span class="muted small">${when(c)}</span>
    ${mine ? `<span class="cact"><button type="button" data-cact="edit" data-cid="${c.id}">Edit</button><button type="button" data-cact="delete" data-cid="${c.id}">Delete</button></span>` : ""}</div>`;
  if (c.deleted) return `<div class="cmt" data-cid="${c.id}"><div class="chead muted small">Deleted comment</div></div>`;
  if (cs.edit === c.id) {
    return `<div class="cmt" data-cid="${c.id}">${head}${formHtml(`edit:${c.id}`, c.text, "Save", "Edit your comment")}</div>`;
  }
  return `<div class="cmt" data-cid="${c.id}">${head}<div class="cbody">${mdHtml(c.text)}</div></div>`;
}

function whereHtml(root, withStep) {
  const bits = [];
  if (withStep) {
    const key = root.target.split("#")[1];
    const i = key ? tour().steps.findIndex((s) => s.key === key) : -1;
    bits.push(!key ? '<span class="muted small">Whole tour</span>'
      : i < 0 ? '<span class="chip stale" title="This step was deleted; the comment is kept">Lost step</span>'
        : `<button class="go-step" type="button" data-go="${i}" title="Go to step ${i + 1}">Step ${i + 1}: ${esc(tour().steps[i].title)}</button>`);
  }
  if (root.anchor) {
    bits.push(root.line ? `<span class="chip gen" title="${esc(root.path)}:${root.line}">Line ${root.line}</span>`
      : `<span class="chip stale" title="${esc(root.anchor.at)} is no longer in the code">Line gone</span>`);
  }
  if (root.quote) bits.push(`<q class="small">${esc(root.quote)}</q>`);
  return bits.length ? `<div class="cwhere">${bits.join("")}</div>` : "";
}

function threadHtml(t, withStep = false) {
  const { root } = t;
  const body = `${whereHtml(root, withStep)}${commentHtml(root)}${t.replies.map(commentHtml).join("")}
    ${cs.reply === root.id ? formHtml(`reply:${root.id}`, "", "Reply", "Reply…") : `<div class="cfoot">
      <button class="btn small" type="button" data-cact="reply" data-cid="${root.id}">Reply</button>
      <button class="btn small" type="button" data-cact="${isOpen(t) ? "resolve" : "reopen"}" data-cid="${root.id}">${isOpen(t) ? "Resolve" : "Reopen"}</button></div>`}`;
  if (isOpen(t)) return `<div class="cthread" data-thread="${root.id}">${body}</div>`;
  const first = (root.text || "").split("\n")[0].slice(0, 60);
  return `<details class="cthread resolved" data-thread="${root.id}"><summary>Resolved by ${esc(root.resolved.by)} · ${esc(root.by)}: ${esc(first)}</summary>${body}</details>`;
}

function formHtml(key, text, label, placeholder) {
  const value = cs.drafts[key] ?? text;
  const cancel = key === "new" ? "" : `<button class="btn small" type="button" data-cact="cancel" data-ckey="${key}">Cancel</button>`;
  return `<div class="cform" data-ckey="${key}"><textarea class="note-input" data-cdraft="${key}" rows="3" placeholder="${esc(placeholder)}" aria-label="${esc(placeholder)}">${esc(value)}</textarea>
    <div class="row"><button class="btn small primary" type="button" data-cact="send" data-ckey="${key}">${label}</button>${cancel}<span class="muted small">Markdown and [[links]] work.</span></div></div>`;
}

function drawPanel() {
  const box = document.getElementById("tourComments");
  if (!box || !tour()) return;
  const target = curTarget(), all = threadsOf(cs.list);
  const mine = all.filter((t) => t.root.target === target);
  const open = all.filter(isOpen).length, stepOpen = mine.filter(isOpen).length;
  const toggle = all.length ? `<button class="btn small ${cs.all ? "on" : ""}" type="button" data-cact="all" title="Every comment on this tour">All on this tour (${open} open)</button>` : "";
  const shown = cs.all ? all : mine;
  const list = shown.map((t) => threadHtml(t, cs.all)).join("")
    || `<p class="muted small">${cs.all ? "No comments on this tour yet." : "No comments on this step yet."}</p>`;
  const line = cs.line ? `<div class="row cline"><span class="chip gen">Line ${cs.line}</span><button class="btn small" type="button" data-cact="noline" title="Comment on the whole step">Not on a line</button></div>` : "";
  const focused = document.activeElement?.dataset?.cdraft;
  box.innerHTML = `<div class="sec">Comments${stepOpen ? ` · ${stepOpen} open` : ""}</div>
    <div class="row">${toggle}</div>
    <div class="clist">${list}</div>
    ${line}${formHtml("new", "", "Comment", cs.line ? `Comment on line ${cs.line}…` : "Comment on this step…")}`;
  if (focused) box.querySelector(`[data-cdraft="${focused}"]`)?.focus();
}

function drawGutter() {
  const src = document.getElementById("codeSrc");
  if (!src || !tour() || !curStep()?.code?.from) return;
  const counts = lineCounts(cs.list, curTarget());
  for (const ln of src.querySelectorAll(".ln")) {
    const no = Number(ln.querySelector(".no")?.textContent);
    if (!no) continue;
    ln.querySelector(".cadd")?.remove();
    ln.querySelector(".cmark")?.remove();
    ln.insertAdjacentHTML("afterbegin", `<button class="cadd" type="button" data-cact="line" data-line="${no}" title="Comment on line ${no}" aria-label="Comment on line ${no}">+</button>`);
    if (counts[no]) ln.insertAdjacentHTML("beforeend", `<span class="cmark" title="${counts[no]} open comment${counts[no] > 1 ? "s" : ""} on this line">${counts[no]}</span>`);
    ln.classList.toggle("has-c", Boolean(counts[no]));
  }
}

const badge = (n, title) => (n ? `<span class="cbadge" title="${n} open comment${n > 1 ? "s" : ""}${title}">${n}</span>` : "");

function drawBadges() {
  const t = tour();
  const counts = t && cs.tourId === t.id ? openCounts(cs.list) : {};
  const ofStep = (i) => (t?.steps[i] ? counts[stepTarget(t.id, t.steps[i].key)] || 0 : 0);
  for (const sel of ["#tourDots [data-go]", "#tourPanel .steplist [data-go]"]) {
    for (const b of document.querySelectorAll(sel)) {
      b.querySelector(".cbadge")?.remove();
      b.insertAdjacentHTML("beforeend", badge(ofStep(Number(b.dataset.go)), ""));
    }
  }
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const row = cs.view?.list?.find((r) => r.id === t?.id);
  if (row && cs.tourId === t.id) row.comments = total;  // the list follows this tab's writes
  for (const b of document.querySelectorAll("#tourList [data-tour]")) {
    b.querySelector(".cbadge")?.remove();
    const n = cs.view?.list?.find((r) => r.id === b.dataset.tour)?.comments || 0;
    b.querySelector(".m")?.insertAdjacentHTML("beforeend", badge(n, " on this tour"));
  }
  const head = document.querySelector("#tourPanel .tourhead .row");
  if (head && t) {
    head.querySelector(".cbadge")?.remove();
    head.querySelector(".muted")?.insertAdjacentHTML("afterend", badge(total, " on this tour"));
  }
}

// ---------- writes ----------

function note(text) {
  const el = document.getElementById("tourToast");
  if (el) el.textContent = text;
}

async function write(action) {
  try {
    const got = await action();
    cs.list = got.comments;
    cs.me = got.me;
    return true;
  } catch (error) {
    note(`Comment not saved: ${error.message}`);
    return false;
  } finally {
    draw();
  }
}

async function sendForm(key) {
  const text = (cs.drafts[key] ?? "").trim();
  if (!text) return;
  const [kind, id] = key.split(":");
  const ok = await write(() => kind === "new"
    ? send("/api/comments", "POST", { target: curTarget(), text, ...(cs.line ? { line: cs.line } : {}) })
    : kind === "reply" ? send("/api/comments", "POST", { parent: id, text })
      : send(`/api/comments/${id}`, "PATCH", { text }));
  if (!ok) return;
  delete cs.drafts[key];
  if (kind === "new") cs.line = null;
  if (kind === "reply") cs.reply = null;
  if (kind === "edit") cs.edit = null;
  draw();
}

function onClick(event) {
  const el = event.target.closest("[data-cact]");
  if (!el) return;
  const { cact, cid, ckey } = el.dataset;
  const actions = {
    all: () => { cs.all = !cs.all; draw(); },
    line: () => { cs.line = Number(el.dataset.line); draw(); document.querySelector('[data-cdraft="new"]')?.focus(); },
    noline: () => { cs.line = null; draw(); },
    reply: () => { cs.reply = cid; draw(); document.querySelector(`[data-cdraft="reply:${cid}"]`)?.focus(); },
    edit: () => { cs.edit = cid; draw(); },
    cancel: () => { delete cs.drafts[ckey]; if (ckey.startsWith("reply")) cs.reply = null; else cs.edit = null; draw(); },
    send: () => sendForm(ckey),
    resolve: () => write(() => send(`/api/comments/${cid}`, "PATCH", { resolved: true })),
    reopen: () => write(() => send(`/api/comments/${cid}`, "PATCH", { resolved: false })),
    delete: () => { if (confirm("Delete this comment?")) write(() => send(`/api/comments/${cid}`, "DELETE")); },
  };
  actions[cact]?.();
}

function bind() {
  cs.bound = true;
  for (const id of ["tourPanel", "tourCode"]) document.getElementById(id)?.addEventListener("click", onClick);
  document.getElementById("tourPanel")?.addEventListener("input", (event) => {
    const key = event.target.dataset?.cdraft;
    if (key) cs.drafts[key] = event.target.value;
  });
  document.getElementById("tourPanel")?.addEventListener("keydown", (event) => {
    const key = event.target.dataset?.cdraft;
    if (key && event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); sendForm(key); }
  });
}

window.CoderComments = { ...pure, update, state: cs };
})();
