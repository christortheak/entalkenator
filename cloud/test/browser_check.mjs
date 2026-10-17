// Browser checks for the web app in headless Chrome, over the DevTools protocol.
// No npm packages: Node 22+ (built-in WebSocket) and a local Google Chrome.
//
//   node test/browser_check.mjs [--base http://127.0.0.1:8787] [--shots <dir>]
//
// Signs in with APP_PASSPHRASE from .dev.vars, then checks: the service worker
// installs and controls the page, Chrome reports the app installable, audio plays
// with the transcript following along, the app shell loads offline, and the
// layout fits a phone screen without sideways scrolling.

import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const arg = (name, dflt) => {
  const i = process.argv.indexOf(name);
  return i > 0 ? process.argv[i + 1] : dflt;
};
const BASE = arg("--base", "http://127.0.0.1:8787");
const SHOTS = arg("--shots", null);
const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9300 + Math.floor(Math.random() * 500);
const vars = Object.fromEntries(
  readFileSync(join(here, "..", ".dev.vars"), "utf8")
    .split("\n")
    .filter((l) => l.includes("=") && !l.startsWith("#"))
    .map((l) => [l.slice(0, l.indexOf("=")).trim(), l.slice(l.indexOf("=") + 1).trim()]),
);

let passed = 0;
const failed = [];
const check = (name, cond, detail = "") => {
  if (cond) {
    passed++;
    console.log("  ok   " + name);
  } else {
    failed.push(name);
    console.log(`  FAIL ${name} ${typeof detail === "string" ? detail : JSON.stringify(detail)}`);
  }
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const profile = mkdtempSync(join(tmpdir(), "entalk-chrome-"));
const chrome = spawn(CHROME, [
  "--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
  "--no-first-run", "--no-default-browser-check", "--autoplay-policy=no-user-gesture-required",
  "--mute-audio", "about:blank",
], { stdio: "ignore" });

async function cdpTarget() {
  for (let i = 0; i < 50; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      const page = list.find((t) => t.type === "page");
      if (page) return page.webSocketDebuggerUrl;
    } catch { /* not up yet */ }
    await sleep(200);
  }
  throw new Error("Chrome didn't start");
}

const ws = new WebSocket(await cdpTarget());
await new Promise((r) => ws.addEventListener("open", r, { once: true }));
let nextId = 1;
const pending = new Map();
const listeners = [];
ws.addEventListener("message", (ev) => {
  const msg = JSON.parse(ev.data);
  if (msg.id && pending.has(msg.id)) {
    const { resolve, reject } = pending.get(msg.id);
    pending.delete(msg.id);
    msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result);
  } else if (msg.method) {
    for (const l of listeners) l(msg);
  }
});
const send = (method, params = {}) =>
  new Promise((resolve, reject) => {
    const id = nextId++;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
const waitFor = (method, ms = 10000) =>
  new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error("timeout waiting for " + method)), ms);
    listeners.push((m) => {
      if (m.method === method) {
        clearTimeout(t);
        resolve(m.params);
      }
    });
  });
const evaluate = async (expression) => {
  const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
  return r.result.value;
};
const go = async (url) => {
  const loaded = waitFor("Page.loadEventFired");
  await send("Page.navigate", { url });
  await loaded;
};
const shot = async (name) => {
  if (!SHOTS) return;
  const { data } = await send("Page.captureScreenshot", { format: "png" });
  writeFileSync(join(SHOTS, name), Buffer.from(data, "base64"));
};

const consoleErrors = [];
listeners.push((m) => {
  if (m.method === "Runtime.exceptionThrown") consoleErrors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
  if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") consoleErrors.push(m.params.args.map((a) => a.value ?? a.description).join(" "));
});

