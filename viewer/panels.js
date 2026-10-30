// Resizable side panels: a drag handle on the inner edge of the specs sidebar and the details
// panel. Mouse drag or arrow keys; double-click resets; widths remembered per browser.
"use strict";

const PANEL = { min: 220, maxShare: 0.6, step: 16, bigStep: 64 };

function loadWidth(key) {
  try { return Number(localStorage.getItem(key)) || null; } catch { return null; }
}

function saveWidth(key, width) {
  try {
    if (width) localStorage.setItem(key, String(width)); else localStorage.removeItem(key);
  } catch { /* private window */ }
}

/**
 * Makes `handle` resize a panel. `panel.edge` is "right" (handle on the panel's right edge, the
 * left sidebar) or "left"; the width lives in the CSS variable `panel.cssVar` on #app.
 */
function bindResizer(handle, panel, onResize) {
  const app = $("app");
  const storeKey = `coderstanding-${panel.cssVar.slice(2)}`;
  const clamp = (w) => Math.round(Math.min(window.innerWidth * PANEL.maxShare, Math.max(PANEL.min, w)));
  const current = () => panel.element().getBoundingClientRect().width;
  const apply = (width) => {
    if (width) app.style.setProperty(panel.cssVar, `${width}px`); else app.style.removeProperty(panel.cssVar);
    handle.setAttribute("aria-valuenow", String(Math.round(width || current())));
    onResize();
  };
  const set = (width) => { const w = clamp(width); apply(w); saveWidth(storeKey, w); };
  const sign = panel.edge === "right" ? 1 : -1;

  handle.setAttribute("aria-valuemin", String(PANEL.min));
  handle.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    const start = { x: event.clientX, width: current() };
    handle.setPointerCapture(event.pointerId);
    handle.classList.add("dragging");
    const move = (e) => set(start.width + sign * (e.clientX - start.x));
    const stop = () => {
      handle.classList.remove("dragging");
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", stop);
      handle.removeEventListener("pointercancel", stop);
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", stop);
    handle.addEventListener("pointercancel", stop);
  });
  handle.addEventListener("keydown", (event) => {
    const step = event.shiftKey ? PANEL.bigStep : PANEL.step;
    const delta = { ArrowRight: step, ArrowLeft: -step }[event.key];
    if (!delta) return;
    event.preventDefault();
    set(current() + sign * delta);
  });
  handle.addEventListener("dblclick", () => { apply(null); saveWidth(storeKey, null); });
  const saved = loadWidth(storeKey);
  if (saved) apply(clamp(saved));
}

function bindPanels(onResize) {
  bindResizer($("sideResizer"), { edge: "right", cssVar: "--side-w", element: () => $("side") }, onResize);
  bindResizer($("detailsResizer"), { edge: "left", cssVar: "--details-w", element: () => $("details") }, onResize);
}
