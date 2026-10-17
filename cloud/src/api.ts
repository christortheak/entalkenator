// The library API. Mounted twice: at /api (bearer token, for publish.py) and at
// /app/api (the web app's session). Both see the same routes.
//
//   GET    items                     list every item, drafts included
//   POST   items                     create: JSON metadata (a draft), or multipart
//                                    (metadata + files, published if it has audio)
//   GET    items/:id
//   PUT    items/:id                 create or update with a client-chosen id
//                                    (JSON metadata, or multipart like POST)
//   PATCH  items/:id                 rename, retag, re-describe, publish/unpublish
//   DELETE items/:id                 delete the item and all its files
//   PUT    items/:id/files/:kind     upload one file as the raw body
//                                    (kind: audio, transcript, chapters, bundle)
//   DELETE items/:id/files/:kind
//   POST   items/:id/publish         put the item in the feeds (needs audio)
//   GET    items/:id/transcript      turns with start/end times, for the web app
//   GET    tags                      tags with item counts
//   GET    library                   counts, storage used, feed addresses

import { FILE_KINDS, FileKind, FileRow, Item, getFile, getItem, listItems, tagSummary, touch } from "./db";
import { feedToken } from "./auth";
import { timedTurns } from "./transcript";
import { Env, HttpError, ID_RE, cleanTag, decodeSegment, json, nowIso, randomId, readJson, slugify } from "./util";

// Workers on the Free and Pro plans accept request bodies up to 100 MB.
const MAX_UPLOAD = 100 * 1000 * 1000;
const MAX_TEXT = 10 * 1000 * 1000;
const MAX_CHAPTERS = 2 * 1000 * 1000;

interface Ctx {
  env: Env;
  origin: string;
}

export async function handleApi(request: Request, env: Env, rest: string, origin: string): Promise<Response> {
  const ctx: Ctx = { env, origin };
  const parts = rest.split("/").filter(Boolean).map(decodeSegment);
  const method = request.method;

  if (parts[0] === "library" && parts.length === 1 && method === "GET") return json(await libraryInfo(ctx));
  if (parts[0] === "tags" && parts.length === 1 && method === "GET") return json({ tags: await tagSummary(env) });
  if (parts[0] !== "items") throw new HttpError(404, "No such endpoint");

  if (parts.length === 1) {
    if (method === "GET") return json({ items: await listItems(env) });
    if (method === "POST") return createOrUpdate(request, ctx, null);
    throw new HttpError(405, "Use GET or POST");
  }

  const id = parts[1];
  if (!ID_RE.test(id)) throw new HttpError(400, "Item ids are 6-64 characters: lowercase letters, digits and hyphens");

  if (parts.length === 2) {
    if (method === "GET") return json({ item: await mustGet(env, id) });
    if (method === "PUT") return createOrUpdate(request, ctx, id);
    if (method === "PATCH") return json({ item: await patchItem(env, id, await readJson(request)) });
    if (method === "DELETE") return json(await deleteItem(env, id));
    throw new HttpError(405, "Use GET, PUT, PATCH or DELETE");
  }

  if (parts[2] === "publish" && parts.length === 3 && method === "POST") return json({ item: await publish(env, id) });
  if (parts[2] === "transcript" && parts.length === 3 && method === "GET") return json(await transcriptView(env, id));

  if (parts[2] === "files" && parts.length === 4) {
    const kind = parts[3] as FileKind;
    if (!FILE_KINDS.includes(kind)) throw new HttpError(404, `File kind must be one of ${FILE_KINDS.join(", ")}`);
    if (method === "PUT") {
      await mustGet(env, id);
      const len = Number(request.headers.get("Content-Length") || "NaN");
      if (!Number.isFinite(len)) throw new HttpError(411, "Send a Content-Length header (chunked uploads can't be stored)");
      const filename = decodeHeader(request.headers.get("X-Filename")) || kind;
      const file = await storeFile(env, id, kind, request.body, request.headers.get("Content-Type") || "", filename, len);
      return json({ item: await mustGet(env, id), file });
    }
    if (method === "DELETE") {
      const row = await getFile(env, id, kind);
      if (row) {
        await env.BUCKET.delete(row.r2_key);
        await env.DB.prepare("DELETE FROM files WHERE item_id = ? AND kind = ?").bind(id, kind).run();
        if (kind === "audio") await env.DB.prepare("UPDATE items SET status = 'draft' WHERE id = ?").bind(id).run();
        await touch(env, id);
      }
      return json({ item: await mustGet(env, id) });
    }
    throw new HttpError(405, "Use PUT or DELETE");
  }
  throw new HttpError(404, "No such endpoint");
}

