// View options (pure): the one table of the main graph's view switches, and everything that reads
// or writes them: the URL parser, the remembered switches (per app, or per browser), the presets
// (Read / Explore / Edit), the toolbar's "More options", and a tour's or step's `view:`. Tested
// with `node --test tests/js/test_view_options.mjs`.
//
// Precedence (lowest first): default < preset < remembered changes < tour < tour step < URL.
// A value is stored and sent under its URL name (`?lines=all`, a tour's `view: {lines: all}`).
"use strict";

(() => {
const LAYOUTS = ["layered", "organic", "hub", "grid"];  // layout.js CoderLayout.METHODS (same order)

/** Every view switch. type: bool | enum | list | int | string. scope: "app" = remembered per app
 *  and set by presets, "browser" = remembered for every app, "url" = this page only.
 *  tour: a tour or step may set it in its `view:`. The theme is personal: not here. */
const OPTIONS = [
  { key: "level", url: "level", type: "int", def: 1, label: "Level", group: "view", scope: "url" },
  { key: "view", url: "view", type: "enum", values: ["all", "concrete"], def: "all", label: "View",
    names: { all: "All types", concrete: "Concrete" }, group: "view", scope: "app", tour: true },
  { key: "external", url: "external", type: "bool", def: true, label: "Outside code", group: "show", scope: "app", tour: true },
  { key: "comm", url: "comm", type: "bool", def: true, label: "Communications", group: "show", scope: "app", tour: true },
  { key: "tests", url: "tests", type: "bool", def: false, label: "Tests", group: "show", scope: "app", tour: true },
  { key: "cards", url: "cards", type: "bool", def: true, label: "Specs & notes", group: "show", scope: "app", tour: true },
  { key: "planned", url: "planned", type: "enum", values: ["hide", "show", "only"], def: "show", label: "Planned work",
    names: { hide: "Hide", show: "Show", only: "Only planned work" }, group: "show", scope: "app", tour: true },
  { key: "track", url: "track", type: "string", def: "", label: "Progress", example: "Described, Tested, Built or a project's own",
    group: "show", scope: "app" },
  { key: "selfCalls", url: "selfcalls", type: "bool", def: true, label: "Self calls", group: "show", scope: "app", tour: true },
  { key: "domains", url: "domains", type: "bool", def: false, label: "Domains", group: "show", scope: "app" },
  { key: "between", url: "between", type: "enum", values: ["bundled", "every"], def: "bundled", label: "Between frames",
    names: { bundled: "Bundled", every: "Every line" }, group: "show", scope: "app" },
  { key: "filter", url: "filter", type: "string", def: "", label: "Highlight", example: "status:<s> or tag:<t>", group: "show", scope: "url", tour: true },
  { key: "layout", url: "layout", type: "enum", values: LAYOUTS, def: "layered", label: "Layout method",
    names: { layered: "Layered", organic: "Organic", hub: "Hub", grid: "Grid" }, group: "layout", scope: "browser" },
  { key: "mode", url: "mode", type: "enum", values: ["view", "edit"], def: "view", label: "Edit layout",
    names: { view: "View", edit: "Edit layout" }, group: "layout", scope: "app" },
  { key: "boxText", url: "boxtext", type: "enum", values: ["name", "short", "full"], def: "short", label: "Box text",
    names: { name: "Name", short: "Name + what it does", full: "Full" }, group: "faces", scope: "app", tour: true },
  { key: "lines", url: "lines", type: "enum", values: ["explained", "all"], def: "explained", label: "Lines",
    names: { explained: "Explained first", all: "All equal" }, group: "faces", scope: "app", tour: true },
  { key: "hiddenKinds", url: "hide_kind", type: "list", def: [], label: "Hidden line kinds", example: "signal, dependency", group: "faces", scope: "app", tour: true },
  { key: "hiddenTypes", url: "hide_type", type: "list", def: [], label: "Hidden file types", example: ".tscn, .txt", group: "show", scope: "app", tour: true },
  { key: "hiddenFileKinds", url: "hide_file_kind", type: "list", def: [], label: "Hidden file kinds", example: "Data, Docs", group: "show", scope: "app", tour: true },
  { key: "hiddenTags", url: "hide_tag", type: "list", def: [], label: "Hidden tags", example: "chat, audio", group: "show", scope: "app", tour: true },
  { key: "onlyTags", url: "tag", type: "list", def: [], label: "Only tags", example: "chat", group: "show", scope: "app", tour: true },
  { key: "details", url: "details", type: "enum", values: ["open", "closed"], def: "closed", label: "Details fold",
    names: { open: "Open", closed: "Closed" }, group: "panel", scope: "browser" },
  { key: "overview", url: "overview", type: "bool", def: true, label: "Overview at level 0", group: "panel", scope: "app" },
];
const BY_KEY = new Map(OPTIONS.map((o) => [o.key, o]));
const BY_URL = new Map(OPTIONS.map((o) => [o.url, o]));
const TOUR_OPTIONS = OPTIONS.filter((o) => o.tour);

/** A preset is a set of option values (over the defaults); Explore = the defaults. */
const PRESETS = {
  read: { label: "Read", values: { domains: true, boxText: "short", lines: "explained", external: false, tests: false, selfCalls: false },
    note: "Read: domains, what each box does, explained lines first; outside code, tests and self calls hidden." },
  explore: { label: "Explore", values: {},
    note: "Explore: every switch as it always was; everything the map knows." },
  edit: { label: "Edit", values: { mode: "edit", cards: true },
    note: "Edit: layout editing on, specs and notes shown, so you can fix spots and write hints." },
};
const PRESET_NAMES = Object.keys(PRESETS);
const DEFAULT_PRESET = "read";
const CUSTOM_NOTE = "Custom: your changed switches are kept for this project; pick a preset to go back.";

const STORE_KEY = "coderstanding-view";  // `<key>:<app>` = {preset, changed} (app scope); `<key>` = browser scope

const TRUE = new Set(["1", "true", "on", "yes"]);
const FALSE = new Set(["0", "false", "off", "no"]);

/** One option's value from text (URL), a stored value (tour front matter, localStorage) or a
 *  control; undefined when it is not a valid value (then the layer leaves it alone). */
function parse(opt, raw) {
  if (raw === undefined || raw === null) return undefined;
  if (opt.type === "list") {
    const items = Array.isArray(raw) ? raw : String(raw).split(",");
    return [...new Set(items.map((x) => String(x).trim()).filter(Boolean))].sort();
  }
  if (Array.isArray(raw) || typeof raw === "object") return undefined;
  const text = String(raw).trim().toLowerCase();
  if (opt.type === "bool") return typeof raw === "boolean" ? raw : TRUE.has(text) ? true : FALSE.has(text) ? false : undefined;
  if (opt.type === "int") return /^-?\d+$/.test(text) ? Number(text) : undefined;
  if (opt.type === "enum") return opt.values.includes(text) ? text : undefined;
  return String(raw);
}

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const clone = (v) => (Array.isArray(v) ? [...v] : v);

function defaults() {
  return Object.fromEntries(OPTIONS.map((o) => [o.key, clone(o.def)]));
}

/** Merge layers (objects of key -> value; null/undefined layers skipped) over the defaults. */
function resolve(...layers) {
  const out = defaults();
  for (const layer of layers) {
    for (const [key, value] of Object.entries(layer || {})) if (BY_KEY.has(key) && value !== undefined) out[key] = clone(value);
  }
  return out;
}

/** The keys of `values` that differ from `base` (default: the defaults), as {key: value}. */
function diff(values, base = defaults(), keys = OPTIONS.map((o) => o.key)) {
  return Object.fromEntries(keys.filter((k) => k in values && !same(values[k], base[k])).map((k) => [k, clone(values[k])]));
}

/** The values a URL sets (URLSearchParams or a query string); lists also take repeated names. */
function fromUrl(params) {
  const p = typeof params === "string" ? new URLSearchParams(params) : params;
  const out = {};
  for (const opt of OPTIONS) {
    if (!p.has(opt.url)) continue;
    const value = parse(opt, opt.type === "list" ? p.getAll(opt.url).join(",") : p.get(opt.url));
    if (value !== undefined) out[opt.key] = value;
  }
  return out;
}

/** `?preset=read|explore|edit`, else null. */
function presetFromUrl(params) {
  const p = typeof params === "string" ? new URLSearchParams(params) : params;
  const name = (p.get("preset") || "").toLowerCase();
  return PRESETS[name] ? name : null;
}

/** Values as URL text: only the ones that differ from the defaults. */
function toUrl(values, params = new URLSearchParams()) {
  for (const [key, value] of Object.entries(diff(values))) {
    const opt = BY_KEY.get(key);
    params.set(opt.url, opt.type === "list" ? value.join(",") : opt.type === "bool" ? (value ? "1" : "0") : String(value));
  }
  return params;
}

/** A stored `view:` (URL names, as a tour's front matter holds it) -> values; unknown names and
 *  bad values are left out, so a newer or older tour still loads. `tourOnly`: just tour options. */
function fromStored(stored, { tourOnly = false } = {}) {
  const out = {};
  if (!stored || typeof stored !== "object" || Array.isArray(stored)) return out;
  for (const [name, raw] of Object.entries(stored)) {
    const opt = BY_URL.get(name) || BY_KEY.get(name);
    if (!opt || (tourOnly && !opt.tour)) continue;
    const value = parse(opt, raw);
    if (value !== undefined) out[opt.key] = value;
  }
  return out;
}

/** Values -> a stored `view:` under URL names (the shape tours and the CLI use). */
function toStored(values) {
  return Object.fromEntries(Object.entries(values).filter(([k]) => BY_KEY.has(k)).map(([k, v]) => [BY_KEY.get(k).url, clone(v)]));
}

function presetValues(name) {
  return { ...(PRESETS[name] || PRESETS[DEFAULT_PRESET]).values };
}

const APP_KEYS = OPTIONS.filter((o) => o.scope === "app").map((o) => o.key);

/** True when the app-scope values are exactly what the preset sets ("Custom" otherwise). */
function isPreset(values, name) {
  return !Object.keys(diff(values, resolve(presetValues(name)), APP_KEYS)).length;
}

/** The options the preset sets to something other than the default (the "set by" marks). */
function presetSets(name) {
  return Object.keys(diff(resolve(presetValues(name))));
}

/** The remembered layer: {preset, changed}; changed = the switches the user set (app scope for
 *  this app, browser scope for all apps). `storage` is localStorage-like (may throw). */
function readRemembered(storage, app) {
  const read = (key) => { try { const t = storage.getItem(key); return t ? JSON.parse(t) : null; } catch { return null; } };
  const mine = read(`${STORE_KEY}:${app || ""}`) || {};
  const shared = read(STORE_KEY) || {};
  const pick = (stored, scope) => Object.fromEntries(Object.entries(fromStored(stored)).filter(([k]) => BY_KEY.get(k).scope === scope));
  return { preset: PRESETS[mine.preset] ? mine.preset : DEFAULT_PRESET,
    changed: { ...pick(mine.changed, "app"), ...pick(shared.changed, "browser") } };
}

function writeRemembered(storage, app, remembered) {
  const part = (scope) => toStored(Object.fromEntries(Object.entries(remembered.changed || {}).filter(([k]) => BY_KEY.get(k)?.scope === scope)));
  try {
    storage.setItem(`${STORE_KEY}:${app || ""}`, JSON.stringify({ preset: remembered.preset, changed: part("app") }));
    storage.setItem(STORE_KEY, JSON.stringify({ changed: part("browser") }));
  } catch { /* private window */ }
}

/** The user's change of one switch: the new remembered layer (a value equal to the preset's
 *  drops the change; "url" scope options are never remembered: kept for this page only). */
function remember(remembered, key, value) {
  const changed = { ...remembered.changed };
  const base = resolve(presetValues(remembered.preset));
  if (same(value, base[key])) delete changed[key]; else changed[key] = clone(value);
  return { preset: remembered.preset, changed };
}

/** Human text of one value ("Lines: All equal"). */
function valueText(key, value) {
  const opt = BY_KEY.get(key);
  if (!opt) return `${key}: ${value}`;
  const text = opt.type === "bool" ? (value ? "on" : "off") : opt.type === "list" ? (value.length ? value.join(", ") : "none")
    : opt.names?.[value] || String(value);
  return `${opt.label}: ${text}`;
}

const api = { OPTIONS, TOUR_OPTIONS, PRESETS, PRESET_NAMES, DEFAULT_PRESET, CUSTOM_NOTE, APP_KEYS, option: (k) => BY_KEY.get(k),
  parse, defaults, resolve, diff, fromUrl, presetFromUrl, toUrl, fromStored, toStored, presetValues, isPreset, presetSets,
  readRemembered, writeRemembered, remember, valueText };
if (typeof module !== "undefined") module.exports = api;
else window.ViewOptions = api;
})();
