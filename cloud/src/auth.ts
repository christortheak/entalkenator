// Three kinds of access:
//   /api/*            Authorization: Bearer <UPLOAD_TOKEN>   (the plugin's publish.py)
//   /app/*            passphrase cookie or Cloudflare Access  (the web app, AUTH_MODE)
//   /feed/*, /media/* FEED_TOKEN in the path                   (podcast apps)

import { Env, HttpError, MIN_PASSPHRASE_LENGTH, MIN_TOKEN_LENGTH, base64url, base64urlDecode, safeEqual } from "./util";

export type AuthMode = "passphrase" | "access";

export function authMode(env: Env): AuthMode {
  const mode = (env.AUTH_MODE || "passphrase").trim().toLowerCase();
  if (mode === "passphrase" || mode === "access") return mode;
  throw new HttpError(500, `AUTH_MODE must be "passphrase" or "access", not "${env.AUTH_MODE}".`);
}

// ---------------------------------------------------------------------------
// Upload API: bearer token

export async function requireBearer(request: Request, env: Env): Promise<void> {
  const expected = env.UPLOAD_TOKEN || "";
  if (expected.length < MIN_TOKEN_LENGTH) {
    throw new HttpError(503, `The UPLOAD_TOKEN secret is not set (or shorter than ${MIN_TOKEN_LENGTH} characters).`);
  }
  const header = request.headers.get("Authorization") || "";
  const m = /^Bearer\s+(.+)$/i.exec(header);
  if (!m || !(await safeEqual(m[1].trim(), expected))) throw new HttpError(401, "Missing or wrong bearer token");
}

// ---------------------------------------------------------------------------
// Feeds: token in the path

export function feedToken(env: Env): string | null {
  const t = env.FEED_TOKEN || "";
  return t.length >= MIN_TOKEN_LENGTH ? t : null;
}

export async function checkFeedToken(env: Env, candidate: string): Promise<boolean> {
  const t = feedToken(env);
  return !!t && (await safeEqual(candidate, t));
}

// ---------------------------------------------------------------------------
// Web app, passphrase mode: a signed session cookie

export const SESSION_COOKIE = "ent_session";
const SESSION_DAYS = 180;
const encoder = new TextEncoder();

export function passphraseConfigured(env: Env): boolean {
  return (env.APP_PASSPHRASE || "").length >= MIN_PASSPHRASE_LENGTH && (env.UPLOAD_TOKEN || "").length >= MIN_TOKEN_LENGTH;
}

