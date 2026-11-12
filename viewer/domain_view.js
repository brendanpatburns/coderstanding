// Domain frames and domain focus in the viewer (DESIGN 6b, approved mock-up
// docs/mockups/domain-blocks.html). The Domains toggle sorts the drawn classes into one
// frame per domain (geometry and line grouping: domains.js); this file moves the boxes, draws the
// frames, bundles, fold cards and merged lines in two SVG layers around the graph (domBack under
// it, domFront over its lines), and handles fold, drag, hover and the 400 ms tween. Double-clicking
// a frame shows just that domain (the server's `domain` view, laid out here). Loaded before app.js
// and uses its state, cy and helpers.
"use strict";

const DOM = { ms: 400, foldMs: 320, bundleMs: 250, clickWait: 260, dragStart: 4, fitPad: 40,
  maxFitZoom: 1, cardTip: 26, hotLines: 12, cardHint: "double-click to show only it", descFont: "12px" };
const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

const dom = {
  frames: [],            // arranged frames (CoderDomains.arrangeFrames), x/y = layout spot
  byName: new Map(),
  frameOf: new Map(),    // class id -> frame name
  folded: new Set(),     // frame names folded into a card (the wanted state; `anim` tweens it)
  offsets: new Map(),    // frame name -> {dx, dy}: drags, never saved
  stack: [],             // frame names in draw order: the last one dragged is on top
  graphSpots: new Map(), // id -> where the box is outside the frames (tween start or end)
  anim: { mode: 0, bund: 1 },  // tweened: mode (0 graph .. 1 frames), bund, fold:<name>
  tweens: [],
  hover: null,           // bundle key under the mouse
  drag: null,            // {name, x, y, moved}
  clickTimer: 0,
  dragMoved: false,      // the pointer just ended a drag: its click is not a fold
};

// ---------- switches (view options: the `domains` and `between` rows of view_options.js) ----------

function domainsOn() {
  return Boolean(state.meta?.domains) && state.domainView.includes("on");
}

function everyLine() {
  return state.domainView.includes("every");
}

/** Frames are drawn at a class level (>= 1) of the whole app or a zoom; not at level 0, in a
 * domain focus or a tour step's view. */
function framesActive() {
  const view = state.current;
  return domainsOn() && Boolean(view) && view.level >= 1 && !view.domain && !state.focus;
}

function renderDomainControls() {
  const has = Boolean(state.meta?.domains);
  const box = $("showDomains");
  box.disabled = !has;
  box.checked = domainsOn();
  box.closest("label").classList.toggle("off", !has);
  box.closest("label").title = has ? "Sort the classes into one frame per domain (not at level 0)"
    : "Arrives when domains exist";
  $("betweenCtl").classList.toggle("off", !domainsOn());
  $("bundledBtn").setAttribute("aria-pressed", String(!everyLine()));
  $("everyBtn").setAttribute("aria-pressed", String(everyLine()));
  document.querySelectorAll(".dom-legend").forEach((el) => { el.hidden = !domainsOn(); });
}

function setDomains(on) {
  setOption("domains", on);
}

function setEveryLine(every) {
  setOption("between", every ? "every" : "bundled");
}

/** The Domains view option changed without a new view: frames glide in or out. */
function domainsChanged() {
  if (framesActive()) showFrames();
  else if (dom.frames.length) hideFrames();
}

/** Between frames changed: bundled lines fan out into every line, or back. */
function betweenChanged() {
  syncDomainClasses();
  tweenTo({ bund: everyLine() ? 0 : 1 }, DOM.bundleMs);
}

// ---------- arranging ----------

/** The drawn classes for a layout that stays put when a class opens: closed sizes plus the open extras. */
function frameInput() {
  return cy.nodes().map((n) => {
    const raw = n.data("raw");
    return { id: n.id(), ...closedSize(n), level: raw.level, kind: raw.kind, flags: raw.flags, domain: raw.domain };
  });
}

function lineInput() {
  return cy.edges().filter((e) => !e.hasClass("fanned")).map((e) => e.data("raw"));
}

/** Every class line, also those an open class draws as fan-out lines: layouts use these, so
 * opening a class changes nothing they decide. */
function layoutLines() {
  return cy.edges().map((e) => e.data("raw"));
}

function titleWidth(name) {
  return textWidth(name, `700 13.5px ${token("f-display")}`) + 130;  // triangle, gaps, "N classes"
}

function canvasAspect() {
  const box = $("canvas").getBoundingClientRect();
  return box.width / Math.max(1, box.height);
}

