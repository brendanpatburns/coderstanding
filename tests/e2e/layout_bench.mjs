// Layout readability numbers on a real app, without a browser: asks a server for the views at
// every level, seeds spots the way the viewer does (layoutByLevels), then prints
// CoderLayout.measure for the drawn view at each level plus timing and a stability number.
// Run: nice -n 19 node tests/e2e/layout_bench.mjs --app REPO --app-data SCRATCH_DIR [--method layered] [--levels 1,2,3]
//   [--split] [--set key=value,...] [--layout FILE] [--spots] [--json] [--boxtext name|short|full]
// --app-data must be a scratch folder (it gets graph.json if missing); the app's real data is never touched.
// Box sizes are estimated from the names (12px mono), close to what the viewer draws; --boxtext
// sizes class boxes with the viewer's own face code (fanout.js) and estimated text widths instead.
import { spawn, execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import vm from "node:vm";
import { createServer } from "node:net";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const cli = join(here, "..", "..", "bin", "coderstanding");
const args = process.argv.slice(2);
const option = (name, fallback) => (args.includes(name) ? args[args.indexOf(name) + 1] : fallback);
// --layout FILE: another layout.js (e.g. main's, for before/after numbers)
const { layoutByLevels, measure, pickMethod, LAYOUT } = createRequire(import.meta.url)(option("--layout", "../../viewer/layout.js"));

const app = option("--app");
const data = option("--app-data");
if (!app || !data) { console.error("need --app REPO --app-data SCRATCH_DIR"); process.exit(2); }
const method = option("--method", "layered");
// --set key=value[,key=value]: try other LAYOUT numbers (tuning), e.g. --set standInLimit=0,alignSweeps=2
for (const pair of (option("--set", "") || "").split(",").filter(Boolean)) {
  const [key, value] = pair.split("=");
  LAYOUT[key] = Number(value);
}
const env = { ...process.env, CODERSTANDING_REGISTRY: join(data, "apps.json"), CODERSTANDING_DATA_HOME: join(data, "share") };

const freePort = () => new Promise((ok) => {
  const server = createServer();
  server.listen(0, "127.0.0.1", () => { const { port } = server.address(); server.close(() => ok(port)); });
});

/** fanNodeData from the viewer's fanout.js for a Box text mode, text widths estimated by font size. */
function faceSizer(mode) {
  const viewer = (name) => readFileSync(join(here, "..", "..", "viewer", name), "utf8");
  const textWidth = (text, font) => String(text).length * Number(/([\d.]+)px/.exec(font)[1]) * (font.includes("mono") ? 0.6 : 0.5);
  const sandbox = { state: { boxText: mode }, token: (name) => (name === "f-mono" ? "mono" : "body"), textWidth };
  vm.runInNewContext(`${viewer("typechips.js")}\n${viewer("fanout.js")}\nthis.fanNodeData = fanNodeData;`, sandbox);
  return sandbox.fanNodeData;
}
const boxText = option("--boxtext");
const sizeOf = boxText && faceSizer(boxText);

/** Viewer-like layout input for a view: box size from the name, lines with their kinds. */
function inputOf(view) {
  const nodes = view.nodes.map((n) => {
    const name = `${n.comm ? "⇄ " : ""}${n.name}${(n.flags || []).includes("singleton") ? "*" : ""}`;
    const box = { w: name.length * 7.2 + 24, h: 29 };
    const face = sizeOf ? sizeOf(n, { name, marks: "", label: name, ...box }) : box;
    return { id: n.id, w: face.w, h: face.h, level: n.level, kind: n.kind, flags: n.flags };
  });
  const ids = new Set(nodes.map((n) => n.id));
  const edges = view.edges.filter((e) => ids.has(e.from) && ids.has(e.to)).map((e) => ({ from: e.from, to: e.to, kinds: e.kinds }));
  return { nodes, edges };
}

/** Mean move (in typical box widths) of the other classes when a few classes are dropped. */
function stability(steps, spots, picks = 5) {
  const last = steps.at(-1);
  const unit = last.nodes.reduce((s, n) => s + n.w, 0) / last.nodes.length;
  const moves = [];
  for (let k = 1; k <= picks; k++) {
    const drop = last.nodes[Math.floor((k * last.nodes.length) / (picks + 1))].id;
    const less = steps.map((s) => ({ nodes: s.nodes.filter((n) => n.id !== drop), edges: s.edges.filter((e) => e.from !== drop && e.to !== drop) }));
    const again = layoutByLevels(less, new Map(), { method });
    let total = 0;
    for (const [id, p] of again) total += Math.hypot(p.x - spots.get(id).x, p.y - spots.get(id).y);
    moves.push(total / again.size / unit);
  }
  return Math.round((moves.reduce((a, b) => a + b, 0) / moves.length) * 100) / 100;
}

if (!existsSync(join(data, "graph.json"))) execFileSync(cli, ["scan", app, "-o", join(data, "graph.json")], { env, stdio: "ignore" });
const port = await freePort();
const server = spawn(cli, ["serve", app, "--data", data, "--port", String(port)], { stdio: "ignore", env });
try {
  const base = `http://127.0.0.1:${port}`;
  let meta = null;
  for (let i = 0; i < 600 && !meta; i++) {
    try { meta = await (await fetch(`${base}/api/meta`)).json(); } catch { await new Promise((ok) => setTimeout(ok, 100)); }
  }
  const views = [];
  for (let level = 1; level <= meta.max_level; level++) {
    views.push(await (await fetch(`${base}/api/view?${new URLSearchParams({ level, view: "all" })}`)).json());
  }
  const steps = views.map(inputOf);
  const started = performance.now();
  const spots = layoutByLevels(steps, new Map(), { method });
  const ms = Math.round(performance.now() - started);
  const levels = option("--levels", "1,2,3").split(",").map(Number).filter((l) => l <= steps.length);
  const result = { app: app.split("/").filter(Boolean).pop(), method: pickMethod(method, steps.at(-1).nodes.length).method, ms,
    stability: stability(steps, spots), levels: {} };
  for (const level of levels) {
    const { nodes, edges } = steps[level - 1];
    const t0 = performance.now();
    result.levels[level] = { ...measure(nodes, edges, spots), measureMs: Math.round(performance.now() - t0) };
    if (args.includes("--split")) {  // lines inside one level's band vs lines between bands
      const levelOf = new Map(nodes.map((n) => [n.id, n.kind === "external" ? 0 : n.level]));
      const same = (e) => levelOf.get(e.from) === levelOf.get(e.to);
      result.levels[`${level} inside`] = measure(nodes, edges.filter(same), spots);
      result.levels[`${level} between`] = measure(nodes, edges.filter((e) => !same(e)), spots);
    }
  }
  if (args.includes("--spots")) result.spots = Object.fromEntries([...spots].map(([id, s]) => [id, [Math.round(s.x), Math.round(s.y)]]));
  if (args.includes("--json")) console.log(JSON.stringify(result));
  else {
    console.log(`${result.app} ${result.method}: ${ms} ms, stability ${result.stability}`);
    for (const [level, m] of Object.entries(result.levels)) console.log(`  L${level}`, JSON.stringify(m));
    if (result.spots) console.log(result.spots);
  }
} finally {
  server.kill();
}
