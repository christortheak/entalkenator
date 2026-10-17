// Checks AUTH_MODE "access" locally: a stand-in Access team serves a JWKS, test
// tokens are signed with Node's crypto, and a second `wrangler dev` (its own local
// storage) runs with the access vars. No npm packages.
//
//   node test/access_check.mjs
//
// It verifies that /app accepts only a valid Cf-Access-Jwt-Assertion (signature,
// aud, iss, exp, nbf, kid, alg) and that feeds and the upload API don't depend on it.

import { spawn } from "node:child_process";
import { createSign, generateKeyPairSync } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import http from "node:http";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const cloud = join(dirname(fileURLToPath(import.meta.url)), "..");
const vars = Object.fromEntries(
  readFileSync(join(cloud, ".dev.vars"), "utf8")
    .split("\n")
    .filter((l) => l.includes("=") && !l.startsWith("#"))
    .map((l) => [l.slice(0, l.indexOf("=")).trim(), l.slice(l.indexOf("=") + 1).trim()]),
);
const JWKS_PORT = 8790 + Math.floor(Math.random() * 50);
const APP_PORT = 8850 + Math.floor(Math.random() * 50);
const TEAM = `http://127.0.0.1:${JWKS_PORT}`;
const AUD = "test-aud-" + Math.random().toString(16).slice(2);
const BASE = `http://127.0.0.1:${APP_PORT}`;

let passed = 0;
const failed = [];
const check = (name, cond, detail = "") => {
  if (cond) {
    passed++;
    console.log("  ok   " + name);
  } else {
    failed.push(name);
    console.log(`  FAIL ${name} ${JSON.stringify(detail)}`);
  }
};

const key = generateKeyPairSync("rsa", { modulusLength: 2048 });
const other = generateKeyPairSync("rsa", { modulusLength: 2048 });
const jwk = { ...key.publicKey.export({ format: "jwk" }), kid: "kid-1", alg: "RS256", use: "sig" };
let certsFetched = 0;
const jwks = http.createServer((req, res) => {
  if (req.url === "/cdn-cgi/access/certs") {
    certsFetched++;
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ keys: [jwk] }));
  } else {
    res.writeHead(404);
    res.end();
  }
});
await new Promise((r) => jwks.listen(JWKS_PORT, "127.0.0.1", r));

const b64 = (o) => Buffer.from(typeof o === "string" ? o : JSON.stringify(o)).toString("base64url");
function token({ claims = {}, header = {}, signer = key.privateKey } = {}) {
  const now = Math.floor(Date.now() / 1000);
  const h = { alg: "RS256", kid: "kid-1", typ: "JWT", ...header };
  const c = { aud: [AUD], iss: TEAM, exp: now + 600, iat: now, nbf: now - 5, email: "listener@example.com", sub: "u1", ...claims };
  const input = `${b64(h)}.${b64(c)}`;
  const sig = h.alg === "RS256" ? createSign("RSA-SHA256").update(input).sign(signer).toString("base64url") : "";
  return `${input}.${sig}`;
}

const persist = mkdtempSync(join(tmpdir(), "entalk-access-"));
const wrangler = spawn(process.env.WRANGLER || "wrangler", [
  "dev", "--local", "--ip", "127.0.0.1", "--port", String(APP_PORT), "--persist-to", persist,
  "--show-interactive-dev-session=false",
  "--var", "AUTH_MODE:access", "--var", `ACCESS_TEAM_DOMAIN:${TEAM}`, "--var", `ACCESS_AUD:${AUD}`,
], { cwd: cloud, env: { ...process.env, WRANGLER_SEND_METRICS: "false" }, stdio: ["ignore", "pipe", "pipe"] });
let log = "";
wrangler.stdout.on("data", (d) => (log += d));
wrangler.stderr.on("data", (d) => (log += d));

async function ready() {
  for (let i = 0; i < 120; i++) {
    if (/Ready on/.test(log)) return;
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error("wrangler dev didn't start:\n" + log.slice(-2000));
}

const get = (path, headers = {}, method = "GET") => fetch(BASE + path, { method, headers, redirect: "manual" });
const withJwt = (t, extra = {}) => ({ "Cf-Access-Jwt-Assertion": t, ...extra });

try {
  await ready();
  const now = Math.floor(Date.now() / 1000);

  let r = await get("/app/");
  check("no Access token -> 403 page", r.status === 403 && (await r.text()).includes("Cloudflare Access"), r.status);
  r = await get("/app/api/items");
  check("no Access token -> API 403", r.status === 403, r.status);
  r = await get("/app/login");
  check("login page is unreachable without Access", r.status === 403, r.status);

  const good = token();
  r = await get("/app/", withJwt(good));
  check("valid token -> app", r.status === 200 && (await r.text()).includes('src="app.js"'), r.status);
  r = await get("/app/api/library", withJwt(good));
  const lib = await r.json();
  check("valid token -> API", r.status === 200 && lib.authMode === "access", [r.status, lib.authMode]);
  r = await get("/app/login", withJwt(good));
  check("login redirects to the app", r.status === 302 && r.headers.get("location").endsWith("/app/"), r.status);
  r = await get("/app/logout", withJwt(good));
  check("logout goes to Access logout", r.status === 302 && r.headers.get("location").endsWith("/cdn-cgi/access/logout"), r.headers.get("location"));
  r = await get("/app/api/items/no-such-item", withJwt(good, { "X-Requested-With": "entalkenator", "Content-Type": "application/json" }), "DELETE");
  check("mutations go through with the token and header (404 for a missing item)", r.status === 404, r.status);
  r = await get("/app/api/items/no-such-item", withJwt(good), "DELETE");
  check("mutations still need X-Requested-With", r.status === 403, r.status);

  const bad = {
    "wrong aud": token({ claims: { aud: ["someone-else"] } }),
    "wrong iss": token({ claims: { iss: "https://evil.cloudflareaccess.com" } }),
    "expired": token({ claims: { exp: now - 120 } }),
    "not yet valid": token({ claims: { nbf: now + 600 } }),
    "signed by another key": token({ signer: other.privateKey }),
    "unknown kid": token({ header: { kid: "kid-2" } }),
    "alg none": token({ header: { alg: "none" } }),
    "garbage": "a.b.c",
  };
  for (const [name, t] of Object.entries(bad)) {
    r = await get("/app/api/items", withJwt(t));
    check(`${name} -> 403`, r.status === 403, r.status);
  }
  check("JWKS fetched and cached (not once per request)", certsFetched >= 1 && certsFetched <= 3, certsFetched);

  r = await get(`/feed/${vars.FEED_TOKEN}.xml`);
  check("feeds work without Access", r.status === 200, r.status);
  r = await get("/api/library", { Authorization: `Bearer ${vars.UPLOAD_TOKEN}` });
  check("upload API uses the bearer token, not Access", r.status === 200, r.status);
  r = await get("/api/library", withJwt(good));
  check("an Access token alone can't use the upload API", r.status === 401, r.status);
} catch (e) {
  check("access run finished", false, e.stack || String(e));
} finally {
  wrangler.kill("SIGINT");
  jwks.close();
  await new Promise((r) => setTimeout(r, 500));
  rmSync(persist, { recursive: true, force: true });
}

console.log(`\n${passed} passed, ${failed.length} failed`);
if (failed.length) {
  console.log("failed: " + failed.join("; "));
  process.exit(1);
}
