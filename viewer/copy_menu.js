// Right-click menu for every copy point: Copy text | Copy location (refs.js `copyButtons`, menu
// form; app.js copies on click). `bind(root, dynamic)` covers one panel: a right-click inside it
// uses `dynamic()` ({ref, text} or null, e.g. the selected code lines) first, else the nearest
// copy pair (the item's own buttons) between the click and `root`. Inputs and text the user
// selected keep the browser's own menu.
"use strict";

const CopyMenu = (() => {
let menu = null;

function hide() {
  menu?.remove();
  menu = null;
}

/** {ref, text} of the nearest `.copy-pair` at or above `target`, up to `root`; null when none. */
function pairAbove(target, root) {
  for (let el = target; el && el !== root.parentElement; el = el.parentElement) {
    const pair = el.querySelector?.(".copy-pair");
    if (!pair) continue;
    return { ref: pair.querySelector("[data-copy-ref]").dataset.copyRef, text: pair.querySelector("[data-copy-text]")?.dataset.copyText || "" };
  }
  return null;
}

function show(x, y, { ref, text }) {
  hide();
  const holder = document.createElement("div");
  holder.innerHTML = CoderRefs.copyButtons(ref, text, true);
  menu = holder.firstChild;
  menu.style.left = `${x}px`;
  menu.style.top = `${y}px`;
  menu.addEventListener("click", () => setTimeout(hide, 700));  // app.js copies; let the button say so first
  document.body.append(menu);
}

function onContextMenu(event, root, dynamic) {
  if (event.target.closest("input, textarea, select, [contenteditable]")) return;
  const sel = getSelection();
  const found = dynamic?.() || (sel.isCollapsed || !sel.anchorNode ? pairAbove(event.target, root) : null);
  if (!found) return;
  event.preventDefault();
  show(event.clientX, event.clientY, found);
}

function bind(root, dynamic = null) {
  root.addEventListener("contextmenu", (event) => onContextMenu(event, root, dynamic));
}

document.addEventListener("mousedown", (event) => { if (menu && !menu.contains(event.target)) hide(); });
document.addEventListener("keydown", (event) => { if (event.key === "Escape") hide(); });
window.addEventListener("blur", hide);

return { bind, hide };
})();
