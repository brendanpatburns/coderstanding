// A tiny Chrome DevTools protocol client for the end-to-end checks: starts headless Chromium,
// opens one page and drives it with real mouse and keyboard events. Node 22 only (built-in
// WebSocket and fetch), no packages.
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const CHROME = process.env.CHROME || "chromium-browser";

class Connection {
  constructor(url) {
    this.ws = new WebSocket(url);
    this.nextId = 1;
    this.waiting = new Map();
    this.listeners = [];
    this.ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.id && this.waiting.has(msg.id)) {
        const { resolve, reject } = this.waiting.get(msg.id);
        this.waiting.delete(msg.id);
        if (msg.error) reject(new Error(`${msg.error.message} ${msg.error.data || ""}`));
        else resolve(msg.result);
      } else if (msg.method) {
        this.listeners.forEach((fn) => fn(msg.method, msg.params));
      }
    };
    this.opened = new Promise((resolve, reject) => {
      this.ws.onopen = resolve;
      this.ws.onerror = reject;
    });
  }
  send(method, params = {}) {
    const id = this.nextId++;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => this.waiting.set(id, { resolve, reject }));
  }
  on(fn) { this.listeners.push(fn); }
  close() { this.ws.close(); }
}

/** Start Chromium headless; `args` adds command-line switches (e.g. --enable-speech-dispatcher). */
export async function launch({ width = 1600, height = 1000, args = [] } = {}) {
  const profile = mkdtempSync(join(tmpdir(), "coderstanding-e2e-"));
  const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`,
    "--no-first-run", "--no-default-browser-check", "--disable-gpu", "--hide-scrollbars", "--mute-audio",
    `--window-size=${width},${height}`, ...args, "about:blank"], { stdio: ["ignore", "ignore", "pipe"] });
  const wsUrl = await new Promise((resolve, reject) => {
    let text = "";
    chrome.stderr.on("data", (chunk) => {
      text += chunk;
      const match = text.match(/DevTools listening on (ws:\/\/\S+)/);
      if (match) resolve(match[1]);
    });
    chrome.on("exit", (code) => reject(new Error(`chromium exited (${code}): ${text.slice(-400)}`)));
  });
  const browser = new Connection(wsUrl);
  await browser.opened;
  const { targetInfos } = await browser.send("Target.getTargets");
  const target = targetInfos.find((t) => t.type === "page");
  const port = new URL(wsUrl).port;
  const page = new Page(new Connection(`ws://127.0.0.1:${port}/devtools/page/${target.targetId}`), browser);
  await page.conn.opened;
  await page.start(width, height);
  const close = () => {
    try { browser.close(); page.conn.close(); } catch { /* already closed */ }
    chrome.kill();
    try { rmSync(profile, { recursive: true, force: true }); } catch { /* chromium still writing */ }
  };
  return { page, browser, close };
}

