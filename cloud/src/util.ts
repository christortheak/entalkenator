// Small helpers shared by the routes.

export interface Env {
  ASSETS: Fetcher;
  BUCKET: R2Bucket;
  DB: D1Database;
  AUTH_MODE?: string;
  ACCESS_TEAM_DOMAIN?: string;
  ACCESS_AUD?: string;
  LIBRARY_TITLE?: string;
  // Secrets
  UPLOAD_TOKEN?: string;
  FEED_TOKEN?: string;
  APP_PASSPHRASE?: string;
}

// Minimum lengths for the secrets. Short values are refused rather than used.
export const MIN_TOKEN_LENGTH = 32;
export const MIN_PASSPHRASE_LENGTH = 10;

/** Headers every response carries: nothing here should be indexed or leak its URL. */
export const PRIVATE_HEADERS: Record<string, string> = {
  "X-Robots-Tag": "noindex, nofollow, noarchive, nosnippet, noimageindex",
  "Referrer-Policy": "no-referrer",
  "X-Content-Type-Options": "nosniff",
};

export function withPrivateHeaders(response: Response): Response {
  const out = new Response(response.body, response);
  for (const [k, v] of Object.entries(PRIVATE_HEADERS)) out.headers.set(k, v);
  return out;
}

export class HttpError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export function json(data: unknown, status = 200, headers: HeadersInit = {}): Response {
  const h = new Headers(headers);
  h.set("Content-Type", "application/json; charset=utf-8");
  if (!h.has("Cache-Control")) h.set("Cache-Control", "no-store");
  return new Response(JSON.stringify(data, null, 2), { status, headers: h });
}

export function text(body: string, status = 200, contentType = "text/plain; charset=utf-8"): Response {
  return new Response(body, { status, headers: { "Content-Type": contentType, "Cache-Control": "no-store" } });
}

export function errorResponse(e: unknown): Response {
  if (e instanceof HttpError) return json({ error: e.message }, e.status);
  console.error("unhandled", e instanceof Error ? e.stack || e.message : String(e));
  return json({ error: "Internal error" }, 500);
}

export function decodeSegment(s: string): string {
  try {
    return decodeURIComponent(s);
  } catch {
    throw new HttpError(400, "Bad URL");
  }
}

export function nowIso(): string {
  return new Date().toISOString();
}

export function escapeXml(s: string): string {
  return s
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\uFFFE\uFFFF]/g, "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

export const escapeHtml = escapeXml;

export function base64url(bytes: ArrayBuffer | Uint8Array): string {
  const b = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  let s = "";
  for (let i = 0; i < b.length; i++) s += String.fromCharCode(b[i]);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function base64urlDecode(s: string): Uint8Array {
  const b64 = s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4);
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

const encoder = new TextEncoder();

async function sha256(s: string): Promise<ArrayBuffer> {
  return crypto.subtle.digest("SHA-256", encoder.encode(s));
}

/** Constant-time string comparison (hashes first so lengths match). */
export async function safeEqual(a: string, b: string): Promise<boolean> {
  const [ha, hb] = await Promise.all([sha256(a), sha256(b)]);
  return crypto.subtle.timingSafeEqual(ha, hb);
}

export function randomId(bytes = 10): string {
  const b = new Uint8Array(bytes);
  crypto.getRandomValues(b);
  return [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
}

export const ID_RE = /^[a-z0-9][a-z0-9-]{5,63}$/;

export function slugify(name: string): string {
  return name
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60);
}

export function cleanTag(name: unknown): string {
  return String(name ?? "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 40);
}

/** RFC 822 date for RSS, e.g. "Sat, 17 Oct 2026 09:30:00 GMT". */
export function rfc822(iso: string): string {
  return new Date(iso).toUTCString();
}

/** RFC 6266 Content-Disposition with an ASCII fallback and a UTF-8 filename*. */
export function contentDisposition(filename: string, attachment = true): string {
  const ascii = filename.replace(/[^\x20-\x7e]/g, "_").replace(/["\\]/g, "_");
  const utf8 = encodeURIComponent(filename).replace(/['()*]/g, (c) => "%" + c.charCodeAt(0).toString(16).toUpperCase());
  return `${attachment ? "attachment" : "inline"}; filename="${ascii}"; filename*=UTF-8''${utf8}`;
}

export function safeFilename(name: string, fallback = "Untitled"): string {
  const s = name.replace(/[/\\:*?"<>|\u0000-\u001f]/g, " ").replace(/\s+/g, " ").trim().replace(/^\.+|\.+$/g, "");
  return s.slice(0, 120) || fallback;
}

export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
}

export async function readJson(request: Request, limit = 256 * 1024): Promise<Record<string, unknown>> {
  const len = Number(request.headers.get("Content-Length") || "0");
  if (len > limit) throw new HttpError(413, "JSON body too large");
  let data: unknown;
  try {
    data = await request.json();
  } catch {
    throw new HttpError(400, "Body must be JSON");
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) throw new HttpError(400, "Body must be a JSON object");
  return data as Record<string, unknown>;
}
