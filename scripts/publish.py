#!/usr/bin/env python3
"""Publish enTalkenator programs to your own library (the Cloudflare Worker in cloud/).

Commands (every one prints JSON):
  push [RUN_DIR]     upload a run's audio, transcript, chapters and bundle, and add it to
                     your podcast feeds. Re-pushing the same run updates the same item.
  info               the library's counts, storage used, and feed addresses (masked)
  list               every item in the library
  edit ID            rename, retag, or publish/unpublish an item
  delete ID --yes    delete an item and its files
  check              fetch the feed and /app from outside, as a podcast app or a browser
                     would, and report problems (feed behind Access, secrets missing...)
  status             is a library configured? (no network)
  configure          save the library address and upload token in the config file

Setting the library up (and the tokens) is library.py's job; this script uses what it saved.
The library address and upload token come from, in order:
  --url / --token-file / --token
  ENTALKENATOR_LIBRARY_URL / ENTALKENATOR_LIBRARY_TOKEN
  the config file: --config, else $ENTALKENATOR_LIBRARY_CONFIG, else
  ~/.config/entalkenator/library.json

Python 3.9+, standard library only.
"""

import argparse
import glob
import hashlib
import json
import os
import re
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

USER_AGENT = "entalkenator-publish/0.1"
MAX_UPLOAD = 100 * 1000 * 1000  # Workers Free/Pro request body limit
TURN_RE = re.compile(r"^\[([^\]\n]{1,80})\]:[ \t]?", re.M)  # as in render_audio.py


