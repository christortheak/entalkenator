// enTalkenator library Worker.
//
//   /api/...                         upload and manage items (bearer UPLOAD_TOKEN)
//   /feed/<FEED_TOKEN>.xml           private podcast feed of every published item
//   /feed/<FEED_TOKEN>/<tag>.xml     one feed per tag
//   /media/<FEED_TOKEN>/<id>/<file>  audio (with Range), transcripts, chapters
//   /app/...                         the web library (passphrase cookie or Cloudflare Access)
//
// Every response is marked noindex and no-referrer.

import { handleApi } from "./api";
import {
  authMode,
  checkFeedToken,
  checkPassphrase,
  clearLoginFailures,
  clearSessionCookie,
  clientIp,
  hasValidSession,
  loginBlocked,
  makeSessionCookie,
  passphraseConfigured,
  recordLoginFailure,
  requireBearer,
  verifyAccess,
} from "./auth";
import { ensureSchema, getFile, getItem, tagSummary } from "./db";
import { buildFeed } from "./feed";
import { serveObject } from "./media";
import { PAGE_CSP, accessDeniedPage, loginPage, notConfiguredPage } from "./pages";
import { segments, timedTurns, toJsonTranscript, toVtt } from "./transcript";
import { Env, contentDisposition, decodeSegment, errorResponse, safeFilename, text, withPrivateHeaders } from "./util";

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    let response: Response;
    try {
      response = await route(request, env);
    } catch (e) {
      response = errorResponse(e);
    }
    return withPrivateHeaders(response);
  },
} satisfies ExportedHandler<Env>;

async function route(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const path = url.pathname;
  const origin = url.origin;

  if (path === "/robots.txt") return text("User-agent: *\nDisallow: /\n");
  if (path === "/" || path === "/app") return Response.redirect(`${origin}/app/`, 302);

  if (path.startsWith("/api/")) {
    await requireBearer(request, env);
    await ensureSchema(env);
    return handleApi(request, env, path.slice("/api/".length), origin);
  }
  if (path.startsWith("/feed/")) return feedRoute(request, env, path, origin);
  if (path.startsWith("/media/")) return mediaRoute(request, env, path, origin);
  if (path.startsWith("/app/")) return appRoute(request, env, url);
  return text("Not found", 404);
}

// ---------------------------------------------------------------------------
// Feeds and media (token in the path)

async function feedRoute(request: Request, env: Env, path: string, origin: string): Promise<Response> {
  if (request.method !== "GET" && request.method !== "HEAD") return text("Method not allowed", 405);
  const m = /^\/feed\/([^/]+?)(?:\/([^/]+))?\.xml$/.exec(path);
  // A wrong token gets the same 404 as a wrong path.
  if (!m || !(await checkFeedToken(env, decodeSegment(m[1])))) return text("Not found", 404);
  await ensureSchema(env);
  let tag: { slug: string; name: string } | null = null;
  if (m[2]) {
    const slug = decodeSegment(m[2]);
    tag = (await tagSummary(env)).find((t) => t.slug === slug) || null;
    if (!tag) return text("Not found", 404);
  }
  const xml = await buildFeed(env, origin, decodeSegment(m[1]), tag);
  const digest = await crypto.subtle.digest("SHA-1", new TextEncoder().encode(xml));
  const etag = `"${[...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("")}"`;
  const headers = { "Content-Type": "application/rss+xml; charset=utf-8", "Cache-Control": "private, no-cache", ETag: etag };
  if (request.headers.get("If-None-Match") === etag) return new Response(null, { status: 304, headers });
  return new Response(request.method === "HEAD" ? null : xml, { headers });
}

async function mediaRoute(request: Request, env: Env, path: string, origin: string): Promise<Response> {
  if (request.method !== "GET" && request.method !== "HEAD") return text("Method not allowed", 405);
  const parts = path.split("/").slice(2).map(decodeSegment); // [token, id, name] or [token, "cover.png"]
  if (!parts[0] || !(await checkFeedToken(env, parts[0]))) return text("Not found", 404);
  if (parts.length === 2 && parts[1] === "cover.png") return env.ASSETS.fetch(new Request(`${origin}/app/icons/cover.png`, { method: request.method }));
  if (parts.length !== 3) return text("Not found", 404);
  await ensureSchema(env);
  // Bundles hold the source document, so they're only offered in the signed-in web app.
  return serveItemFile(request, env, parts[1], parts[2], { allowBundle: false });
}