export class Page {
  constructor(conn, browser) {
    this.conn = conn;
    this.browser = browser;
    this.errors = [];
    this.mouse = { x: 0, y: 0 };
    conn.on((method, params) => {
      if (method === "Runtime.exceptionThrown") {
        this.errors.push(params.exceptionDetails.exception?.description || params.exceptionDetails.text);
      } else if (method === "Runtime.consoleAPICalled" && params.type === "error") {
        this.errors.push(params.args.map((a) => a.value ?? a.description).join(" "));
      } else if (method === "Log.entryAdded" && params.entry.level === "error") {
        this.errors.push(`${params.entry.text} ${params.entry.url || ""}`);
      }
    });
  }
  async start(width, height) {
    await this.conn.send("Runtime.enable");
    await this.conn.send("Log.enable");
    await this.conn.send("Page.enable");
    await this.conn.send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false });
  }
  send(method, params) { return this.conn.send(method, params); }

  async goto(url) {
    const loaded = new Promise((resolve) => this.conn.on((m) => { if (m === "Page.loadEventFired") resolve(); }));
    await this.send("Page.navigate", { url });
    await loaded;
  }
  async grantClipboard(origin) {
    await this.browser.send("Browser.grantPermissions", { origin,
      permissions: ["clipboardReadWrite", "clipboardSanitizedWrite"] });
  }
  /** Evaluate an expression (or `() => ...` source) in the page; returns its JSON value. */
  async eval(expr) {
    const source = typeof expr === "function" ? `(${expr})()` : expr;
    const r = await this.send("Runtime.evaluate", { expression: source, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error(`eval failed: ${r.exceptionDetails.exception?.description || r.exceptionDetails.text}`);
    return r.result.value;
  }
  async waitFor(expr, { timeout = 15000, what = String(expr) } = {}) {
    const end = Date.now() + timeout;
    for (;;) {
      const value = await this.eval(expr).catch(() => null);
      if (value) return value;
      if (Date.now() > end) throw new Error(`timed out waiting for ${what}`);
      await sleep(80);
    }
  }
  async screenshot(path) {
    const { data } = await this.send("Page.captureScreenshot", { format: "png" });
    writeFileSync(path, Buffer.from(data, "base64"));
  }

  // ---------- mouse ----------
  async move(x, y, steps = 1) {
    const from = this.mouse;
    for (let i = 1; i <= steps; i++) {
      const p = { x: from.x + ((x - from.x) * i) / steps, y: from.y + ((y - from.y) * i) / steps };
      await this.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: p.x, y: p.y, buttons: this.buttons || 0,
        button: this.buttons ? "left" : "none" });
    }
    this.mouse = { x, y };
  }
  async down(clickCount = 1) {
    this.buttons = 1;
    await this.send("Input.dispatchMouseEvent", { type: "mousePressed", ...this.mouse, button: "left", buttons: 1, clickCount });
  }
  async up(clickCount = 1) {
    this.buttons = 0;
    await this.send("Input.dispatchMouseEvent", { type: "mouseReleased", ...this.mouse, button: "left", buttons: 0, clickCount });
  }
  async click(x, y) {
    await this.move(x, y);
    await this.down(1);
    await this.up(1);
  }
  async dblclick(x, y) {
    await this.click(x, y);
    await this.down(2);
    await this.up(2);
  }
  async drag(from, to, steps = 8) {
    await this.move(from.x, from.y);
    await this.down();
    await this.move(to.x, to.y, steps);
    await this.up();
  }
  /** Press the middle button at `from`, drag to `to`, release. */
  async middleDrag(from, to, steps = 6) {
    await this.move(from.x, from.y);
    await this.send("Input.dispatchMouseEvent", { type: "mousePressed", ...this.mouse, button: "middle", buttons: 4, clickCount: 1 });
    for (let i = 1; i <= steps; i++) {
      await this.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: from.x + ((to.x - from.x) * i) / steps,
        y: from.y + ((to.y - from.y) * i) / steps, button: "middle", buttons: 4 });
    }
    this.mouse = { ...to };
    await this.send("Input.dispatchMouseEvent", { type: "mouseReleased", ...this.mouse, button: "middle", buttons: 0, clickCount: 1 });
  }
  async wheel(x, y, deltaY) {
    await this.move(x, y);
    await this.send("Input.dispatchMouseEvent", { type: "mouseWheel", x, y, deltaX: 0, deltaY });
  }
  /** Centre of the first element matching `selector` (scrolled into view), or null. */
  async centreOf(selector) {
    return this.eval(`(() => { const el = document.querySelector(${JSON.stringify(selector)});
      if (!el) return null; el.scrollIntoView({block: "nearest"}); const r = el.getBoundingClientRect();
      return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; })()`);
  }
  async clickOn(selector) {
    const p = await this.centreOf(selector);
    if (!p) throw new Error(`no element ${selector}`);
    await this.click(p.x, p.y);
  }

  // ---------- keyboard ----------
  async key(key, { ctrl = false, shift = false } = {}) {
    const info = KEYS[key] || { code: `Key${key.toUpperCase()}`, keyCode: key.toUpperCase().charCodeAt(0), text: key };
    const modifiers = (ctrl ? 2 : 0) | (shift ? 8 : 0);
    const text = ctrl ? undefined : info.text;
    await this.send("Input.dispatchKeyEvent", { type: text ? "keyDown" : "rawKeyDown", key, code: info.code,
      windowsVirtualKeyCode: info.keyCode, modifiers, text });
    await this.send("Input.dispatchKeyEvent", { type: "keyUp", key, code: info.code, windowsVirtualKeyCode: info.keyCode, modifiers });
  }
  async type(text) {
    await this.send("Input.insertText", { text });
  }
}

const KEYS = {
  Enter: { code: "Enter", keyCode: 13, text: "\r" },
  Escape: { code: "Escape", keyCode: 27 },
  Tab: { code: "Tab", keyCode: 9 },
  ArrowLeft: { code: "ArrowLeft", keyCode: 37 },
  ArrowRight: { code: "ArrowRight", keyCode: 39 },
  ArrowUp: { code: "ArrowUp", keyCode: 38 },
  ArrowDown: { code: "ArrowDown", keyCode: 40 },
  Home: { code: "Home", keyCode: 36 },
  End: { code: "End", keyCode: 35 },
};

export function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