/** Sort the drawn boxes into frames. `keep`: same view redrawn, so drags and folds stay. */
function arrange(keep) {
  const before = new Set(dom.byName.keys());
  dom.frames = CoderDomains.arrangeFrames(frameInput(), layoutLines().map((e) => ({ from: e.from, to: e.to, kinds: e.kinds })),
    { aspect: canvasAspect(), titleWidth, described: (name) => Boolean(domainText(name)) });
  dom.byName = new Map(dom.frames.map((b) => [b.name, b]));
  dom.frameOf = new Map(dom.frames.flatMap((b) => b.members.map((id) => [id, b.name])));
  const same = keep && dom.frames.every((b) => before.has(b.name));
  if (!same) dom.offsets.clear();
  dom.folded = new Set([...dom.folded].filter((name) => dom.byName.has(name)));
  dom.stack = dom.frames.map((b) => b.name);
  for (const key of Object.keys(dom.anim)) if (key.startsWith("fold:")) delete dom.anim[key];
  dom.folded.forEach((name) => { dom.anim[`fold:${name}`] = 1; });
}

/** placeNodes' hook: with frames on, every box goes into its frame at once (no tween). */
function placeDomains(keep) {
  stopTweens();
  if (state.current?.domain && !state.focus) { placeFocus(); return; }
  if (!framesActive()) { clearFrames(); return; }
  dom.graphSpots = new Map(cy.nodes().map((n) => [n.id(), { ...n.position() }]));
  arrange(keep);
  dom.anim.mode = 1;
  dom.anim.bund = everyLine() ? 0 : 1;
  placeMembers();
  syncDomainClasses();
}

function clearFrames() {
  dom.frames = [];
  dom.byName.clear();
  dom.frameOf.clear();
  dom.anim.mode = 0;
  cy.nodes().forEach((n) => { n.removeScratch("_domAlpha"); n.removeStyle("opacity"); });
  syncDomainClasses();
}

/** Tick Domains: boxes glide from their graph spots into the frames; the camera follows. */
function showFrames() {
  stopTweens();  // a half-done untick must not clear the new frames when it ends
  dom.graphSpots = new Map(cy.nodes().map((n) => [n.id(), { ...n.position() }]));
  dom.folded.clear();
  arrange(false);
  dom.anim.mode = 0;
  dom.anim.bund = everyLine() ? 0 : 1;
  syncDomainClasses();
  applyGrab();
  fitBox(framesBox(), true);
  tweenTo({ mode: 1 }, DOM.ms, routeEdges);
}

/** Untick: every frame opens, boxes glide back to their graph spots (View moves snap back). */
function hideFrames() {
  stopTweens();
  dom.graphSpots = new Map(cy.nodes().map((n) => [n.id(), grownSpot(n.id(), spotOf(n.id())) || { ...n.position() }]));
  const targets = { mode: 0 };
  dom.folded.forEach((name) => { targets[`fold:${name}`] = 0; });
  dom.folded.clear();
  cy.nodes().removeClass("dom-gone");
  cy.elements().removeClass("dom-hide dom-dim");
  fitBox(boxAround([...dom.graphSpots].map(([id, p]) => rectAt(id, p))), true);
  tweenTo(targets, DOM.ms, () => { clearFrames(); applyGrab(); routeEdges(); });
}

// ---------- geometry ----------

const lerp = (a, b, t) => a + (b - a) * t;
const lerpRect = (a, b, t) => ({ x: lerp(a.x, b.x, t), y: lerp(a.y, b.y, t), w: lerp(a.w, b.w, t), h: lerp(a.h, b.h, t) });
const foldOf = (name) => dom.anim[`fold:${name}`] || 0;
const offsetOf = (name) => dom.offsets.get(name) || { dx: 0, dy: 0 };
const isFolded = (name) => dom.folded.has(name);

function openFrameRect(b) {
  const o = offsetOf(b.name);
  return { x: b.x + o.dx, y: b.y + o.dy, w: b.w, h: b.h };
}

/** Where a frame is drawn now: its frame, shrinking into a card as it folds. */
function frameRect(b) {
  const frame = openFrameRect(b);
  const card = CoderDomains.FRAME.card;
  return lerpRect(frame, { x: frame.x, y: frame.y, w: card.w, h: card.h }, foldOf(b.name));
}

function rectAt(id, p) {
  const n = cy.getElementById(id);
  return { x: p.x - n.data("w") / 2, y: p.y - n.data("h") / 2, w: n.data("w"), h: n.data("h") };
}

function boxAround(rects) {
  if (!rects.length) return null;
  const x1 = Math.min(...rects.map((r) => r.x)), y1 = Math.min(...rects.map((r) => r.y));
  return { x: x1, y: y1, w: Math.max(...rects.map((r) => r.x + r.w)) - x1, h: Math.max(...rects.map((r) => r.y + r.h)) - y1 };
}

function framesBox() {
  const card = CoderDomains.FRAME.card;
  return boxAround(dom.frames.map((b) => (isFolded(b.name) ? { ...openFrameRect(b), ...card } : openFrameRect(b))));
}