async function mustGet(env: Env, id: string): Promise<Item> {
  const item = await getItem(env, id);
  if (!item) throw new HttpError(404, `No item ${id}`);
  return item;
}

function decodeHeader(v: string | null): string {
  if (!v) return "";
  try {
    return decodeURIComponent(v).slice(0, 200);
  } catch {
    return v.slice(0, 200);
  }
}

// ---------------------------------------------------------------------------
// Metadata

function str(v: unknown, field: string, max: number): string | undefined {
  if (v === undefined || v === null) return undefined;
  if (typeof v !== "string") throw new HttpError(400, `${field} must be a string`);
  const s = v.trim();
  if (s.length > max) throw new HttpError(400, `${field} is longer than ${max} characters`);
  return s;
}

interface Fields {
  title?: string;
  author?: string;
  template?: string;
  description?: string;
  speakers?: string;
  meta?: string;
  duration?: number | null;
  createdAt?: string;
}

function readFields(data: Record<string, unknown>): Fields {
  const f: Fields = {
    title: str(data.title, "title", 300),
    author: str(data.author, "author", 300),
    template: str(data.template, "template", 120),
    description: str(data.description, "description", 4000),
  };
  if (f.title === "") throw new HttpError(400, "title can't be empty");
  if (data.speakers !== undefined) {
    if (!Array.isArray(data.speakers) || data.speakers.length > 30) throw new HttpError(400, "speakers must be a list (30 at most)");
    const speakers = data.speakers.map((s) => {
      const o = (typeof s === "string" ? { name: s } : s) as Record<string, unknown>;
      const name = str(o?.name, "speaker name", 120);
      if (!name) throw new HttpError(400, "each speaker needs a name");
      const voice = str(o.voice, "speaker voice", 120);
      return voice ? { name, voice } : { name };
    });
    f.speakers = JSON.stringify(speakers);
  }
  if (data.meta !== undefined) {
    if (!data.meta || typeof data.meta !== "object" || Array.isArray(data.meta)) throw new HttpError(400, "meta must be an object");
    const s = JSON.stringify(data.meta);
    if (s.length > 16000) throw new HttpError(400, "meta is too large");
    f.meta = s;
  }
  if (data.duration !== undefined) {
    if (data.duration !== null && (typeof data.duration !== "number" || !(data.duration >= 0))) throw new HttpError(400, "duration must be seconds");
    f.duration = data.duration as number | null;
  }
  if (data.createdAt !== undefined) {
    const t = typeof data.createdAt === "string" ? Date.parse(data.createdAt) : NaN;
    if (!Number.isFinite(t)) throw new HttpError(400, "createdAt must be an ISO date");
    f.createdAt = new Date(t).toISOString();
  }
  return f;
}

function readTags(v: unknown, field: string): { slug: string; name: string }[] | undefined {
  if (v === undefined) return undefined;
  const list = typeof v === "string" ? v.split(",") : v;
  if (!Array.isArray(list) || list.length > 50) throw new HttpError(400, `${field} must be a list of tags (50 at most)`);
  const out = new Map<string, string>();
  for (const raw of list) {
    const name = cleanTag(raw);
    const slug = slugify(name);
    if (name && slug && !out.has(slug)) out.set(slug, name);
  }
  return [...out].map(([slug, name]) => ({ slug, name }));
}

