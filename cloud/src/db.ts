// D1 access: schema bootstrap and item queries.

import schemaSql from "../migrations/0001_init.sql";
import { Env, nowIso } from "./util";

let schemaReady: Promise<void> | null = null;

/** Run the (idempotent) schema once per isolate. */
export function ensureSchema(env: Env): Promise<void> {
  if (!schemaReady) {
    const statements = schemaSql
      .split("\n")
      .filter((line) => !line.trim().startsWith("--"))
      .join("\n")
      .split(";")
      .map((s) => s.trim())
      .filter(Boolean);
    schemaReady = env.DB.batch(statements.map((s) => env.DB.prepare(s)))
      .then(() => undefined)
      .catch((e) => {
        schemaReady = null;
        throw e;
      });
  }
  return schemaReady;
}

export type FileKind = "audio" | "transcript" | "chapters" | "bundle";
export const FILE_KINDS: FileKind[] = ["audio", "transcript", "chapters", "bundle"];

export interface FileRow {
  item_id: string;
  kind: FileKind;
  r2_key: string;
  filename: string;
  content_type: string;
  size: number;
  etag: string;
  uploaded_at: string;
}

export interface ItemRow {
  id: string;
  title: string;
  author: string;
  template: string;
  description: string;
  speakers: string;
  meta: string;
  duration: number | null;
  status: "draft" | "published";
  created_at: string;
  updated_at: string;
  published_at: string | null;
}

export interface Speaker {
  name: string;
  voice?: string;
}

export interface Item {
  id: string;
  title: string;
  author: string;
  template: string;
  description: string;
  speakers: Speaker[];
  meta: Record<string, unknown>;
  duration: number | null;
  status: "draft" | "published";
  createdAt: string;
  updatedAt: string;
  publishedAt: string | null;
  tags: { name: string; slug: string }[];
  files: Partial<Record<FileKind, { filename: string; contentType: string; size: number; uploadedAt: string }>>;
}

function parseJson<T>(s: string, fallback: T): T {
  try {
    return JSON.parse(s) as T;
  } catch {
    return fallback;
  }
}

function assemble(row: ItemRow, tags: { slug: string; name: string }[], files: FileRow[]): Item {
  const out: Item = {
    id: row.id,
    title: row.title,
    author: row.author,
    template: row.template,
    description: row.description,
    speakers: parseJson<Speaker[]>(row.speakers, []),
    meta: parseJson<Record<string, unknown>>(row.meta, {}),
    duration: row.duration,
    status: row.status,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    publishedAt: row.published_at,
    tags: tags.map((t) => ({ name: t.name, slug: t.slug })),
    files: {},
  };
  for (const f of files) {
    out.files[f.kind] = { filename: f.filename, contentType: f.content_type, size: f.size, uploadedAt: f.uploaded_at };
  }
  return out;
}

export async function getItem(env: Env, id: string): Promise<Item | null> {
  const [items, tags, files] = await env.DB.batch([
    env.DB.prepare("SELECT * FROM items WHERE id = ?").bind(id),
    env.DB.prepare("SELECT slug, name FROM tags WHERE item_id = ? ORDER BY name COLLATE NOCASE").bind(id),
    env.DB.prepare("SELECT * FROM files WHERE item_id = ?").bind(id),
  ]);
  const row = (items.results as unknown as ItemRow[])[0];
  if (!row) return null;
  return assemble(row, tags.results as unknown as { slug: string; name: string }[], files.results as unknown as FileRow[]);
}

/** All items (optionally only published ones, optionally only those with a tag), newest first. */
export async function listItems(env: Env, opts: { publishedOnly?: boolean; tagSlug?: string } = {}): Promise<Item[]> {
  const where: string[] = [];
  const binds: string[] = [];
  if (opts.publishedOnly) where.push("i.status = 'published'");
  if (opts.tagSlug) {
    where.push("i.id IN (SELECT item_id FROM tags WHERE slug = ?)");
    binds.push(opts.tagSlug);
  }
  const clause = where.length ? "WHERE " + where.join(" AND ") : "";
  const [items, tags, files] = await env.DB.batch([
    env.DB.prepare(`SELECT i.* FROM items i ${clause} ORDER BY COALESCE(i.published_at, i.created_at) DESC`).bind(...binds),
    env.DB.prepare(`SELECT t.item_id, t.slug, t.name FROM tags t JOIN items i ON i.id = t.item_id ${clause} ORDER BY t.name COLLATE NOCASE`).bind(...binds),
    env.DB.prepare(`SELECT f.* FROM files f JOIN items i ON i.id = f.item_id ${clause}`).bind(...binds),
  ]);
  const tagsBy = new Map<string, { slug: string; name: string }[]>();
  for (const t of tags.results as unknown as { item_id: string; slug: string; name: string }[]) {
    if (!tagsBy.has(t.item_id)) tagsBy.set(t.item_id, []);
    tagsBy.get(t.item_id)!.push(t);
  }
  const filesBy = new Map<string, FileRow[]>();
  for (const f of files.results as unknown as FileRow[]) {
    if (!filesBy.has(f.item_id)) filesBy.set(f.item_id, []);
    filesBy.get(f.item_id)!.push(f);
  }
  return (items.results as unknown as ItemRow[]).map((row) => assemble(row, tagsBy.get(row.id) || [], filesBy.get(row.id) || []));
}

export async function getFile(env: Env, id: string, kind: FileKind): Promise<FileRow | null> {
  return env.DB.prepare("SELECT * FROM files WHERE item_id = ? AND kind = ?").bind(id, kind).first<FileRow>();
}

/** Tags in use, with how many published items carry each. */
export async function tagSummary(env: Env): Promise<{ slug: string; name: string; count: number; published: number }[]> {
  const res = await env.DB.prepare(
    `SELECT t.slug, MIN(t.name) AS name, COUNT(*) AS count,
            SUM(CASE WHEN i.status = 'published' THEN 1 ELSE 0 END) AS published
       FROM tags t JOIN items i ON i.id = t.item_id
      GROUP BY t.slug ORDER BY name COLLATE NOCASE`,
  ).all<{ slug: string; name: string; count: number; published: number }>();
  return res.results;
}

export async function touch(env: Env, id: string): Promise<void> {
  await env.DB.prepare("UPDATE items SET updated_at = ? WHERE id = ?").bind(nowIso(), id).run();
}