/** Put the camera on a model box (at most 1:1), animated unless reduced motion asks. */
function fitBox(box, animate) {
  if (!box) return;
  const { width, height } = $("canvas").getBoundingClientRect();
  const zoom = Math.min(DOM.maxFitZoom, (width - 2 * DOM.fitPad) / box.w, (height - 2 * DOM.fitPad) / box.h);
  const z = Math.max(cy.minZoom(), zoom);
  const pan = { x: width / 2 - (box.x + box.w / 2) * z, y: height / 2 - (box.y + box.h / 2) * z };
  if (animate && !reducedMotion()) cy.animate({ zoom: z, pan }, { duration: DOM.ms, easing: "ease-in-out-cubic" });
  else { cy.zoom(z); cy.pan(pan); }
}

/** Every box where the tweened state puts it: between its graph spot and its frame spot, then
 * toward its folded frame's card (fading out). */
function placeMembers() {
  const mode = dom.anim.mode;
  cy.batch(() => cy.nodes().forEach((n) => {
    const b = dom.byName.get(dom.frameOf.get(n.id()));
    if (!b) return;
    const o = offsetOf(b.name);
    const inner = b.inner.get(n.id());
    const from = dom.graphSpots.get(n.id()) || n.position();
    let x = lerp(from.x, b.x + o.dx + inner.x, mode);
    let y = lerp(from.y, b.y + o.dy + inner.y, mode);
    const fold = foldOf(b.name) * mode;
    const card = frameRect(b);
    x = lerp(x, card.x + card.w / 2, fold);
    y = lerp(y, card.y + card.h / 2, fold);
    n.position({ x, y });
    const alpha = 1 - fold;
    if (alpha < 1) { n.scratch("_domAlpha", alpha); n.style("opacity", alpha); }
    else if (n.scratch("_domAlpha") !== undefined) { n.removeScratch("_domAlpha"); n.removeStyle("opacity"); }
  }));
}

/** Which Cytoscape lines and boxes give way to the SVG drawing: lines between frames while
 * bundled, lines touching a folded frame (drawn to its card instead), boxes inside a folded card. */
function syncDomainClasses() {
  cy.batch(() => {
    cy.elements().removeClass("dom-hide dom-gone");
    if (!dom.frames.length) return;
    cy.nodes().forEach((n) => {
      const name = dom.frameOf.get(n.id());
      if (isFolded(name) && foldOf(name) >= 0.99) n.addClass("dom-gone");
    });
    cy.edges().forEach((e) => {
      if (e.hasClass("fanned")) return;
      const a = dom.frameOf.get(e.data("source")), b = dom.frameOf.get(e.data("target"));
      if ((a !== b && !everyLine()) || isFolded(a) || isFolded(b)) e.addClass("dom-hide");
    });
  });
}

// ---------- tween (one loop for mode, bundles and folds; reduced motion jumps) ----------

const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

function tweenTo(targets, ms, done = null) {
  for (const t of dom.tweens) for (const k of Object.keys(targets)) delete t.to[k];
  const from = Object.fromEntries(Object.keys(targets).map((k) => [k, dom.anim[k] ?? 0]));
  dom.tweens.push({ from, to: { ...targets }, start: performance.now(), ms: reducedMotion() ? 0 : ms, done });
  if (dom.tweens.length === 1) requestAnimationFrame(tweenStep);
}

function tweenStep(now) {
  const finished = [];
  dom.tweens = dom.tweens.filter((t) => {
    const p = t.ms ? Math.min(1, (now - t.start) / t.ms) : 1;
    for (const k of Object.keys(t.to)) dom.anim[k] = lerp(t.from[k], t.to[k], ease(p));
    if (p >= 1) finished.push(t);
    return p < 1;
  });
  if (dom.frames.length) placeMembers();
  scheduleFan();
  finished.forEach((t) => t.done?.());
  if (dom.tweens.length) requestAnimationFrame(tweenStep);
}

function stopTweens() {
  dom.tweens = [];
}

function settledDomains() {
  return !dom.tweens.length;
}

// ---------- fold, drag, focus ----------

function toggleFold(name) {
  const fold = !isFolded(name);
  if (fold) dom.folded.add(name); else dom.folded.delete(name);
  syncDomainClasses();
  tweenTo({ [`fold:${name}`]: fold ? 1 : 0 }, DOM.foldMs, () => { syncDomainClasses(); routeEdges(); });
}

/** One click folds; a second click soon after is a double-click: show just that domain. */
function onFrameClick(name, detail) {
  clearTimeout(dom.clickTimer);
  if (detail >= 2) { enterDomainFocus(name); return; }
  dom.clickTimer = setTimeout(() => toggleFold(name), DOM.clickWait);
}

function bringToFront(name) {
  dom.stack = [...dom.stack.filter((n) => n !== name), name];
}