function tagStatements(env: Env, id: string, replace?: { slug: string; name: string }[], add?: { slug: string; name: string }[], remove?: { slug: string; name: string }[]): D1PreparedStatement[] {
  const st: D1PreparedStatement[] = [];
  if (replace) st.push(env.DB.prepare("DELETE FROM tags WHERE item_id = ?").bind(id));
  for (const t of [...(replace || []), ...(add || [])]) {
    st.push(env.DB.prepare("INSERT INTO tags (item_id, slug, name) VALUES (?, ?, ?) ON CONFLICT(item_id, slug) DO UPDATE SET name = excluded.name").bind(id, t.slug, t.name));
  }
  for (const t of remove || []) st.push(env.DB.prepare("DELETE FROM tags WHERE item_id = ? AND slug = ?").bind(id, t.slug));
  return st;
}

async function upsertMetadata(env: Env, id: string, data: Record<string, unknown>): Promise<boolean> {
  const f = readFields(data);
  const tags = readTags(data.tags, "tags");
  const exists = await env.DB.prepare("SELECT id FROM items WHERE id = ?").bind(id).first();
  const now = nowIso();
  const st: D1PreparedStatement[] = [];
  if (!exists) {
    if (!f.title) throw new HttpError(400, "A new item needs a title");
    st.push(
      env.DB.prepare(
        `INSERT INTO items (id, title, author, template, description, speakers, meta, duration, status, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)`,
      ).bind(id, f.title, f.author ?? "", f.template ?? "", f.description ?? "", f.speakers ?? "[]", f.meta ?? "{}", f.duration ?? null, f.createdAt ?? now, now),
    );
  } else {
    const sets: string[] = [];
    const binds: unknown[] = [];
    const cols: [keyof Fields, string][] = [
      ["title", "title"],
      ["author", "author"],
      ["template", "template"],
      ["description", "description"],
      ["speakers", "speakers"],
      ["meta", "meta"],
      ["duration", "duration"],
    ];
    for (const [k, col] of cols) {
      if (f[k] !== undefined) {
        sets.push(`${col} = ?`);
        binds.push(f[k]);
      }
    }
    sets.push("updated_at = ?");
    binds.push(now);
    st.push(env.DB.prepare(`UPDATE items SET ${sets.join(", ")} WHERE id = ?`).bind(...binds, id));
  }
  st.push(...tagStatements(env, id, tags));
  await env.DB.batch(st);
  return !exists;
}

async function createOrUpdate(request: Request, ctx: Ctx, givenId: string | null): Promise<Response> {
  const { env } = ctx;
  const type = request.headers.get("Content-Type") || "";
  if (type.startsWith("multipart/form-data")) return multipart(request, ctx, givenId);
  const data = await readJson(request);
  let id = givenId;
  if (!id) {
    id = typeof data.id === "string" && data.id ? data.id : randomId();
    if (!ID_RE.test(id)) throw new HttpError(400, "Item ids are 6-64 characters: lowercase letters, digits and hyphens");
    if (await env.DB.prepare("SELECT id FROM items WHERE id = ?").bind(id).first()) throw new HttpError(409, `Item ${id} already exists; use PUT /items/${id} to update it`);
  }
  const created = await upsertMetadata(env, id, data);
  let item = await mustGet(env, id);
  if (data.publish === true) item = await publish(env, id);
  return json({ item, created, uploads: Object.fromEntries(FILE_KINDS.map((k) => [k, `items/${id}/files/${k}`])) }, created ? 201 : 200);
}

/** One-request upload: a "metadata" JSON field plus file fields named after their kind. */
async function multipart(request: Request, ctx: Ctx, givenId: string | null): Promise<Response> {
  const { env } = ctx;
  let form: FormData;
  try {
    form = await request.formData();
  } catch {
    throw new HttpError(400, "Could not read the multipart body");
  }
  const rawMeta = form.get("metadata");
  let data: Record<string, unknown> = {};
  if (typeof rawMeta === "string" && rawMeta.trim()) {
    try {
      data = JSON.parse(rawMeta);
    } catch {
      throw new HttpError(400, "The metadata field must be JSON");
    }
  }
  const id = givenId || (typeof data.id === "string" && data.id ? data.id : randomId());
  if (!ID_RE.test(id)) throw new HttpError(400, "Item ids are 6-64 characters: lowercase letters, digits and hyphens");
  if (!givenId && (await env.DB.prepare("SELECT id FROM items WHERE id = ?").bind(id).first())) throw new HttpError(409, `Item ${id} already exists`);
  const created = await upsertMetadata(env, id, data);
  for (const kind of FILE_KINDS) {
    const f = form.get(kind);
    if (f && typeof f !== "string") await storeFile(env, id, kind, f, f.type, f.name || kind, f.size);
  }
  let item = await mustGet(env, id);
  if (data.publish !== false && item.files.audio) item = await publish(env, id);
  return json({ item, created }, created ? 201 : 200);
}

