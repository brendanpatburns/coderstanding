// Depth ruler (DESIGN 5, 6): one column per level, bar height = classes at that level.
// Pure helpers (columns, barHeight, label) plus the HTML for the row; app.js wires the clicks.
(() => {
const BAR_MAX_PX = 26;

// shown: the level on screen; zoomBase: the zoom's own level (null outside a zoom). Levels after
// zoomBase and before shown were skipped by the zoom, so they get "skipped".
function columns(scale, shown, zoomBase = null, domains = false) {
  shown = Math.min(shown, scale.count);  // a zoom at the last level shows one deeper: the last column stays lit
  return scale.names.map((name, level) => {
    let look = level === shown ? "here" : level < shown ? "above" : "deeper";
    if (zoomBase != null && level > zoomBase && level < shown) look = "skipped";
    return { level, name, count: scale.counts[level] ?? 0, look, disabled: level === 0 && !domains };
  });
}

function barHeight(count, most) {
  if (!most || !count) return 2;
  return Math.max(3, Math.round(count / most * BAR_MAX_PX));
}

function label(col) {
  return `${col.level} ${col.name} · ${col.count}`;
}

function esc(text) {
  return String(text).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}

function html(cols) {
  return cols.map((c) => `<button type="button" class="rcol ${c.look}" data-level="${c.level}"${c.disabled ? " disabled" : ""}
    title="${esc(label(c))}${c.disabled ? " (arrives when domains exist)" : ""}${c.look === "skipped" ? " (skipped by this zoom)" : ""}"
    ${c.look === "here" ? 'aria-current="true"' : ""}><i></i>
    <span>${esc(label(c))}${c.look === "here" ? " ◂" : ""}</span></button>`).join("");
}

// Fills `el` with the columns. Bar heights are set through the DOM (the page's CSP forbids style attributes).
function render(el, cols) {
  const most = Math.max(...cols.map((c) => c.count), 0);
  el.innerHTML = html(cols);
  el.querySelectorAll(".rcol").forEach((button, i) => {
    button.firstElementChild.style.height = `${barHeight(cols[i].count, most)}px`;
  });
}

const api = { columns, barHeight, label, html, render };
if (typeof module !== "undefined") module.exports = api;
else window.Ruler = api;
})();