function startFrameDrag(name, event) {
  bringToFront(name);
  dom.drag = { name, x: event.clientX, y: event.clientY, moved: false };
  cy.userPanningEnabled(false);
  hideDomainTip();
}

function onFrameDragMove(event) {
  const drag = dom.drag;
  if (!drag) return;
  const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
  if (!drag.moved && Math.hypot(dx, dy) < DOM.dragStart) return;
  drag.moved = true;
  drag.x = event.clientX;
  drag.y = event.clientY;
  const k = 1 / cy.zoom();
  const o = offsetOf(drag.name);
  dom.offsets.set(drag.name, { dx: o.dx + dx * k, dy: o.dy + dy * k });
  placeMembers();
  scheduleFan();
}

function endFrameDrag() {
  if (!dom.drag) return;
  const moved = dom.drag.moved;
  dom.drag = null;
  cy.userPanningEnabled(true);
  if (moved) routeEdges();
}

/** The open frame whose frame holds a model point (the top one), or null. */
function frameAt(p) {
  for (const name of [...dom.stack].reverse()) {
    const b = dom.byName.get(name);
    const r = frameRect(b);
    if (!isFolded(name) && p.x >= r.x && p.x <= r.x + r.w && p.y >= r.y && p.y <= r.y + r.h) return name;
  }
  return null;
}

function enterDomainFocus(name) {
  if (state.focus) return;  // a tour step's view
  clearTimeout(dom.clickTimer);
  state.domainFocus = { name, back: state.level === 0 ? 0 : null };
  if (state.level === 0) state.level = 1;
  state.crumbs = [];
  state.selected = null;
  loadView();
}

function leaveDomainFocus() {
  if (!state.domainFocus) return;
  if (state.domainFocus.back === 0) state.level = 0;
  state.domainFocus = null;
  state.crumbs = [];
  state.selected = null;
  loadView();
}

/** Domain focus layout: the domain's classes laid out on their own, the faded cards and outside
 * code they talk to on a ring around them (each near the classes it talks to). */
function placeFocus() {
  clearFrames();
  const core = cy.nodes().filter((n) => !n.data("raw").faded);
  const outside = cy.nodes().filter((n) => n.data("raw").faded);
  const nodes = frameInput().filter((n) => !cy.getElementById(n.id).data("raw").faded);
  const ids = new Set(nodes.map((n) => n.id));
  const spots = CoderLayout.layered(nodes, layoutLines().filter((e) => ids.has(e.from) && ids.has(e.to)));
  // laid out closed, so opening a class moves nothing: an open box grows right and down from its spot
  const rects = nodes.map((n) => { const p = spots.get(n.id); return { x: p.x - n.w / 2, y: p.y - n.h / 2, w: n.w, h: n.h }; });
  core.forEach((n) => n.position(grownSpot(n.id(), spots.get(n.id()))));
  const { pad, head } = CoderDomains.FRAME;  // the frame drawFocus puts around them
  const inner = boxAround(rects) || { x: 0, y: 0, w: 0, h: 0 };
  const box = { x: inner.x - pad, y: inner.y - head - pad, w: inner.w + 2 * pad, h: inner.h + head + 2 * pad };
  const items = outside.map((n) => {
    const linked = n.connectedEdges().connectedNodes().filter((m) => ids.has(m.id())).map((m) => spots.get(m.id()));
    const anchor = linked.length ? { x: linked.reduce((s, p) => s + p.x, 0) / linked.length,
      y: linked.reduce((s, p) => s + p.y, 0) / linked.length } : null;
    return { id: n.id(), w: n.data("w"), h: n.data("h"), anchor };
  });
  const ring = CoderDomains.ringSpots({ x1: box.x, y1: box.y, x2: box.x + box.w, y2: box.y + box.h }, items);
  outside.forEach((n) => n.position(ring.get(n.id())));
  outside.connectedEdges().addClass("dom-hide");  // drawn as one bundle per card instead
}

// ---------- drawing ----------

/** Size of a domain card box (level 0 and the cards around a focused domain). */
/** A server domain card: taller by a line when its description takes two. */
function domainCardData(raw) {
  const { w, h } = CoderDomains.FRAME.card;
  return { w, h: h + (cardDesc(raw, w - 28).length > 1 ? 15 : 0), label: "" };
}

/** A domain card's description (its own notes), on at most 2 lines of `maxW`. */
function cardDesc(raw, maxW) {
  const text = whatItDoes(raw);
  if (!text) return [];
  const measure = (t) => textWidth(t, `${DOM.descFont} ${token("f-body")}`);
  return clampLines(wrapText(text, measure, maxW), 2, measure, maxW);
}

function frameGroup(layer, name, extra = "") {
  const unsorted = name === CoderDomains.UNSORTED;
  const group = svgEl("g", { class: `dom-frame${unsorted ? " unsorted" : ""}${extra}`, "data-frame": name }, layer);
  group.style.setProperty("--h", CoderDomains.hueOf(name));
  group.style.setProperty("--s", unsorted ? "8%" : "55%");
  return group;
}

