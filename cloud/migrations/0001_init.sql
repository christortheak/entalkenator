-- enTalkenator library index. Every statement is idempotent: the Worker also runs
-- this file on first use (src/db.ts), so deploying without migrating still works.
-- Statements are split on semicolons, so don't put one inside a statement.

-- One row per program (a rendered transcript with audio).
CREATE TABLE IF NOT EXISTS items (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  author TEXT NOT NULL DEFAULT '',
  template TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  speakers TEXT NOT NULL DEFAULT '[]',
  meta TEXT NOT NULL DEFAULT '{}',
  duration REAL,
  status TEXT NOT NULL DEFAULT 'draft',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  published_at TEXT
);

CREATE INDEX IF NOT EXISTS items_by_status ON items (status, published_at);

-- Objects in R2, one per kind: audio, transcript, chapters, bundle.
CREATE TABLE IF NOT EXISTS files (
  item_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  r2_key TEXT NOT NULL,
  filename TEXT NOT NULL,
  content_type TEXT NOT NULL,
  size INTEGER NOT NULL,
  etag TEXT NOT NULL,
  uploaded_at TEXT NOT NULL,
  PRIMARY KEY (item_id, kind)
);

-- Tags. The slug is the feed address (/feed/<token>/<slug>.xml).
CREATE TABLE IF NOT EXISTS tags (
  item_id TEXT NOT NULL,
  slug TEXT NOT NULL,
  name TEXT NOT NULL,
  PRIMARY KEY (item_id, slug)
);

CREATE INDEX IF NOT EXISTS tags_by_slug ON tags (slug);

-- Failed passphrase logins per client IP, to slow down guessing.
CREATE TABLE IF NOT EXISTS login_failures (
  ip TEXT PRIMARY KEY,
  count INTEGER NOT NULL,
  window_start INTEGER NOT NULL
);