def emit(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def fail(msg, code=1):
    emit({"error": msg})
    sys.exit(code)


# ---------------------------------------------------------------------------
# Configuration


def default_config_path():
    if os.environ.get("ENTALKENATOR_LIBRARY_CONFIG"):
        return os.environ["ENTALKENATOR_LIBRARY_CONFIG"]
    return os.path.join(os.path.expanduser("~"), ".config", "entalkenator", "library.json")


def unset(value):
    """Empty, or an unsubstituted ${...} placeholder."""
    return not value or not value.strip() or value.strip().startswith("${")


def read_config(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_config(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def resolve(args):
    """(url, token, where each came from)."""
    config_path = args.config or default_config_path()
    cfg = read_config(config_path)
    url, url_from = None, None
    for value, source in ((args.url, "--url"),
                          (os.environ.get("ENTALKENATOR_LIBRARY_URL"), "ENTALKENATOR_LIBRARY_URL"),
                          (cfg.get("url"), config_path)):
        if not unset(value):
            url, url_from = value.strip().rstrip("/"), source
            break
    token, token_from = None, None
    file_token = None
    if args.token_file:
        try:
            with open(args.token_file, "r", encoding="utf-8") as f:
                file_token = f.read().strip()
        except OSError as e:
            fail("Can't read --token-file: %s" % e)
    for value, source in ((file_token, "--token-file"),
                          (args.token, "--token"),
                          (os.environ.get("ENTALKENATOR_LIBRARY_TOKEN"), "ENTALKENATOR_LIBRARY_TOKEN"),
                          (cfg.get("token"), config_path)):
        if not unset(value):
            token, token_from = value.strip(), source
            break
    if url and not re.match(r"^https?://", url):
        url = "https://" + url
    return url, token, {"url": url_from, "token": token_from, "config": config_path}


def need(args):
    url, token, sources = resolve(args)
    if not url or not token:
        missing = [n for n, v in (("the library URL", url), ("the upload token", token)) if not v]
        fail("No library configured: %s %s missing. Set one up with the setup-library skill "
             "(scripts/library.py deploy), or pass --url and --token-file."
             % (" and ".join(missing), "are" if len(missing) > 1 else "is"))
    return url, token


# ---------------------------------------------------------------------------
# HTTP


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def request(url, token, method="GET", path="", body=None, data=None, headers=None, timeout=60, retries=3):
    """JSON in, JSON out. `data` may be an open file (streamed with its Content-Length)."""
    h = {"Authorization": "Bearer " + token, "User-Agent": USER_AGENT, "Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    full = url + "/api/" + path
    delay = 2.0
    for attempt in range(retries):
        if hasattr(data, "seek"):
            data.seek(0)
        req = urllib.request.Request(full, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                raw = res.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                msg = json.loads(raw.decode("utf-8")).get("error") or raw.decode("utf-8", "replace")
            except ValueError:
                msg = raw.decode("utf-8", "replace")[:300] or e.reason
            if e.code >= 500 and e.code != 503 and attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise ApiError(e.code, "%s %s: HTTP %d: %s" % (method, "/api/" + path, e.code, msg))
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise ApiError(0, "%s %s: %s" % (method, full, getattr(e, "reason", e)))


def call(url, token, *a, **kw):
    try:
        return request(url, token, *a, **kw)
    except ApiError as e:
        hint = ""
        if e.status == 401:
            hint = " The upload token doesn't match the Worker's UPLOAD_TOKEN secret."
        elif e.status == 503:
            hint = " The Worker's secrets aren't set yet; re-run scripts/library.py deploy."
        elif e.status == 404 and "/api/library" in str(e):
            hint = " Is the library URL right?"
        fail(str(e) + hint)


# ---------------------------------------------------------------------------
# Run directory -> item


def mp4_duration(path):
    """Duration in seconds from an .m4a's mvhd box, or None."""
    try:
        with open(path, "rb") as f:
            end = os.fstat(f.fileno()).st_size

            def boxes(start, stop):
                pos = start
                while pos + 8 <= stop:
                    f.seek(pos)
                    size, kind = struct.unpack(">I4s", f.read(8))
                    head = 8
                    if size == 1:
                        size = struct.unpack(">Q", f.read(8))[0]
                        head = 16
                    elif size == 0:
                        size = stop - pos
                    if size < head:
                        return
                    yield kind, pos + head, pos + size
                    pos += size

            for kind, body, stop in boxes(0, end):
                if kind != b"moov":
                    continue
                for k2, b2, _ in boxes(body, stop):
                    if k2 == b"mvhd":
                        f.seek(b2)
                        version = f.read(1)[0]
                        f.seek(b2 + 4 + (16 if version == 1 else 8))
                        if version == 1:
                            scale, dur = struct.unpack(">IQ", f.read(12))
                        else:
                            scale, dur = struct.unpack(">II", f.read(8))
                        return dur / scale if scale else None
    except (OSError, struct.error, IndexError):
        return None
    return None


def newest(paths):
    paths = [p for p in paths if os.path.isfile(p)]
    return max(paths, key=os.path.getmtime) if paths else None


# The library refuses longer values (cloud/src/api.ts, readFields), so they're shortened here.
LIMITS = {"title": 300, "author": 300, "template": 120, "description": 4000, "speaker name": 120, "speaker voice": 120}


def fit(text, limit):
    """Shorten text to the limit at a natural break: a sentence end, then a clause, then a word."""
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    i = text[:limit + 1].rfind(". ")
    if i >= limit // 3:
        return text[:i + 1]
    head = text[:limit - 1]
    for sep in ("; ", ", ", " — ", " - ", " "):
        i = head.rfind(sep)
        if i >= limit // 3:
            return head[:i].rstrip(" ,;:—-") + "…"
    return head + "…"


def transcript_speakers(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return [], 0
    names = []
    turns = 0
    for m in TURN_RE.finditer(text):
        turns += 1
        if m.group(1).strip() not in names:
            names.append(m.group(1).strip())
    return names, turns


def gather(args):
    """Work out the files and metadata to upload."""
    run = os.path.abspath(args.run_dir) if args.run_dir else None
    st = {}
    if run:
        if not os.path.isdir(run):
            fail("Not a directory: %s" % run)
        try:
            with open(os.path.join(run, "state.json"), "r", encoding="utf-8") as f:
                st = json.load(f)
        except (OSError, ValueError):
            st = {}

    def pick(explicit, *patterns, exclude=None):
        if explicit:
            if not os.path.isfile(explicit):
                fail("File not found: %s" % explicit)
            return os.path.abspath(explicit)
        if not run:
            return None
        found = []
        for pat in patterns:
            found += glob.glob(os.path.join(glob.escape(run), pat))
        if exclude:
            found = [p for p in found if not exclude(os.path.basename(p))]
        return newest(found)

    audio = pick(args.audio, "*.m4a", "*.mp3", exclude=lambda n: n.startswith("audition-") or n.endswith(".tmp.m4a"))
    transcript = pick(args.transcript, "transcript.txt")
    chapters = args.chapters and pick(args.chapters)
    if not chapters and audio and os.path.isfile(os.path.splitext(audio)[0] + ".chapters.json"):
        chapters = os.path.splitext(audio)[0] + ".chapters.json"
    bundle = None if args.no_bundle else pick(args.bundle, "*.enTalkenator")
    if not audio:
        fail("No audio found. Render it first (render_audio.py), or pass --audio.")

    chap = {}
    if chapters:
        try:
            with open(chapters, "r", encoding="utf-8") as f:
                chap = json.load(f)
        except (OSError, ValueError) as e:
            fail("Can't read %s: %s" % (chapters, e))
    extra = chap.get("entalkenator") or {}

    names, turns = transcript_speakers(transcript) if transcript else ([], 0)
    voices = extra.get("voices") or {}
    warnings = []
    speakers = []
    for n in names:
        sp = {"name": fit(n, LIMITS["speaker name"])}
        if voices.get(n):
            # A Qwen3 voice is its description, often longer than the library keeps. The render
            # keeps the full one (it's the voice's key); the library gets a shortened label.
            sp["voice"] = fit(voices[n], LIMITS["speaker voice"])
        speakers.append(sp)
    timed = extra.get("turnTimes") if isinstance(extra.get("turnTimes"), list) else chap.get("chapters") or []
    if chapters and transcript and len(timed) != turns:
        warnings.append("The turn timings (%d) don't match the transcript's turns (%d), so the web app can't "
                        "follow along. Re-render to rebuild them." % (len(timed), turns))

    duration = extra.get("durationSeconds") or mp4_duration(audio)
    title = args.title or st.get("title") or os.path.splitext(os.path.basename(audio))[0]
    if args.id:
        item_id = args.id
    elif st.get("seed"):
        # Stable per run, so pushing the same run again updates it instead of duplicating.
        item_id = "r" + hashlib.sha256(("entalkenator-run:" + st["seed"]).encode("utf-8")).hexdigest()[:19]
    else:
        item_id = "u" + uuid.uuid4().hex[:19]
    created = st.get("finished_at") or st.get("created_at")
    meta = {k: v for k, v in {
        "form": st.get("form"),
        "mode": st.get("mode"),
        "models": {r: (m or "session") for r, m in (st.get("models") or {}).items()} or None,
        "spokenWords": st.get("total_words"),
        "loopScale": st.get("loop_scale"),
        "sourceType": st.get("source_type"),
        "language": st.get("language") or None,
        "engine": extra.get("engine"),
    }.items() if v not in (None, "", {})}

    metadata = {
        "title": fit(title, LIMITS["title"]),
        "author": fit(args.author or st.get("author") or "", LIMITS["author"]),
        "template": fit(args.template or st.get("template_name") or "", LIMITS["template"]),
        "speakers": speakers,
        "meta": meta,
    }
    if duration:
        metadata["duration"] = round(float(duration), 3)
    if created:
        metadata["createdAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(created)))
    if args.description:
        metadata["description"] = fit(args.description, LIMITS["description"])
    if args.tag:
        metadata["tags"] = args.tag
    files = [(kind, path) for kind, path in (("transcript", transcript), ("chapters", chapters), ("audio", audio), ("bundle", bundle)) if path]
    for kind, path in files:
        if os.path.getsize(path) > MAX_UPLOAD:
            fail("%s is %.1f MB; a Worker on the free plan accepts at most 100 MB per request." % (path, os.path.getsize(path) / 1e6))
    return item_id, metadata, files, warnings


CONTENT_TYPES = {".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".txt": "text/plain; charset=utf-8",
                 ".json": "application/json", ".enTalkenator": "application/octet-stream"}


def cmd_push(args):
    item_id, metadata, files, warnings = gather(args)
    plan = {"id": item_id, "metadata": metadata,
            "files": [{"kind": k, "path": p, "bytes": os.path.getsize(p)} for k, p in files],
            "publish": not args.draft, "warnings": warnings}
    if args.dry_run:
        emit(dict(plan, dryRun=True))
        return
    url, token = need(args)
    for w in warnings:
        log("warning: " + w)
    t0 = time.time()
    res = call(url, token, "PUT", "items/" + item_id, body=metadata)
    log("%s item %s" % ("created" if res.get("created") else "updating", item_id))
    uploaded = []
    for kind, path in files:
        size = os.path.getsize(path)
        ext = ".enTalkenator" if path.endswith(".enTalkenator") else os.path.splitext(path)[1].lower()
        log("  uploading %s (%.1f MB)..." % (os.path.basename(path), size / 1e6))
        with open(path, "rb") as f:
            call(url, token, "PUT", "items/%s/files/%s" % (item_id, kind), data=f,
                 headers={"Content-Type": CONTENT_TYPES.get(ext, "application/octet-stream"),
                          "Content-Length": str(size),
                          "X-Filename": urllib.parse.quote(os.path.basename(path))},
                 timeout=max(120, size / 200000))
        uploaded.append({"kind": kind, "file": os.path.basename(path), "bytes": size})
    if args.draft:
        item = call(url, token, "GET", "items/" + item_id)["item"]
    else:
        item = call(url, token, "POST", "items/%s/publish" % item_id)["item"]
    emit({
        "id": item_id,
        "title": item["title"],
        "status": item["status"],
        "inFeeds": item["status"] == "published",
        "tags": [t["name"] for t in item["tags"]],
        "duration": item.get("duration"),
        "uploaded": uploaded,
        "seconds": round(time.time() - t0, 1),
        "appUrl": "%s/app/#/item/%s" % (url, urllib.parse.quote(item_id)),
        "warnings": warnings,
    })


def mask_feeds(info, show):
    if show or not info.get("feeds"):
        return info
    mask = lambda u: re.sub(r"/feed/[^/.]+", "/feed/<FEED_TOKEN>", u)  # noqa: E731
    feeds = info["feeds"]
    info["feeds"] = {"all": mask(feeds["all"]),
                     "tags": [dict(t, url=mask(t["url"])) for t in feeds.get("tags", [])]}
    info["note"] = ("Feed addresses are masked. Open the web app's Podcast feeds page to copy them, "
                    "or re-run with --show-feed-urls.")
    return info


def cmd_info(args):
    url, token = need(args)
    emit(mask_feeds(call(url, token, "GET", "library"), args.show_feed_urls))


def cmd_list(args):
    url, token = need(args)
    items = call(url, token, "GET", "items")["items"]
    emit({"items": [{
        "id": i["id"], "title": i["title"], "template": i["template"], "status": i["status"],
        "duration": i.get("duration"), "tags": [t["name"] for t in i["tags"]],
        "files": sorted(i["files"]), "publishedAt": i.get("publishedAt"),
    } for i in items]})


def cmd_edit(args):
    url, token = need(args)
    body = {}
    if args.title:
        body["title"] = fit(args.title, LIMITS["title"])
    if args.description is not None:
        body["description"] = fit(args.description, LIMITS["description"])
    if args.tags is not None:
        body["tags"] = [t.strip() for t in args.tags.split(",") if t.strip()]
    if args.add_tag:
        body["addTags"] = args.add_tag
    if args.remove_tag:
        body["removeTags"] = args.remove_tag
    if args.publish:
        body["status"] = "published"
    if args.unpublish:
        body["status"] = "draft"
    if not body:
        fail("Nothing to change. Use --title, --tags, --add-tag, --remove-tag, --description, --publish or --unpublish.")
    item = call(url, token, "PATCH", "items/" + urllib.parse.quote(args.id), body=body)["item"]
    emit({"id": item["id"], "title": item["title"], "status": item["status"], "tags": [t["name"] for t in item["tags"]]})


def cmd_delete(args):
    if not args.yes:
        fail("This deletes the item and its files for good. Re-run with --yes to confirm.")
    url, token = need(args)
    emit(call(url, token, "DELETE", "items/" + urllib.parse.quote(args.id)))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


def fetch_plain(url):
    """GET without credentials or redirects: (status, headers, body)."""
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with opener.open(req, timeout=30) as res:
            return res.status, res.headers, res.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        return 0, {}, str(getattr(e, "reason", e)).encode()


def cmd_check(args):
    """Outside-in checks. Prints no tokens."""
    url, token = need(args)
    info = call(url, token, "GET", "library")
    problems = []
    out = {"library": url, "authMode": info["authMode"], "items": info["items"], "published": info["published"]}
    if not info.get("feeds"):
        problems.append(info.get("feedProblem") or "Feeds are off.")
    else:
        status, headers, body = fetch_plain(info["feeds"]["all"])
        location = headers.get("Location", "") if headers else ""
        feed = {"status": status, "contentType": headers.get("Content-Type") if headers else None}
        if status in (301, 302, 303, 307, 308) and "cloudflareaccess.com" in location:
            problems.append("The feed redirects to Cloudflare Access, so podcast apps can't fetch it. "
                            "Limit the Access application to the path /app (or add a Bypass application "
                            "for /feed, /media and /api).")
        elif status != 200:
            problems.append("The feed returned HTTP %s." % status)
        else:
            feed["items"] = body.count(b"<item>")
            feed["blocked"] = b"<itunes:block>Yes</itunes:block>" in body
        out["feed"] = feed
    status, headers, _ = fetch_plain(url + "/app/")
    location = headers.get("Location", "") if headers else ""
    app = {"status": status}
    if info["authMode"] == "access":
        app["behindAccess"] = status in (302, 303) and "cloudflareaccess.com" in location
        if not app["behindAccess"]:
            problems.append("AUTH_MODE is access, but no Access application sits in front of /app "
                            "(the Worker answered HTTP %s itself). Add one for the path /app." % status)
    else:
        app["signInPage"] = status == 302 and location.endswith("/app/login")
        if status == 503:
            problems.append("The APP_PASSPHRASE secret isn't set (or is shorter than 10 characters).")
        elif not app["signInPage"]:
            problems.append("/app/ didn't redirect to the sign-in page (HTTP %s)." % status)
    out["app"] = app
    out["ok"] = not problems
    out["problems"] = problems
    emit(out)


def cmd_status(args):
    url, token, sources = resolve(args)
    emit({"configured": bool(url and token), "url": url, "urlFrom": sources["url"],
          "tokenFrom": sources["token"], "configFile": sources["config"]})


def cmd_configure(args):
    path = args.config or default_config_path()
    cfg = read_config(path)
    if args.url:
        cfg["url"] = args.url.strip().rstrip("/")
    if args.token_stdin:
        token = sys.stdin.readline().strip()
        if len(token) < 32:
            fail("The upload token must be at least 32 characters.")
        cfg["token"] = token
    if not args.url and not args.token_stdin:
        fail("Pass --url and/or --token-stdin.")
    cfg["source"] = "configure"
    write_config(path, cfg)
    emit({"configFile": path, "url": cfg.get("url"), "hasToken": bool(cfg.get("token"))})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--url", help="library address, e.g. https://entalkenator-library.you.workers.dev")
    common.add_argument("--token-file", help="file holding the upload token")
    common.add_argument("--token", help="the upload token (visible to other processes; prefer --token-file or env)")
    common.add_argument("--config", help="config file (default: see above)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("push", parents=[common], help="upload a run and add it to your feeds")
    p.add_argument("run_dir", nargs="?", help="a programs/<...> run directory")
    p.add_argument("--audio")
    p.add_argument("--transcript")
    p.add_argument("--chapters")
    p.add_argument("--bundle")
    p.add_argument("--no-bundle", action="store_true", help="skip the .enTalkenator bundle (saves storage)")
    p.add_argument("--title")
    p.add_argument("--author")
    p.add_argument("--template")
    p.add_argument("--description")
    p.add_argument("--tag", action="append", help="repeatable; each tag also gets its own feed")
    p.add_argument("--id", help="item id (default: derived from the run, so re-pushing updates it)")
    p.add_argument("--draft", action="store_true", help="upload but keep it out of the feeds")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_push)

    p = sub.add_parser("info", parents=[common])
    p.add_argument("--show-feed-urls", action="store_true", help="print the feed addresses unmasked")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("list", parents=[common])
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("edit", parents=[common])
    p.add_argument("id")
    p.add_argument("--title")
    p.add_argument("--description")
    p.add_argument("--tags", help='replace all tags: "Law, Supreme Court"')
    p.add_argument("--add-tag", action="append")
    p.add_argument("--remove-tag", action="append")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--publish", action="store_true")
    g.add_argument("--unpublish", action="store_true")
    p.set_defaults(func=cmd_edit)

    p = sub.add_parser("delete", parents=[common])
    p.add_argument("id")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("check", parents=[common], help="check the feed and web app from outside")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("status", parents=[common])
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("configure", parents=[common])
    p.add_argument("--token-stdin", action="store_true", help="read the upload token from stdin")
    p.set_defaults(func=cmd_configure)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