/** A card: the folded look of a frame, also every server domain card. */
function drawCard(layer, r, { name, count = null, sub = "", desc = [], tip = "", extra = "" }) {
  const group = frameGroup(layer, name, ` card${extra}`);
  const head = CoderDomains.FRAME.head;
  svgEl("rect", { class: "body", x: r.x, y: r.y, width: r.w, height: r.h, rx: 12 }, group);
  svgEl("path", { class: "head", d: headPath(r) }, group);
  svgEl("path", { class: "tri", transform: `translate(${r.x + 16},${r.y + head / 2})`, d: "M-3,-4.5 L4,0 L-3,4.5 z" }, group);
  svgEl("text", { class: "title", x: r.x + 30, y: r.y + 22 }, group).textContent = name;
  if (count !== null) svgEl("text", { class: "count", x: r.x + r.w - 14, y: r.y + 21.5, "text-anchor": "end" }, group).textContent = classCount(count);
  if (sub) svgEl("text", { class: "sub", x: r.x + 14, y: r.y + head + 20 }, group).textContent = sub;
  desc.forEach((text, i) => { svgEl("text", { class: "desc", x: r.x + 14, y: r.y + head + 18 + i * 15 }, group).textContent = text; });
  if (tip) svgEl("title", {}, group).textContent = tip;
  return group;
}

function classCount(n) {
  return `${n} ${n === 1 ? "class" : "classes"}`;
}

function headPath(r) {
  const head = CoderDomains.FRAME.head;
  return `M${r.x},${r.y + head} V${r.y + 12} a12,12 0 0 1 12,-12 H${r.x + r.w - 12} a12,12 0 0 1 12,12 V${r.y + head} z`;
}

function cardSub(b) {
  const names = b.members.map((id) => nodeName(id)).join(", ");
  return names.length > DOM.cardTip ? `${names.slice(0, DOM.cardTip - 1)}…` : names;
}

/** What a domain does (its notes on `domain:<name>`, from the view's `domains`), "" when unknown. */
function domainText(name) {
  return whatItDoes(state.current?.domains?.[name]);
}

/** Text cut with "…" to fit `maxW` at `font` (one line). */
function fitLine(text, font, maxW) {
  return clampLines(wrapText(text, (t) => textWidth(t, font), maxW), 1, (t) => textWidth(t, font), maxW)[0] || "";
}

function frameHint(name) {
  return isFolded(name) ? `${name}: click to open the frame · double-click to show only this domain`
    : `${name}: click to fold into one card · double-click to show only this domain · drag to move (not saved)`;
}

function drawFrame(back, front, b) {
  const r = frameRect(b);
  const alpha = Math.min(1, dom.anim.mode * 1.4);
  if (foldOf(b.name) >= 0.5) {
    const card = drawCard(front, r, { name: b.name, count: b.members.length, sub: foldOf(b.name) > 0.95 ? cardSub(b) : "", extra: " clickable" });
    card.setAttribute("opacity", alpha);
    bindFramePointer(card, b.name);
    return;
  }
  const body = frameGroup(back, b.name);
  body.setAttribute("opacity", alpha);
  svgEl("rect", { class: "body", x: r.x, y: r.y, width: r.w, height: r.h, rx: 12 }, body);
  const head = frameGroup(front, b.name, " header");
  head.setAttribute("opacity", alpha);
  const path = svgEl("path", { class: "head", d: headPath(r) }, head);
  svgEl("title", {}, path).textContent = frameHint(b.name);
  svgEl("path", { class: "tri", transform: `translate(${r.x + 16},${r.y + CoderDomains.FRAME.head / 2})`, d: "M-4.5,-3 L4.5,-3 L0,4 z" }, head);
  svgEl("text", { class: "title", x: r.x + 30, y: r.y + 22 }, head).textContent = b.name;
  svgEl("text", { class: "count", x: r.x + r.w - 14, y: r.y + 21.5, "text-anchor": "end" }, head).textContent = classCount(b.members.length);
  const desc = domainText(b.name);
  if (desc) {
    path.querySelector("title").textContent = `${desc}\n${frameHint(b.name)}`;
    const font = `${DOM.descFont} ${token("f-body")}`;
    const line = svgEl("text", { class: "desc", x: r.x + 14, y: r.y + CoderDomains.FRAME.head + 15 }, head);
    line.textContent = fitLine(desc, font, r.w - 28);
    svgEl("title", {}, line).textContent = desc;
  }
  bindFramePointer(path, b.name);
}

function bindFramePointer(el, name) {
  el.addEventListener("pointerdown", (event) => { if (event.button === 0) startFrameDrag(name, event); });
  el.addEventListener("click", (event) => { if (!dom.dragMoved) onFrameClick(name, event.detail); });
  el.addEventListener("wheel", forwardWheel, { passive: false });
}

