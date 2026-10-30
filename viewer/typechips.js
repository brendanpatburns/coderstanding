// Code-type chips (DESIGN 12d): the one place for the file type -> colour map and for wrapping
// a long name onto lines. Boxes (fanout.js) and the types strip (app.js) both use it.
// Pure functions; tests: tests/js/test_typechips.mjs.

const TYPE_COLORS = {
  light: { ".ts": "#063a46", ".cs": "#5b34a0", ".kt": "#b02f68", ".gd": "#177d49", ".tres": "#d4682a",
    ".tscn": "#5ea2ee", ".py": "#e0b020", ".js": "#f2e27a", other: "#a5adb8" },
  dark: { ".ts": "#15667a", ".cs": "#a07de8", ".kt": "#c14a7c", ".gd": "#3dbd7a", ".tres": "#e88a4c",
    ".tscn": "#8dbcf7", ".py": "#e9c24f", ".js": "#f7ec9f", other: "#7d8795" },
};
const INK_DARK = "#141b24";
const INK_LIGHT = "#ffffff";

/** The chip text for a file (`.gd`), "" when it has no extension. */
function fileType(file) {
  const base = String(file || "").split("/").pop();
  const dot = base.lastIndexOf(".");
  return dot > 0 ? base.slice(dot).toLowerCase() : "";
}

function relativeLuminance(hex) {
  const channel = (i) => {
    const c = parseInt(hex.slice(1 + i * 2, 3 + i * 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(0) + 0.7152 * channel(1) + 0.0722 * channel(2);
}

function contrast(a, b) {
  const [hi, lo] = [relativeLuminance(a), relativeLuminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** {bg, ink} for a type in a theme ("light" | "dark"); unknown types are grey. */
function typeColors(type, theme) {
  const palette = TYPE_COLORS[theme] || TYPE_COLORS.light;
  const bg = palette[type] || palette.other;
  return { bg, ink: contrast(bg, INK_DARK) >= contrast(bg, INK_LIGHT) ? INK_DARK : INK_LIGHT };
}

/** Split a name where a line may break: after `_` and `.`, before a capital that follows a lowercase letter or number. */
function nameParts(name) {
  return name.match(/[^_.]*[_.]+|[^_.]+$/g)?.flatMap((chunk) => chunk.split(/(?<=[a-z0-9])(?=[A-Z])/)) || [name];
}

/** Pieces of a sub-line: split after each " · " so marks wrap whole. */
function subParts(sub) {
  return sub.split(/(?<=·) /).map((part, i, all) => (i < all.length - 1 ? `${part} ` : part));
}

/**
 * Pack `parts` onto lines no wider than `maxW` (`measure(text)` gives the width). A single
 * part wider than a line is cut by characters: nothing is ever dropped or ellipsised.
 */
function packLines(parts, measure, maxW) {
  const lines = [];
  let line = "";
  const flush = () => { if (line) lines.push(line); line = ""; };
  for (const part of parts) {
    if (line && measure(line + part) > maxW) flush();
    if (measure(part) > maxW) {
      for (const ch of part) {
        if (line && measure(line + ch) > maxW) flush();
        line += ch;
      }
    } else {
      line += part;
    }
  }
  flush();
  return lines.length ? lines : [""];
}

/** A class name on lines (one line when it fits). */
function wrapName(name, measure, maxW) {
  return measure(name) <= maxW ? [name] : packLines(nameParts(name), measure, maxW);
}

/** A sub-line on lines (one line when it fits). */
function wrapSub(sub, measure, maxW) {
  return measure(sub) <= maxW ? [sub] : packLines(subParts(sub), measure, maxW).map((l) => l.trimEnd());
}

/** "dark" or "light": the page's theme setting, else the system's. */
function themeName() {
  const set = document.documentElement.getAttribute("data-theme");
  if (set) return set;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}