/** One of an item's files, by public name. */
async function serveItemFile(request: Request, env: Env, id: string, name: string, opts: { allowBundle: boolean }): Promise<Response> {
  const notFound = () => text("Not found", 404);
  switch (name) {
    case "audio.m4a":
    case "audio.mp3": {
      const f = await getFile(env, id, "audio");
      return f ? serveObject(request, env, f.r2_key, { contentType: f.content_type }) : notFound();
    }
    case "transcript.txt": {
      const f = await getFile(env, id, "transcript");
      return f ? serveObject(request, env, f.r2_key, { contentType: "text/plain; charset=utf-8" }) : notFound();
    }
    case "chapters.json": {
      const f = await getFile(env, id, "chapters");
      return f ? serveObject(request, env, f.r2_key, { contentType: "application/json+chapters" }) : notFound();
    }
    case "transcript.vtt":
    case "transcript.json": {
      const [t, c, item] = await Promise.all([getFile(env, id, "transcript"), getFile(env, id, "chapters"), getItem(env, id)]);
      if (!t || !c || !item) return notFound();
      const etag = `"${name}-${t.etag.replace(/"/g, "")}-${c.etag.replace(/"/g, "")}"`;
      const type = name.endsWith(".vtt") ? "text/vtt; charset=utf-8" : "application/json; charset=utf-8";
      const headers = { "Content-Type": type, ETag: etag, "Cache-Control": "private, max-age=3600" };
      if (request.headers.get("If-None-Match") === etag) return new Response(null, { status: 304, headers });
      const [tObj, cObj] = await Promise.all([env.BUCKET.get(t.r2_key), env.BUCKET.get(c.r2_key)]);
      if (!tObj || !cObj) return notFound();
      const segs = segments(timedTurns(await tObj.text(), await cObj.text()), item.duration);
      if (!segs) return notFound();
      const body = name.endsWith(".vtt") ? toVtt(segs) : toJsonTranscript(segs);
      return new Response(request.method === "HEAD" ? null : body, { headers });
    }
    case "bundle.enTalkenator": {
      if (!opts.allowBundle) return notFound();
      const [f, item] = await Promise.all([getFile(env, id, "bundle"), getItem(env, id)]);
      if (!f || !item) return notFound();
      return serveObject(request, env, f.r2_key, {
        contentType: "application/octet-stream",
        disposition: contentDisposition(`${safeFilename(item.title)}.enTalkenator`),
        cacheControl: "private, no-cache",
      });
    }
  }
  return notFound();
}

// ---------------------------------------------------------------------------
// Web app

async function appRoute(request: Request, env: Env, url: URL): Promise<Response> {
  const path = url.pathname;
  const mode = authMode(env);
  const secure = url.protocol === "https:";
  const title = env.LIBRARY_TITLE || "enTalkenator Library";
  const isApi = path.startsWith("/app/api/");
  const isMedia = path.startsWith("/app/media/");

  if (mode === "access") {
    const check = await verifyAccess(request, env);
    if (!check.ok) return isApi || isMedia ? jsonError(403, check.reason) : accessDeniedPage(check.reason);
    if (path === "/app/login") return Response.redirect(`${url.origin}/app/`, 302);
    if (path === "/app/logout") return Response.redirect(`${url.origin}/cdn-cgi/access/logout`, 302);
  } else {
    if (path === "/app/login") return loginRoute(request, env, url, title, secure);
    if (path === "/app/logout") {
      return new Response(null, { status: 303, headers: { Location: "/app/login", "Set-Cookie": clearSessionCookie(secure) } });
    }
    const signedIn = await hasValidSession(request, env);
    if (!signedIn) {
      if (isApi || isMedia) return jsonError(401, "Sign in first");
      if (path === "/app/" || path === "/app/index.html") {
        return passphraseConfigured(env) ? Response.redirect(`${url.origin}/app/login`, 302) : notConfiguredPage();
      }
      // Other static files (styles, script, icons, manifest) hold no library data.
    }
  }

  if (isApi) {
    if (!["GET", "HEAD"].includes(request.method)) {
      // Cross-site requests can't set this header without a CORS preflight, which
      // this Worker never approves; Sec-Fetch-Site catches the rest in modern browsers.
      const site = request.headers.get("Sec-Fetch-Site");
      if (request.headers.get("X-Requested-With") !== "entalkenator" || (site && site !== "same-origin" && site !== "none")) {
        return jsonError(403, "Cross-site request refused");
      }
    }
    await ensureSchema(env);
    return handleApi(request, env, path.slice("/app/api/".length), url.origin);
  }
  if (isMedia) {
    if (request.method !== "GET" && request.method !== "HEAD") return text("Method not allowed", 405);
    const parts = path.split("/").slice(3).map(decodeSegment);
    if (parts.length !== 2) return text("Not found", 404);
    await ensureSchema(env);
    return serveItemFile(request, env, parts[0], parts[1], { allowBundle: true });
  }

  const asset = await env.ASSETS.fetch(request);
  const out = new Response(asset.body, asset);
  if ((out.headers.get("Content-Type") || "").includes("text/html")) {
    out.headers.set("Content-Security-Policy", PAGE_CSP);
    out.headers.set("Cache-Control", "no-cache");
  }
  return out;
}

function jsonError(status: number, message: string): Response {
  return new Response(JSON.stringify({ error: message }), { status, headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" } });
}

async function loginRoute(request: Request, env: Env, url: URL, title: string, secure: boolean): Promise<Response> {
  if (!passphraseConfigured(env)) return notConfiguredPage();
  if (request.method === "GET") {
    if (await hasValidSession(request, env)) return Response.redirect(`${url.origin}/app/`, 302);
    return loginPage(title);
  }
  if (request.method !== "POST") return text("Method not allowed", 405);
  if (Number(request.headers.get("Content-Length") || "0") > 4096) return text("Too large", 413);
  await ensureSchema(env);
  const ip = clientIp(request);
  if (await loginBlocked(env, ip)) return loginPage(title, "Too many wrong attempts. Wait 15 minutes and try again.", 429);
  let passphrase = "";
  try {
    const form = await request.formData();
    passphrase = String(form.get("passphrase") || "");
  } catch {
    return loginPage(title, "Couldn't read the form.", 400);
  }
  if (!(await checkPassphrase(env, passphrase))) {
    await recordLoginFailure(env, ip);
    return loginPage(title, "That passphrase isn't right.", 401);
  }
  await clearLoginFailures(env, ip);
  return new Response(null, { status: 303, headers: { Location: "/app/", "Set-Cookie": await makeSessionCookie(env, secure) } });
}