/** A class line's end in the drawing: the box, or its folded frame's card. */
function endRect(id) {
  const name = dom.frameOf.get(id);
  if (isFolded(name)) return frameRect(dom.byName.get(name));
  const n = cy.getElementById(id);
  return rectAt(id, n.position());
}

function endKey(id) {
  const name = dom.frameOf.get(id);
  return isFolded(name) ? `frame:${name}` : id;
}

function clip(r, tx, ty) {  // where the ray from the rect's centre to (tx, ty) leaves the rect
  const cx = r.x + r.w / 2, cy0 = r.y + r.h / 2, dx = tx - cx, dy = ty - cy0;
  if (!dx && !dy) return [cx, cy0];
  const s = Math.min(dx ? (r.w / 2) / Math.abs(dx) : Infinity, dy ? (r.h / 2) / Math.abs(dy) : Infinity);
  return [cx + dx * s, cy0 + dy * s];
}

/** A gentle curve between two rects; `bend` pushes it sideways (two lines in opposite directions). */
function route(ra, rb, bend = 0) {
  const ax = ra.x + ra.w / 2, ay = ra.y + ra.h / 2, bx = rb.x + rb.w / 2, by = rb.y + rb.h / 2;
  const len = Math.hypot(bx - ax, by - ay) || 1;
  const c = [(ax + bx) / 2 - (by - ay) / len * bend, (ay + by) / 2 + (bx - ax) / len * bend];
  const a = clip(ra, ...c), b = clip(rb, ...c);
  const unit = (p) => { const l = Math.hypot(p[0] - c[0], p[1] - c[1]) || 1; return [(p[0] - c[0]) / l, (p[1] - c[1]) / l]; };
  return { a, b, c, end: unit(b), start: unit(a), mid: [(a[0] + 2 * c[0] + b[0]) / 4, (a[1] + 2 * c[1] + b[1]) / 4] };
}

function pathD(r, trimEnd = 0, trimStart = 0) {
  const b = [r.b[0] - r.end[0] * trimEnd, r.b[1] - r.end[1] * trimEnd];
  const a = [r.a[0] - r.start[0] * trimStart, r.a[1] - r.start[1] * trimStart];
  return `M${a[0]},${a[1]} Q${r.c[0]},${r.c[1]} ${b[0]},${b[1]}`;
}

function arrowHead(layer, tip, dir, size, tone) {  // a filled vee, sized with the bundle
  const [x, y] = tip, [dx, dy] = dir, px = -dy, py = dx;
  const pts = [[x, y], [x - dx * size + px * size * 0.5, y - dy * size + py * size * 0.5],
    [x - dx * size * 0.65, y - dy * size * 0.65], [x - dx * size - px * size * 0.5, y - dy * size - py * size * 0.5]];
  svgEl("path", { class: `fan-mark t-${tone}`, d: `M${pts.map((p) => p.join(",")).join(" L")} z` }, layer);
}

function badge(layer, at, label, cls) {
  const w = label.length * 7 + 14;
  const g = svgEl("g", { class: cls }, layer);
  svgEl("rect", { x: at[0] - w / 2, y: at[1] - 10, width: w, height: 20, rx: 10 }, g);
  svgEl("text", { x: at[0], y: at[1] + 4, "text-anchor": "middle" }, g).textContent = label;
  return g;
}

/** Lines drawn here instead of by Cytoscape: those touching a folded card (Every line), merged
 * per pair of ends with a count; `hot` lines (a hovered bundle's) in the highlight colour. */
function drawLines(layer, edges, hot) {
  const groups = CoderDomains.mergeEnds(edges, endKey);
  const keys = new Set(groups.map((g) => g.key));
  for (const g of groups) {
    const first = g.edges[0];
    const r = route(endRect(first.from), endRect(first.to), keys.has(`${g.b}>${g.a}`) ? 12 : 0);
    const many = g.edges.length > 1;
    const kind = many ? "association" : primaryKind(first.kinds);
    const tone = hot ? "hl" : many ? "accent" : wireTone({ faded: first.faded, direct: first.direct, from_fn: true, members: [] }, kind);
    drawPath(layer, pathD(r), { kind, links: [{ direct: many || first.direct }] }, tone, hot || null, wireMarkers(kind, tone));
    if (many) badge(layer, r.mid, String(g.edges.length), "dom-merge");
  }
}