try {
  await send("Page.enable");
  await send("Runtime.enable");
  await send("Network.enable");

  console.log("sign in");
  await go(`${BASE}/app/`);
  check("signed out lands on the login page", (await evaluate("location.pathname")) === "/app/login");
  const loaded = waitFor("Page.loadEventFired");
  await evaluate(`document.getElementById('passphrase').value = ${JSON.stringify(vars.APP_PASSPHRASE)}; document.querySelector('form').submit(); true`);
  await loaded;
  await sleep(800);
  check("signed in to the library", (await evaluate("location.pathname")) === "/app/" && (await evaluate("document.querySelectorAll('.card').length")) > 0);

  console.log("PWA");
  await sleep(1500);
  const reg = await evaluate("navigator.serviceWorker.getRegistration().then(r => r && r.active ? r.active.state : String(r))");
  check("service worker activated", reg === "activated", reg);
  await go(`${BASE}/app/`);
  await sleep(500);
  check("service worker controls the page after reload", await evaluate("!!navigator.serviceWorker.controller"));
  const manifest = await send("Page.getAppManifest");
  check("manifest parses without errors", manifest.url.endsWith("/app/manifest.webmanifest") && manifest.errors.length === 0, manifest.errors);
  const install = await send("Page.getInstallabilityErrors");
  check("Chrome reports the app installable", install.installabilityErrors.length === 0, install.installabilityErrors);

  console.log("player");
  // An item with chapters, so the transcript can follow the audio.
  const id = await evaluate("fetch('api/items').then(r => r.json()).then(d => d.items.find(i => i.files.audio && i.files.chapters).id)");
  await evaluate(`location.hash = '#/item/${id}'; true`);
  await sleep(1200);
  await evaluate("document.querySelector('.actions button.primary').click(); true");
  await sleep(2500);
  const p = await evaluate(`(() => { const a = document.getElementById('audio'); return { t: a.currentTime, paused: a.paused, err: a.error && a.error.code, current: document.querySelectorAll('.turn.current').length, speaker: document.getElementById('player-speaker').textContent }; })()`);
  check("audio plays", !p.paused && p.t > 0.5 && !p.err, p);
  check("transcript highlights the current turn", p.current === 1 && p.speaker.length > 0, p);
  await evaluate("document.getElementById('next-turn').click(); true");
  await sleep(400);
  const after = await evaluate("document.querySelector('.turn.current') && document.querySelector('.turn.current').dataset.i");
  check("next-speaker button moves to the next turn", after === "1", after);
  await evaluate("document.getElementById('audio').pause(); true");
  await shot("desktop-item.png");

  console.log("phone layout");
  await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
  for (const hash of ["#/", `#/item/${id}`, "#/feeds"]) {
    await evaluate(`location.hash = '${hash}'; true`);
    await sleep(900);
    const fit = await evaluate("({ sw: document.documentElement.scrollWidth, w: window.innerWidth })");
    check(`no sideways scrolling at 390px (${hash})`, fit.sw <= fit.w, fit);
    await shot(`phone-${hash === "#/" ? "library" : hash.startsWith("#/item") ? "item" : "feeds"}.png`);
  }
  const playerFits = await evaluate("(() => { const r = document.getElementById('player').getBoundingClientRect(); return r.width <= window.innerWidth && r.bottom <= window.innerHeight + 1; })()");
  check("player bar fits the phone screen", playerFits);
  await send("Emulation.clearDeviceMetricsOverride");

  console.log("offline");
  await send("Network.emulateNetworkConditions", { offline: true, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
  await go(`${BASE}/app/`).catch(() => {});
  await sleep(800);
  const offline = await evaluate("({ title: document.title, hasPlayer: !!document.getElementById('player'), css: getComputedStyle(document.querySelector('.topbar')).position })");
  check("app shell opens offline", offline.hasPlayer && offline.css === "sticky", offline);
  await send("Network.emulateNetworkConditions", { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });

  const unexpected = consoleErrors.filter((e) => !/Failed to load resource|net::ERR_INTERNET_DISCONNECTED|Couldn't load the library|Failed to fetch/.test(e));
  check("no script errors", unexpected.length === 0, unexpected);
} catch (e) {
  check("browser run finished", false, e.stack || String(e));
} finally {
  ws.close();
  chrome.kill();
  await sleep(300);
  rmSync(profile, { recursive: true, force: true });
}

console.log(`\n${passed} passed, ${failed.length} failed`);
if (failed.length) {
  console.log("failed: " + failed.join("; "));
  process.exit(1);
}