// ---------------------------------------------------------------------------
// Files

function resolveType(kind: FileKind, declared: string, filename: string): { contentType: string; name: string; max: number } {
  const type = declared.split(";")[0].trim().toLowerCase();
  const ext = (filename.match(/\.([A-Za-z0-9]+)$/)?.[1] || "").toLowerCase();
  switch (kind) {
    case "audio":
      if (["audio/mp4", "audio/x-m4a", "audio/m4a", "audio/aac"].includes(type) || ext === "m4a") return { contentType: "audio/mp4", name: "audio.m4a", max: MAX_UPLOAD };
      if (type === "audio/mpeg" || ext === "mp3") return { contentType: "audio/mpeg", name: "audio.mp3", max: MAX_UPLOAD };
      throw new HttpError(415, "Audio must be .m4a (audio/mp4) or .mp3 (audio/mpeg)");
    case "transcript":
      return { contentType: "text/plain; charset=utf-8", name: "transcript.txt", max: MAX_TEXT };
    case "chapters":
      return { contentType: "application/json+chapters", name: "chapters.json", max: MAX_CHAPTERS };
    case "bundle":
      return { contentType: "application/octet-stream", name: "bundle.enTalkenator", max: MAX_UPLOAD };
  }
}

async function storeFile(env: Env, id: string, kind: FileKind, body: ReadableStream | Blob | null, declaredType: string, filename: string, size: number) {
  const t = resolveType(kind, declaredType, filename);
  if (!body) throw new HttpError(400, "Empty upload");
  if (size > t.max) throw new HttpError(413, `${kind} is ${size} bytes; the limit is ${t.max}`);
  let value: ReadableStream | Blob | string = body;
  if (kind === "chapters" || kind === "transcript") {
    const s = body instanceof Blob ? await body.text() : await new Response(body).text();
    if (kind === "chapters") {
      let parsed: unknown;
      try {
        parsed = JSON.parse(s);
      } catch {
        throw new HttpError(400, "chapters must be JSON");
      }
      if (!parsed || !Array.isArray((parsed as { chapters?: unknown }).chapters)) throw new HttpError(400, "chapters needs a \"chapters\" list (Podcasting 2.0 format)");
    }
    value = s;
  }
  const key = `items/${id}/${t.name}`;
  const old = await getFile(env, id, kind);
  const obj = await env.BUCKET.put(key, value, { httpMetadata: { contentType: t.contentType } });
  if (!obj) throw new HttpError(500, "Storage refused the upload");
  if (old && old.r2_key !== key) await env.BUCKET.delete(old.r2_key);
  const now = nowIso();
  const row: FileRow = { item_id: id, kind, r2_key: key, filename, content_type: t.contentType, size: obj.size, etag: obj.httpEtag, uploaded_at: now };
  await env.DB.batch([
    env.DB.prepare(
      `INSERT INTO files (item_id, kind, r2_key, filename, content_type, size, etag, uploaded_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
       ON CONFLICT(item_id, kind) DO UPDATE SET r2_key = excluded.r2_key, filename = excluded.filename,
         content_type = excluded.content_type, size = excluded.size, etag = excluded.etag, uploaded_at = excluded.uploaded_at`,
    ).bind(row.item_id, row.kind, row.r2_key, row.filename, row.content_type, row.size, row.etag, row.uploaded_at),
    env.DB.prepare("UPDATE items SET updated_at = ? WHERE id = ?").bind(now, id),
  ]);
  return { kind, size: obj.size, contentType: t.contentType, filename };
}

// ---------------------------------------------------------------------------
// Edits