/** Bundles between groups: `rectOf(group)` is where a group (a frame, a card) is drawn. */
function drawBundles(layer, bundles, opacity, rectOf) {
  for (const bd of bundles) {
    const r = route(rectOf(bd.p), rectOf(bd.q));
    const width = CoderDomains.bundleWidth(bd.edges.length);
    const hot = dom.hover === bd.key;
    const g = svgEl("g", { class: `dom-bundle-group${dom.hover && !hot ? " dim" : ""}`, opacity }, layer);
    svgEl("path", { class: `dom-bundle${hot ? " hot" : ""}`, "stroke-width": width, d: pathD(r, bd.fwd ? 9 : 0, bd.back ? 9 : 0) }, g);
    if (bd.fwd) arrowHead(g, r.b, r.end, 11 + width * 0.6, hot ? "hl" : "muted");
    if (bd.back) arrowHead(g, r.a, r.start, 11 + width * 0.6, hot ? "hl" : "muted");
    const hit = svgEl("path", { class: "dom-bundle-hit", d: pathD(r) }, g);
    const label = badge(g, r.mid, CoderDomains.bundleLabel(bd), `dom-bundle-badge${hot ? " hot" : ""}`);
    for (const el of [hit, label]) bindBundleHover(el, bd);
  }
}

function bindBundleHover(el, bundle) {
  el.addEventListener("mouseenter", (event) => setBundleHover(bundle, event));
  el.addEventListener("mousemove", (event) => placeDomainTip(event));
  el.addEventListener("mouseleave", () => setBundleHover(null));
  el.addEventListener("wheel", forwardWheel, { passive: false });
}

/** Hovering a bundle: tooltip with the split and the busiest lines; its classes stay lit. */
function setBundleHover(bundle, event = null) {
  if ((bundle?.key || null) === dom.hover) { placeDomainTip(event); return; }  // redrawn under the mouse
  dom.hover = bundle?.key || null;
  cy.batch(() => {
    cy.elements().removeClass("dom-dim");
    if (!bundle) return;
    const ends = new Set(bundle.edges.flatMap((e) => [e.from, e.to]));
    cy.nodes().filter((n) => !ends.has(n.id())).addClass("dom-dim");
    cy.edges().addClass("dom-dim");
  });
  if (bundle) showDomainTip(bundleTip(bundle), event); else hideDomainTip();
  scheduleFan();
}

function lineRow(e) {
  const calls = CoderDomains.callCount(e);
  return `<li>${esc(nodeName(e.from))} → ${esc(nodeName(e.to))} <span>${esc(primaryKind(e.kinds))}${e.direct ? "" : ", through hidden"}${calls ? ` · ${plural(calls, "call")}` : ""}</span></li>`;
}

/** A bundle end's name: a frame or focused domain by name, a card or outside class by its node's name. */
function groupName(key) {
  return isOutside(key) ? cy.getElementById(key).data("raw").name : key;
}

function bundleTip(bd) {
  const top = CoderDomains.topLines(bd.edges, DOM.hotLines);
  const more = bd.edges.length - top.length;
  const p = esc(groupName(bd.p)), q = esc(groupName(bd.q));
  return `<b>${p} ⇄ ${q}</b> · ${plural(bd.edges.length, "line")}`
    + `<div class="meta">${bd.fwd} ${p} → ${q}, ${bd.back} back</div>`
    + `<ul>${top.map(lineRow).join("")}${more > 0 ? `<li><span>… and ${more} more</span></li>` : ""}</ul>`;
}

function showDomainTip(html, event) {
  const tip = $("domTip");
  tip.innerHTML = html;
  tip.hidden = false;
  placeDomainTip(event);
}

function placeDomainTip(event) {
  const tip = $("domTip");
  if (tip.hidden || !event) return;
  const box = $("canvas").getBoundingClientRect();
  const x = Math.min(event.clientX - box.x + 14, box.width - tip.offsetWidth - 8);
  const below = event.clientY - box.y + 16;
  tip.style.left = `${Math.max(4, x)}px`;
  tip.style.top = `${below + tip.offsetHeight > box.height ? event.clientY - box.y - tip.offsetHeight - 10 : below}px`;
}

function hideDomainTip() {
  $("domTip").hidden = true;
}

/** Server domain cards (level 0, around a focused domain): the fold card look over their box. */
function drawDomainCards(front) {
  cy.nodes(":visible").filter((n) => n.data("raw").kind === "domain").forEach((n) => {
    const raw = n.data("raw");
    const r = rectAt(n.id(), n.position());
    const text = cardDesc(raw, r.w - 28);
    drawCard(front, r, { name: raw.name, sub: text.length ? "" : DOM.cardHint, desc: text,
      tip: text.length ? `${whatItDoes(raw, true)}\n${DOM.cardHint}` : "",
      extra: `${raw.faded ? " faded" : ""}${n.hasClass("sel") ? " sel" : ""}${isDimmed(n) ? " dim" : ""}` });
  });
}

/** Redraw both domain layers (drawFan calls this on every redraw, so they follow the boxes). */
function drawDomains() {
  const back = resetLayer("domBack");
  const front = resetLayer("domFront");
  if (cy.nodes().nonempty()) drawDomainCards(front.world);
  if (dom.frames.length && dom.anim.mode > 0.01) drawFrames(back.world, front.world);
  else if (state.current?.domain && !state.focus && cy.nodes().nonempty()) drawFocus(back.world, front.world);
}

