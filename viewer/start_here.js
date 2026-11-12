// "Start here" (DESIGN 7c): the graph tab's button that opens a start tour (a tour flagged
// `start`) in the Tours tab, and the numbered step badges on the graph boxes while one is open.
// The path itself is picked by the view engine (views.start_paths) and saved as a tour; this
// file only lists those tours and draws numbers. `numbersOf` is pure (tests/js/test_start_here.mjs).
const StartHere = (() => {
const AI_REQUEST = (app) => `Draft the "Start here" path for ${app}: run \`coderstanding tour ${app} --draft-start\`, then read each drafted step with \`coderstanding tour ${app} <id>\` and improve its text so a newcomer knows where to start reading.`;

const here = { tours: [], numbers: new Map(), current: 0, bound: false, frame: 0 };

/** {view id -> [step numbers]} for a start tour (steps numbered from 1 by the ids they focus). */
function numbersOf(tour) {
  const numbers = new Map();
  if (!tour?.start) return numbers;
  tour.steps.forEach((step, i) => {
    for (const id of step.focus || []) {
      if (!numbers.has(id)) numbers.set(id, []);
      if (!numbers.get(id).includes(i + 1)) numbers.get(id).push(i + 1);
    }
  });
  return numbers;
}

// ---------- the badges ----------

const layer = () => document.getElementById("startBadges");

function drawBadges() {
  here.frame = 0;
  const svg = layer();
  if (!svg) return;
  svg.textContent = "";
  if (!here.numbers.size || typeof cy === "undefined" || cy.nodes().empty()) return;
  const { x, y } = cy.pan();
  const world = svgEl("g", { transform: `translate(${x},${y}) scale(${cy.zoom()})` }, svg);
  cy.nodes(":visible").forEach((node) => {
    const list = here.numbers.get(node.id());
    if (!list) return;
    const box = node.position(), w = node.data("w") || 0, h = node.data("h") || 0;
    list.forEach((number, k) => {
      const badge = svgEl("g", { class: "stepbadge" + (number - 1 === here.current ? " now" : "") + (number - 1 > here.current ? " later" : ""),
        transform: `translate(${box.x + w / 2 - 4 - k * 24},${box.y - h / 2 + 2})` }, world);
      svgEl("circle", { r: 11, opacity: number - 1 <= here.current ? 1 : Number(token("op-soft")) }, badge);
      svgEl("text", { y: 4 }, badge).textContent = number;
    });
  });
}

function scheduleBadges() {
  if (!here.frame) here.frame = requestAnimationFrame(drawBadges);
}

/** The Tours tab shows `tour` at step index `current` (null: none open): number its classes. */
function show(tour, current = 0) {
  here.numbers = numbersOf(tour);
  here.current = current;
  if (!here.bound && typeof cy !== "undefined" && cy) {
    here.bound = true;
    cy.on("viewport position add remove", scheduleBadges);
  }
  scheduleBadges();
}

// ---------- the button ----------

async function loadTours() {
  try { here.tours = (await api("/api/tours")).tours.filter((t) => t.start); } catch { here.tours = []; }
}

const menu = () => document.getElementById("startMenu");

function renderMenu() {
  if (!here.tours.length) {
    menu().innerHTML = `<p class="muted">No start path yet. Ask the AI to draft one from the map's entry points.</p>
      <button type="button" class="btn small" data-ask="1">Copy a request for AI</button>`;
    return;
  }
  menu().innerHTML = `<p class="muted">Pick a way in:</p>` + here.tours.map((t) =>
    `<button type="button" class="start-pick" data-tour="${esc(t.id)}"><span class="t">${esc(t.title)}</span><span class="m">${t.steps} steps${t.ai ? " · AI draft" : ""}</span></button>`).join("");
}

function copyRequest() {
  askAi(AI_REQUEST(CoderApps.current), flashNote, showCopyBox);  // app.js: the one offline Ask AI helper
}

/** Play a start tour: open it in the Tours tab at step 1. */
async function open(id) {
  menu().hidden = true;
  await showTab("tours");
  await TourView.open(id);
}

async function onButton() {
  if (!menu().hidden) { menu().hidden = true; return; }
  await loadTours();
  if (here.tours.length === 1) { await open(here.tours[0].id); return; }
  renderMenu();
  menu().hidden = false;
}

function bind() {
  document.getElementById("startHereBtn").addEventListener("click", onButton);
  menu().addEventListener("click", (event) => {
    const pick = event.target.closest("[data-tour]");
    if (pick) open(pick.dataset.tour);
    else if (event.target.closest("[data-ask]")) { menu().hidden = true; copyRequest(); }
  });
  document.addEventListener("click", (event) => {
    if (!menu().hidden && !event.target.closest("#startMenu, #startHereBtn")) menu().hidden = true;
  });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") menu().hidden = true; });
}

return { show, numbersOf, bind, open };
})();
if (typeof module !== "undefined") module.exports = StartHere;