// The signing key depends on both the passphrase and UPLOAD_TOKEN: changing
// either signs everyone out, and a stolen cookie can't be used to guess the
// passphrase offline without also knowing the upload token.
async function sessionKey(env: Env): Promise<CryptoKey> {
  const base = await crypto.subtle.importKey("raw", encoder.encode(env.UPLOAD_TOKEN || ""), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const derived = await crypto.subtle.sign("HMAC", base, encoder.encode("entalkenator-session-v1\u0000" + (env.APP_PASSPHRASE || "")));
  return crypto.subtle.importKey("raw", derived, { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);
}

export async function makeSessionCookie(env: Env, secure: boolean): Promise<string> {
  const exp = Math.floor(Date.now() / 1000) + SESSION_DAYS * 86400;
  const payload = `v1.${exp}`;
  const sig = await crypto.subtle.sign("HMAC", await sessionKey(env), encoder.encode(payload));
  return cookieHeader(`${payload}.${base64url(sig)}`, SESSION_DAYS * 86400, secure);
}

export function clearSessionCookie(secure: boolean): string {
  return cookieHeader("", 0, secure);
}

function cookieHeader(value: string, maxAge: number, secure: boolean): string {
  return `${SESSION_COOKIE}=${value}; Path=/app; Max-Age=${maxAge}; HttpOnly; SameSite=Lax${secure ? "; Secure" : ""}`;
}

function readCookie(request: Request, name: string): string | null {
  const header = request.headers.get("Cookie") || "";
  for (const part of header.split(";")) {
    const i = part.indexOf("=");
    if (i > 0 && part.slice(0, i).trim() === name) return part.slice(i + 1).trim();
  }
  return null;
}

export async function hasValidSession(request: Request, env: Env): Promise<boolean> {
  if (!passphraseConfigured(env)) return false;
  const value = readCookie(request, SESSION_COOKIE);
  if (!value) return false;
  const m = /^v1\.(\d{1,12})\.([A-Za-z0-9_-]{20,100})$/.exec(value);
  if (!m || Number(m[1]) < Date.now() / 1000) return false;
  try {
    return await crypto.subtle.verify("HMAC", await sessionKey(env), base64urlDecode(m[2]), encoder.encode(`v1.${m[1]}`));
  } catch {
    return false;
  }
}

export async function checkPassphrase(env: Env, candidate: string): Promise<boolean> {
  return passphraseConfigured(env) && (await safeEqual(candidate, env.APP_PASSPHRASE || ""));
}

// Throttle: 10 failures per IP per 15 minutes.
const FAIL_LIMIT = 10;
const FAIL_WINDOW = 15 * 60;

export function clientIp(request: Request): string {
  return request.headers.get("CF-Connecting-IP") || "local";
}

export async function loginBlocked(env: Env, ip: string): Promise<boolean> {
  const row = await env.DB.prepare("SELECT count, window_start FROM login_failures WHERE ip = ?").bind(ip).first<{ count: number; window_start: number }>();
  const now = Math.floor(Date.now() / 1000);
  return !!row && row.window_start > now - FAIL_WINDOW && row.count >= FAIL_LIMIT;
}

export async function recordLoginFailure(env: Env, ip: string): Promise<void> {
  const now = Math.floor(Date.now() / 1000);
  await env.DB.batch([
    env.DB.prepare("DELETE FROM login_failures WHERE window_start <= ?").bind(now - FAIL_WINDOW),
    env.DB.prepare(
      `INSERT INTO login_failures (ip, count, window_start) VALUES (?, 1, ?)
       ON CONFLICT(ip) DO UPDATE SET count = count + 1`,
    ).bind(ip, now),
  ]);
}

export async function clearLoginFailures(env: Env, ip: string): Promise<void> {
  await env.DB.prepare("DELETE FROM login_failures WHERE ip = ?").bind(ip).run();
}

// ---------------------------------------------------------------------------
// Web app, access mode: verify the Cloudflare Access JWT
//
// Access sits in front of /app and adds Cf-Access-Jwt-Assertion to every request it
// lets through. The Worker still checks it (RS256 against the team's published
// keys, plus aud, iss, exp and nbf), so a misconfigured Access policy fails closed.

interface Jwk {
  kid: string;
  kty: string;
  alg?: string;
  n: string;
  e: string;
}

let jwksCache: { url: string; keys: Map<string, CryptoKey>; fetched: number } | null = null;

export function accessTeamDomain(env: Env): string {
  let d = (env.ACCESS_TEAM_DOMAIN || "").trim().replace(/\/+$/, "");
  if (d && !/^https?:\/\//.test(d)) d = "https://" + d;
  return d;
}

async function accessKey(teamDomain: string, kid: string): Promise<CryptoKey | null> {
  const url = `${teamDomain}/cdn-cgi/access/certs`;
  const age = jwksCache && jwksCache.url === url ? Date.now() - jwksCache.fetched : Infinity;
  const cached = age < 10 * 60 * 1000 ? jwksCache!.keys.get(kid) : undefined;
  if (cached) return cached;
  // A stale cache or an unknown kid (Access rotates keys every few weeks) means a
  // refetch, but at most every 30 seconds, so bad tokens can't hammer the certs URL.
  if (age < 30 * 1000) return null;
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new HttpError(502, `Could not fetch Access keys from ${url} (HTTP ${res.status})`);
  const body = (await res.json()) as { keys?: Jwk[] };
  const keys = new Map<string, CryptoKey>();
  for (const k of body.keys || []) {
    if (k.kty !== "RSA" || !k.kid) continue;
    keys.set(
      k.kid,
      await crypto.subtle.importKey("jwk", { kty: "RSA", n: k.n, e: k.e, alg: "RS256", ext: true }, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]),
    );
  }
  jwksCache = { url, keys, fetched: Date.now() };
  return keys.get(kid) || null;
}

export interface AccessIdentity {
  email?: string;
  sub?: string;
}

/** Returns the identity, or a reason the request isn't allowed. */
export async function verifyAccess(request: Request, env: Env): Promise<{ ok: true; identity: AccessIdentity } | { ok: false; reason: string }> {
  const team = accessTeamDomain(env);
  const aud = (env.ACCESS_AUD || "").trim();
  if (!team || !aud) return { ok: false, reason: "AUTH_MODE is \"access\" but ACCESS_TEAM_DOMAIN or ACCESS_AUD is not set." };
  const token = request.headers.get("Cf-Access-Jwt-Assertion");
  if (!token) return { ok: false, reason: "No Cloudflare Access token on this request. Is an Access application protecting /app?" };
  const parts = token.split(".");
  if (parts.length !== 3) return { ok: false, reason: "Malformed Access token." };
  let header: { alg?: string; kid?: string };
  let claims: { aud?: string | string[]; iss?: string; exp?: number; nbf?: number; email?: string; sub?: string };
  try {
    header = JSON.parse(new TextDecoder().decode(base64urlDecode(parts[0])));
    claims = JSON.parse(new TextDecoder().decode(base64urlDecode(parts[1])));
  } catch {
    return { ok: false, reason: "Malformed Access token." };
  }
  if (header.alg !== "RS256" || !header.kid) return { ok: false, reason: "Unexpected Access token algorithm." };
  const key = await accessKey(team, header.kid);
  if (!key) return { ok: false, reason: "Access token signed with an unknown key." };
  const valid = await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, base64urlDecode(parts[2]), encoder.encode(`${parts[0]}.${parts[1]}`));
  if (!valid) return { ok: false, reason: "Access token signature is invalid." };
  const now = Math.floor(Date.now() / 1000);
  const auds = Array.isArray(claims.aud) ? claims.aud : [claims.aud];
  if (!auds.includes(aud)) return { ok: false, reason: "Access token is for a different application (aud)." };
  if (claims.iss !== team) return { ok: false, reason: "Access token is from a different team (iss)." };
  if (typeof claims.exp !== "number" || claims.exp < now - 30) return { ok: false, reason: "Access token has expired." };
  if (typeof claims.nbf === "number" && claims.nbf > now + 30) return { ok: false, reason: "Access token is not valid yet." };
  return { ok: true, identity: { email: claims.email, sub: claims.sub } };
}