function drawFrames(back, front) {
  const frameOf = (id) => dom.frameOf.get(id);
  const cross = lineInput().filter((e) => frameOf(e.from) !== frameOf(e.to));
  const bundles = CoderDomains.bundlesOf(cross, frameOf);
  const bundled = dom.anim.mode * dom.anim.bund;
  if (bundled > 0.01) drawBundles(front, bundles, bundled, (name) => frameRect(dom.byName.get(name)));
  if (everyLine()) drawLines(front, cross.filter((e) => isFolded(frameOf(e.from)) || isFolded(frameOf(e.to))), false);
  for (const name of dom.stack) drawFrame(back, front, dom.byName.get(name));
  drawHotLines(front, bundles);
}

function drawHotLines(front, bundles) {
  const hot = dom.hover && bundles.find((b) => b.key === dom.hover);
  if (hot) drawLines(front, hot.edges, true);
}

function isOutside(id) {
  return Boolean(cy.getElementById(id).data("raw")?.faded);
}

/** Domain focus: the domain's classes in one frame, a bundle to each card (or outside class) it talks to. */
function drawFocus(back, front) {
  const name = state.current.domain;
  const core = cy.nodes(":visible").filter((n) => !isOutside(n.id()));
  const box = boxAround(core.map((n) => rectAt(n.id(), n.position())));
  if (!box) return;
  const { pad, head } = CoderDomains.FRAME;
  const frame = { x: box.x - pad, y: box.y - head - pad * 0.7, w: box.w + 2 * pad, h: box.h + head + pad * 1.7 };
  const body = frameGroup(back, name);
  svgEl("rect", { class: "body", x: frame.x, y: frame.y, width: frame.w, height: frame.h, rx: 12 }, body);
  const top = frameGroup(front, name, " title-only");
  svgEl("path", { class: "head", d: headPath(frame) }, top);
  svgEl("text", { class: "title", x: frame.x + 14, y: frame.y + 22 }, top).textContent = name;
  svgEl("text", { class: "count", x: frame.x + frame.w - 14, y: frame.y + 21.5, "text-anchor": "end" }, top).textContent = classCount(core.length);
  const groupOf = (id) => (isOutside(id) ? id : name);
  const bundles = CoderDomains.bundlesOf(lineInput(), groupOf);
  drawBundles(front, bundles, 1, (key) => (key === name ? frame : rectAt(key, cy.getElementById(key).position())));
  drawHotLines(front, bundles);
}

// ---------- domain focus breadcrumb ----------

/** The crumbs' root while a domain is focused: the level (back to the frames), then the domain. */
function focusCrumbs(root) {
  const name = state.domainFocus.name;
  const here = state.crumbs.length ? `<button data-crumb="-1">${esc(name)}</button>` : `<span class="here">${esc(name)}</span>`;
  return [`<button data-crumb="domain-out" title="Back to the frames (Esc)">${esc(root)}</button>`, `<span class="dom-chip">${here}</span>`];
}

/** The domain chip's tint (set through the CSSOM: the page's CSP allows no style attributes). */
function tintFocusCrumbs(nav) {
  nav.querySelector(".dom-chip")?.style.setProperty("--h", CoderDomains.hueOf(state.domainFocus.name));
}

// ---------- events ----------

function onDomainKey(event) {
  if (event.key !== "Escape" || !state.domainFocus || $("tabGraph").hidden) return;
  if (event.target.closest?.("input, textarea, select, dialog, [contenteditable]") || document.querySelector("dialog[open]")) return;
  leaveDomainFocus();
}

function bindDomains() {
  $("showDomains").addEventListener("change", (event) => setDomains(event.target.checked));
  $("bundledBtn").addEventListener("click", () => setEveryLine(false));
  $("everyBtn").addEventListener("click", () => setEveryLine(true));
  cy.on("tapstart", (event) => {
    if (!dom.frames.length || dom.anim.mode < 1) return;
    const target = event.target;
    const name = target === cy ? frameAt(event.position) : target.isNode?.() ? dom.frameOf.get(target.id()) : null;
    if (name && event.originalEvent) startFrameDrag(name, event.originalEvent);
  });
  cy.on("dbltap", (event) => {
    if (event.target !== cy || !dom.frames.length) return;
    const name = frameAt(event.position);
    if (name) enterDomainFocus(name);
  });
  window.addEventListener("pointermove", onFrameDragMove);
  window.addEventListener("pointerup", () => {  // the click after a drag must not fold
    dom.dragMoved = Boolean(dom.drag?.moved);
    endFrameDrag();
    setTimeout(() => { dom.dragMoved = false; });
  });
  document.addEventListener("keydown", onDomainKey);
}
