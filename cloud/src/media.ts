// Serving files from R2 with HTTP Range support. Podcast apps (Apple Podcasts above
// all) refuse audio that can't be fetched in byte ranges, and seeking in the web
// player needs it too.

import { Env } from "./util";

interface ServeOptions {
  contentType: string;
  /** Sets Content-Disposition. */
  disposition?: string;
  cacheControl?: string;
}

type ByteRange = { offset: number; length: number };

/**
 * Parse a Range header against the object size.
 * Returns null to serve the whole object (no header, several ranges, or a
 * unit other than bytes), "unsatisfiable" for a 416, or the single range.
 */
export function parseRange(header: string | null, size: number): ByteRange | null | "unsatisfiable" {
  if (!header) return null;
  const m = /^bytes=\s*(\d*)\s*-\s*(\d*)\s*$/i.exec(header.trim());
  if (!m) return null; // multiple ranges or another unit: RFC 9110 lets us send the whole thing
  const [, a, b] = m;
  if (a === "" && b === "") return null;
  if (a === "") {
    // Suffix: the last N bytes.
    const n = Number(b);
    if (n === 0 || size === 0) return "unsatisfiable";
    const length = Math.min(n, size);
    return { offset: size - length, length };
  }
  const start = Number(a);
  if (start >= size) return "unsatisfiable";
  const end = b === "" ? size - 1 : Math.min(Number(b), size - 1);
  if (end < start) return null; // invalid range: ignore it
  return { offset: start, length: end - start + 1 };
}

function etagMatches(header: string | null, etag: string): boolean {
  if (!header) return false;
  if (header.trim() === "*") return true;
  const strip = (s: string) => s.trim().replace(/^W\//, "");
  return header.split(",").some((t) => strip(t) === strip(etag));
}

export async function serveObject(request: Request, env: Env, key: string, opts: ServeOptions): Promise<Response> {
  const head = await env.BUCKET.head(key);
  if (!head) return new Response("Not found", { status: 404 });

  const headers = new Headers();
  headers.set("Content-Type", opts.contentType);
  headers.set("Accept-Ranges", "bytes");
  headers.set("ETag", head.httpEtag);
  headers.set("Last-Modified", head.uploaded.toUTCString());
  headers.set("Cache-Control", opts.cacheControl || "private, max-age=3600");
  if (opts.disposition) headers.set("Content-Disposition", opts.disposition);

  if (etagMatches(request.headers.get("If-None-Match"), head.httpEtag)) {
    return new Response(null, { status: 304, headers });
  }

  let range = parseRange(request.headers.get("Range"), head.size);
  // If-Range: only honour the range when the client's copy is still current.
  const ifRange = request.headers.get("If-Range");
  if (range && ifRange) {
    const current = ifRange.startsWith('"') || ifRange.startsWith("W/") ? etagMatches(ifRange, head.httpEtag) : new Date(ifRange).getTime() >= Math.floor(head.uploaded.getTime() / 1000) * 1000;
    if (!current) range = null;
  }
  if (range === "unsatisfiable") {
    headers.set("Content-Range", `bytes */${head.size}`);
    return new Response(null, { status: 416, headers });
  }

  const isHead = request.method === "HEAD";
  if (range) {
    headers.set("Content-Range", `bytes ${range.offset}-${range.offset + range.length - 1}/${head.size}`);
    headers.set("Content-Length", String(range.length));
    if (isHead) return new Response(null, { status: 206, headers });
    const obj = await env.BUCKET.get(key, { range });
    if (!obj || !("body" in obj)) return new Response("Not found", { status: 404 });
    return new Response(obj.body, { status: 206, headers });
  }

  headers.set("Content-Length", String(head.size));
  if (isHead) return new Response(null, { status: 200, headers });
  const obj = await env.BUCKET.get(key);
  if (!obj || !("body" in obj)) return new Response("Not found", { status: 404 });
  return new Response(obj.body, { status: 200, headers });
}