async function publish(env: Env, id: string): Promise<Item> {
  const item = await mustGet(env, id);
  if (!item.files.audio) throw new HttpError(409, "Upload the audio before publishing");
  if (item.status !== "published") {
    const now = nowIso();
    await env.DB.prepare("UPDATE items SET status = 'published', published_at = COALESCE(published_at, ?), updated_at = ? WHERE id = ?").bind(now, now, id).run();
  }
  return mustGet(env, id);
}

async function patchItem(env: Env, id: string, data: Record<string, unknown>): Promise<Item> {
  await mustGet(env, id);
  const f = readFields(data);
  const replace = readTags(data.tags, "tags");
  const add = readTags(data.addTags, "addTags");
  const remove = readTags(data.removeTags, "removeTags");
  const sets: string[] = [];
  const binds: unknown[] = [];
  for (const k of ["title", "author", "template", "description"] as const) {
    if (f[k] !== undefined) {
      sets.push(`${k} = ?`);
      binds.push(f[k]);
    }
  }
  const st: D1PreparedStatement[] = [];
  if (data.status !== undefined) {
    if (data.status === "published") {
      await publish(env, id);
    } else if (data.status === "draft") {
      sets.push("status = 'draft'");
    } else {
      throw new HttpError(400, "status must be \"draft\" or \"published\"");
    }
  }
  sets.push("updated_at = ?");
  binds.push(nowIso());
  st.push(env.DB.prepare(`UPDATE items SET ${sets.join(", ")} WHERE id = ?`).bind(...binds, id));
  st.push(...tagStatements(env, id, replace, add, remove));
  await env.DB.batch(st);
  return mustGet(env, id);
}

async function deleteItem(env: Env, id: string): Promise<{ deleted: string; objects: number }> {
  await mustGet(env, id);
  let objects = 0;
  let cursor: string | undefined;
  do {
    const page = await env.BUCKET.list({ prefix: `items/${id}/`, cursor });
    if (page.objects.length) {
      await env.BUCKET.delete(page.objects.map((o) => o.key));
      objects += page.objects.length;
    }
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor);
  await env.DB.batch([
    env.DB.prepare("DELETE FROM files WHERE item_id = ?").bind(id),
    env.DB.prepare("DELETE FROM tags WHERE item_id = ?").bind(id),
    env.DB.prepare("DELETE FROM items WHERE id = ?").bind(id),
  ]);
  return { deleted: id, objects };
}

// ---------------------------------------------------------------------------
// Views

async function transcriptView(env: Env, id: string) {
  const item = await mustGet(env, id);
  const t = await getFile(env, id, "transcript");
  if (!t) return { id, turns: [], timed: false };
  const c = await getFile(env, id, "chapters");
  const [tObj, cObj] = await Promise.all([env.BUCKET.get(t.r2_key), c ? env.BUCKET.get(c.r2_key) : Promise.resolve(null)]);
  const turns = timedTurns(tObj ? await tObj.text() : "", cObj ? await cObj.text() : null);
  return { id, duration: item.duration, timed: turns.length > 0 && turns.every((x) => x.start !== null), turns };
}

async function libraryInfo(ctx: Ctx) {
  const { env, origin } = ctx;
  const counts = await env.DB.prepare(
    `SELECT COUNT(*) AS items, SUM(CASE WHEN status = 'published' THEN 1 ELSE 0 END) AS published,
            (SELECT COALESCE(SUM(size), 0) FROM files) AS bytes FROM items`,
  ).first<{ items: number; published: number | null; bytes: number }>();
  const token = feedToken(env);
  const tags = await tagSummary(env);
  return {
    title: env.LIBRARY_TITLE || "enTalkenator Library",
    authMode: (env.AUTH_MODE || "passphrase").toLowerCase(),
    items: counts?.items ?? 0,
    published: counts?.published ?? 0,
    storageBytes: counts?.bytes ?? 0,
    appUrl: `${origin}/app/`,
    feeds: token
      ? {
          all: `${origin}/feed/${token}.xml`,
          tags: tags.map((t) => ({ name: t.name, slug: t.slug, published: t.published, url: `${origin}/feed/${token}/${t.slug}.xml` })),
        }
      : null,
    feedProblem: token ? null : "The FEED_TOKEN secret is not set (or shorter than 32 characters), so feeds are off.",
  };
}
