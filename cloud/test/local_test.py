#!/usr/bin/env python3
"""End-to-end checks against a running `wrangler dev` (local R2 and D1). Stdlib only.

    cd plugin/cloud && npx wrangler dev          # in one terminal, with .dev.vars filled in
    python3 test/local_test.py --run <entalkenator run dir with audio, chapters, bundle>

It pushes the run with scripts/publish.py, then checks the feeds (well-formed XML,
the iTunes and Podcasting 2.0 tags, tokenized URLs), byte ranges on the audio, the
generated transcripts, the upload API's auth and edits, the web app's sign-in and
CSRF guard, and deletion. The test item is deleted at the end unless --keep.
"""

import argparse
import hashlib
import http.client
import json
import os
import re
import subprocess
import sys
import urllib.parse
import uuid
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
CLOUD = os.path.dirname(HERE)
PUBLISH = os.path.join(os.path.dirname(CLOUD), "scripts", "publish.py")
NS = {
    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
    "podcast": "https://podcastindex.org/namespace/1.0",
    "atom": "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
}

passed, failed = 0, []


def check(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print("  ok   " + name)
    else:
        failed.append(name)
        print("  FAIL %s %s" % (name, detail))


def dev_vars():
    out = {}
    with open(os.path.join(CLOUD, ".dev.vars"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


class Client:
    def __init__(self, base):
        u = urllib.parse.urlparse(base)
        self.host, self.port = u.hostname, u.port or 80
        self.base = base.rstrip("/")

    def req(self, method, path, headers=None, body=None):
        if path.startswith("http"):
            u = urllib.parse.urlparse(path)
            path = u.path + ("?" + u.query if u.query else "")
        c = http.client.HTTPConnection(self.host, self.port, timeout=60)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        hdrs = {k.lower(): v for k, v in r.getheaders()}
        c.close()
        return r.status, hdrs, data


def run_publish(args, *extra):
    cmd = [sys.executable, PUBLISH] + list(extra) + ["--url", args.base, "--token-file", args.token_file]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        print(p.stdout, p.stderr)
        raise SystemExit("publish.py failed: " + " ".join(extra))
    return json.loads(p.stdout)


def private_headers(h):
    return "noindex" in h.get("x-robots-tag", "") and h.get("referrer-policy") == "no-referrer"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8787")
    ap.add_argument("--run", required=True, help="a run directory with audio, chapters, transcript and bundle")
    ap.add_argument("--keep", action="store_true", help="leave the test items in the library")
    args = ap.parse_args()
    env = dev_vars()
    feed_token, upload_token, passphrase = env["FEED_TOKEN"], env["UPLOAD_TOKEN"], env["APP_PASSPHRASE"]
    args.token_file = os.path.join(CLOUD, ".wrangler", "test-upload-token")
    os.makedirs(os.path.dirname(args.token_file), exist_ok=True)
    fd = os.open(args.token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(upload_token)
    c = Client(args.base)
    bearer = {"Authorization": "Bearer " + upload_token}

    print("publish")
    tag = "Test " + uuid.uuid4().hex[:6]
    pushed = run_publish(args, "push", args.run, "--tag", tag, "--tag", "Law")
    item_id = pushed["id"]
    check("push publishes the item", pushed["status"] == "published" and pushed["inFeeds"])
    check("push uploaded four files", sorted(u["kind"] for u in pushed["uploaded"]) == ["audio", "bundle", "chapters", "transcript"], pushed["uploaded"])
    again = run_publish(args, "push", args.run, "--tag", tag, "--tag", "Law")
    check("re-pushing a run updates the same item", again["id"] == item_id)
    audio_path = next(os.path.join(args.run, f) for f in os.listdir(args.run) if f.endswith(".m4a") and not f.startswith("audition-"))
    with open(audio_path, "rb") as f:
        audio_bytes = f.read()

    print("feed")
    st, h, body = c.req("GET", "/feed/%s.xml" % feed_token)
    check("feed 200", st == 200, st)
    check("feed content type", h.get("content-type", "").startswith("application/rss+xml"), h.get("content-type"))
    check("feed noindex + no-referrer", private_headers(h), h)
    try:
        root = ET.fromstring(body)
        check("feed is well-formed XML", True)
    except ET.ParseError as e:
        check("feed is well-formed XML", False, e)
        raise SystemExit(1)
    ch = root.find("channel")
    check("rss 2.0", root.tag == "rss" and root.get("version") == "2.0")
    for tagname in ("title", "link", "description", "language"):
        check("channel <%s>" % tagname, ch.find(tagname) is not None and (ch.find(tagname).text or "").strip() != "")
    check("itunes:block Yes", (ch.findtext("itunes:block", namespaces=NS) or "") == "Yes")
    check("podcast:block yes", (ch.findtext("podcast:block", namespaces=NS) or "") == "yes")
    check("podcast:locked yes", (ch.findtext("podcast:locked", namespaces=NS) or "") == "yes")
    check("itunes:image", ch.find("itunes:image", NS) is not None)
    check("atom self link", (ch.find("atom:link", NS).get("href") or "").endswith("/feed/%s.xml" % feed_token))
    items = [i for i in ch.findall("item") if (i.findtext("guid") or "").endswith(item_id)]
    check("item in feed", len(items) == 1)
    it = items[0]
    enc = it.find("enclosure")
    check("enclosure length matches the file", enc is not None and enc.get("length") == str(len(audio_bytes)), enc.attrib if enc is not None else None)
    check("enclosure type audio/x-m4a", enc.get("type") == "audio/x-m4a")
    enc_url = enc.get("url")
    check("enclosure URL is tokenized", "/media/%s/%s/audio.m4a" % (feed_token, item_id) in enc_url, enc_url)
    check("itunes:duration", it.findtext("itunes:duration", namespaces=NS) is not None)
    check("pubDate is RFC 822", re.match(r"^[A-Z][a-z]{2}, \d{2} [A-Z][a-z]{2} \d{4} \d{2}:\d{2}:\d{2} GMT$", it.findtext("pubDate") or "") is not None, it.findtext("pubDate"))
    transcripts = {t.get("type"): t.get("url") for t in it.findall("podcast:transcript", NS)}
    check("podcast:transcript vtt/json/txt", set(transcripts) == {"text/vtt", "application/json", "text/plain"}, transcripts)
    chapters = it.find("podcast:chapters", NS)
    check("podcast:chapters", chapters is not None and chapters.get("type") == "application/json+chapters")
    check("the episode says its voices are AI-generated", "AI-generated" in (it.findtext("description") or ""))
    check("media links carry a version marker, so replaced files get new addresses",
          "?v=" in enc.get("url", "") and "?v=" in (chapters.get("url") if chapters is not None else ""))
    check("every URL carries the feed token", all(feed_token in u for u in list(transcripts.values()) + [chapters.get("url"), enc_url]))
    image = ch.find("itunes:image", NS).get("href")
    st, h, data = c.req("GET", image)
    check("cover image 200 png", st == 200 and data[:8] == b"\x89PNG\r\n\x1a\n" and h.get("content-type") == "image/png", (st, h.get("content-type")))

    etag = h_feed = None
    st, h_feed, _ = c.req("GET", "/feed/%s.xml" % feed_token)
    etag = h_feed.get("etag")
    st, _, _ = c.req("GET", "/feed/%s.xml" % feed_token, {"If-None-Match": etag})
    check("feed If-None-Match -> 304", st == 304, st)
    slug = re.sub(r"[^a-z0-9]+", "-", tag.lower()).strip("-")
    st, _, body = c.req("GET", "/feed/%s/%s.xml" % (feed_token, slug))
    check("tag feed has the item", st == 200 and item_id.encode() in body, st)
    st, _, _ = c.req("GET", "/feed/%s/no-such-tag-%s.xml" % (feed_token, uuid.uuid4().hex[:4]))
    check("unknown tag feed 404", st == 404, st)
    st, h, _ = c.req("GET", "/feed/%s.xml" % ("x" * 43))
    check("wrong feed token 404", st == 404 and private_headers(h), st)
    st, _, _ = c.req("GET", "/media/%s/%s/audio.m4a" % ("x" * 43, item_id))
    check("wrong media token 404", st == 404, st)
    st, _, _ = c.req("GET", "/media/%s/%s/bundle.enTalkenator" % (feed_token, item_id))
    check("bundle is not served through the feed token", st == 404, st)

    print("ranges")
    size = len(audio_bytes)
    path = urllib.parse.urlparse(enc_url).path
    st, h, data = c.req("GET", path)
    check("full GET 200 with the uploaded bytes", st == 200 and hashlib.sha256(data).digest() == hashlib.sha256(audio_bytes).digest(), (st, len(data), size))
    check("Accept-Ranges: bytes", h.get("accept-ranges") == "bytes")
    check("Content-Type audio/mp4", h.get("content-type") == "audio/mp4", h.get("content-type"))
    check("media noindex + no-referrer", private_headers(h))
    audio_etag = h.get("etag")
    st, h, data = c.req("GET", path, {"Range": "bytes=0-1"})
    check("bytes=0-1 -> 206, 2 bytes", st == 206 and data == audio_bytes[:2] and h.get("content-range") == "bytes 0-1/%d" % size, (st, h.get("content-range"), len(data)))
    st, h, data = c.req("GET", path, {"Range": "bytes=100-"})
    check("bytes=100- -> 206 rest of file", st == 206 and data == audio_bytes[100:] and h.get("content-length") == str(size - 100), (st, h.get("content-range")))
    st, h, data = c.req("GET", path, {"Range": "bytes=-500"})
    check("bytes=-500 -> last 500 bytes", st == 206 and data == audio_bytes[-500:] and h.get("content-range") == "bytes %d-%d/%d" % (size - 500, size - 1, size), (st, h.get("content-range")))
    st, h, data = c.req("GET", path, {"Range": "bytes=1000-1999"})
    check("bytes=1000-1999 -> that slice", st == 206 and data == audio_bytes[1000:2000], st)
    st, h, data = c.req("GET", path, {"Range": "bytes=%d-%d" % (size - 10, size + 1000)})
    check("range past the end is clipped", st == 206 and data == audio_bytes[-10:] and h.get("content-range") == "bytes %d-%d/%d" % (size - 10, size - 1, size), (st, h.get("content-range")))
    st, h, _ = c.req("GET", path, {"Range": "bytes=%d-" % size})
    check("range starting past the end -> 416", st == 416 and h.get("content-range") == "bytes */%d" % size, (st, h.get("content-range")))
    st, h, data = c.req("GET", path, {"Range": "bytes=0-1,5-6"})
    check("multiple ranges -> whole file", st == 200 and len(data) == size, st)
    st, h, data = c.req("HEAD", path)
    check("HEAD -> 200, length, no body", st == 200 and h.get("content-length") == str(size) and data == b"", (st, h.get("content-length")))
    st, h, data = c.req("HEAD", path, {"Range": "bytes=0-99"})
    check("HEAD with Range -> 206", st == 206 and h.get("content-range") == "bytes 0-99/%d" % size, (st, h.get("content-range")))
    st, _, _ = c.req("GET", path, {"If-None-Match": audio_etag})
    check("If-None-Match -> 304", st == 304, st)
    st, h, data = c.req("GET", path, {"Range": "bytes=0-1", "If-Range": '"stale"'})
    check("If-Range mismatch -> whole file", st == 200 and len(data) == size, st)
    st, h, data = c.req("GET", path, {"Range": "bytes=0-1", "If-Range": audio_etag})
    check("If-Range match -> range", st == 206 and len(data) == 2, st)

    print("transcripts and chapters")
    st, h, vtt = c.req("GET", transcripts["text/vtt"])
    lines = vtt.decode("utf-8").splitlines()
    cues = [l for l in lines if "-->" in l]
    check("VTT 200 text/vtt", st == 200 and h.get("content-type", "").startswith("text/vtt"), (st, h.get("content-type")))
    check("VTT header and cues", lines[:1] == ["WEBVTT"] and len(cues) >= 1)
    check("VTT voice tags", any(l.startswith("<v ") for l in lines))
    times = [tuple(x.strip() for x in l.split("-->")) for l in cues]

    def secs(t):
        hh, mm, ss = t.split(":")
        return int(hh) * 3600 + int(mm) * 60 + float(ss)
    ordered = all(secs(a) < secs(b) for a, b in times) and all(secs(times[i][1]) <= secs(times[i + 1][0]) + 1e-6 for i in range(len(times) - 1))
    check("VTT cue times increase and don't overlap", ordered)
    st, h, js = c.req("GET", transcripts["application/json"])
    seg = json.loads(js)["segments"]
    check("JSON transcript segments", st == 200 and seg and all({"speaker", "startTime", "endTime", "body"} <= set(s) for s in seg))
    st, h, txt = c.req("GET", transcripts["text/plain"])
    with open(os.path.join(args.run, "transcript.txt"), "rb") as f:
        check("text transcript is the uploaded file", st == 200 and txt == f.read())
    st, h, chap = c.req("GET", chapters.get("url"))
    check("chapters 200 application/json+chapters", st == 200 and h.get("content-type", "").startswith("application/json+chapters"), h.get("content-type"))
    check("chapters JSON has chapters", len(json.loads(chap)["chapters"]) >= 1)

    print("upload API")
    st, _, _ = c.req("GET", "/api/items")
    check("no token -> 401", st == 401, st)
    st, _, _ = c.req("GET", "/api/items", {"Authorization": "Bearer " + "y" * 43})
    check("wrong token -> 401", st == 401, st)
    st, _, body = c.req("PATCH", "/api/items/" + item_id, dict(bearer, **{"Content-Type": "application/json"}), json.dumps({"title": "Renamed by test", "addTags": ["Extra tag"], "removeTags": ["Law"]}))
    item = json.loads(body).get("item", {})
    check("PATCH renames and retags", st == 200 and item.get("title") == "Renamed by test" and {t["name"] for t in item["tags"]} == {tag, "Extra tag"}, (st, item.get("title"), item.get("tags")))
    st, _, body = c.req("GET", "/feed/%s.xml" % feed_token)
    check("feed shows the new title", b"Renamed by test" in body)
    st, _, body = c.req("PATCH", "/api/items/" + item_id, dict(bearer, **{"Content-Type": "application/json"}), json.dumps({"status": "draft"}))
    st, _, body = c.req("GET", "/feed/%s.xml" % feed_token)
    check("unpublished item leaves the feed", item_id.encode() not in body)
    st, _, _ = c.req("PATCH", "/api/items/" + item_id, dict(bearer, **{"Content-Type": "application/json"}), json.dumps({"status": "published"}))
    st, _, body = c.req("GET", "/feed/%s.xml" % feed_token)
    check("republished item returns", st == 200 and item_id.encode() in body)
    st, _, body = c.req("PUT", "/api/items/BAD_ID", dict(bearer, **{"Content-Type": "application/json"}), json.dumps({"title": "x"}))
    check("bad id -> 400", st == 400, st)
    st, _, body = c.req("PUT", "/api/items/%s/files/chapters" % item_id, dict(bearer, **{"Content-Type": "application/json"}), b"not json")
    check("invalid chapters -> 400", st == 400, st)

    # One-request multipart upload (metadata + files).
    boundary = "----entalk" + uuid.uuid4().hex
    parts = []

    def field(name, value, filename=None, ctype=None):
        head = 'Content-Disposition: form-data; name="%s"' % name + ('; filename="%s"' % filename if filename else "")
        parts.append(("--%s\r\n%s\r\n%s\r\n" % (boundary, head, "Content-Type: %s\r\n" % ctype if ctype else "")).encode() + value + b"\r\n")
    field("metadata", json.dumps({"title": "Multipart test", "template": "Podcast", "tags": [tag]}).encode())
    field("audio", audio_bytes, "show.m4a", "audio/mp4")
    field("transcript", b"[Host]: Hello there.\n\n[Guest]: Hi.\n\n", "transcript.txt", "text/plain")
    mp_body = b"".join(parts) + ("--%s--\r\n" % boundary).encode()
    st, _, body = c.req("POST", "/api/items", dict(bearer, **{"Content-Type": "multipart/form-data; boundary=" + boundary}), mp_body)
    mp = json.loads(body).get("item", {})
    check("multipart POST creates and publishes", st == 201 and mp.get("status") == "published" and set(mp.get("files", {})) == {"audio", "transcript"}, (st, body[:200]))
    mp_id = mp.get("id")
    st, _, body = c.req("GET", "/feed/%s/%s.xml" % (feed_token, slug))
    check("tag feed now has both items", st == 200 and item_id.encode() in body and (mp_id or "").encode() in body)
    root = ET.fromstring(body)
    mp_item = [i for i in root.find("channel").findall("item") if (i.findtext("guid") or "").endswith(mp_id or "?")]
    check("no timed transcripts without chapters", mp_item and {t.get("type") for t in mp_item[0].findall("podcast:transcript", NS)} == {"text/plain"})

    print("web app")
    st, h, _ = c.req("GET", "/app/")
    check("/app/ signed out -> login", st == 302 and h.get("location", "").endswith("/app/login"), (st, h.get("location")))
    st, h, body = c.req("GET", "/app/login")
    check("login page noindex + CSP", st == 200 and private_headers(h) and "default-src 'self'" in h.get("content-security-policy", ""))
    form = {"Content-Type": "application/x-www-form-urlencoded", "CF-Connecting-IP": "203.0.113.%d" % (uuid.uuid4().int % 200)}
    st, h, _ = c.req("POST", "/app/login", form, urllib.parse.urlencode({"passphrase": "wrong"}))
    check("wrong passphrase -> 401", st == 401, st)
    st, h, _ = c.req("POST", "/app/login", form, urllib.parse.urlencode({"passphrase": passphrase}))
    cookie = (h.get("set-cookie") or "").split(";")[0]
    check("right passphrase -> 303 + HttpOnly cookie", st == 303 and cookie.startswith("ent_session=") and "HttpOnly" in h.get("set-cookie", ""), (st, h.get("set-cookie", "")[:40]))
    session = {"Cookie": cookie}
    st, h, body = c.req("GET", "/app/", session)
    check("/app/ signed in -> index.html", st == 200 and b'<script src="app.js"' in body and "default-src 'self'" in h.get("content-security-policy", ""), st)
    st, _, body = c.req("GET", "/app/api/items", session)
    check("app API lists items", st == 200 and item_id.encode() in body, st)
    st, _, body = c.req("GET", "/app/api/items/%s/transcript" % item_id, session)
    tv = json.loads(body)
    check("app transcript view is timed", st == 200 and tv.get("timed") and all(t["start"] is not None for t in tv["turns"]), tv.get("timed"))
    st, _, _ = c.req("GET", "/app/api/items", {"Cookie": cookie[:-3] + "AAA"})
    check("tampered cookie -> 401", st == 401, st)
    st, _, _ = c.req("PATCH", "/app/api/items/" + item_id, dict(session, **{"Content-Type": "application/json"}), json.dumps({"title": "csrf"}))
    check("app mutation without X-Requested-With -> 403", st == 403, st)
    st, _, _ = c.req("PATCH", "/app/api/items/" + item_id, dict(session, **{"Content-Type": "application/json", "X-Requested-With": "entalkenator", "Sec-Fetch-Site": "cross-site"}), json.dumps({"title": "csrf"}))
    check("app mutation from another site -> 403", st == 403, st)
    st, _, body = c.req("PATCH", "/app/api/items/" + item_id, dict(session, **{"Content-Type": "application/json", "X-Requested-With": "entalkenator", "Sec-Fetch-Site": "same-origin"}), json.dumps({"title": "Renamed in the app"}))
    check("app rename", st == 200 and json.loads(body)["item"]["title"] == "Renamed in the app", st)
    st, h, data = c.req("GET", "/app/media/%s/bundle.enTalkenator" % item_id, session)
    check("app bundle download", st == 200 and data[:2] == b"PK" and "attachment" in h.get("content-disposition", "") and ".enTalkenator" in h.get("content-disposition", ""), (st, h.get("content-disposition")))
    st, h, data = c.req("GET", "/app/media/%s/audio.m4a" % item_id, dict(session, Range="bytes=0-9"))
    check("app audio range", st == 206 and data == audio_bytes[:10], st)
    st, _, _ = c.req("GET", "/app/media/%s/audio.m4a" % item_id)
    check("app media signed out -> 401", st == 401, st)
    ip = {"Content-Type": "application/x-www-form-urlencoded", "CF-Connecting-IP": "198.51.100.%d" % (uuid.uuid4().int % 200)}
    codes = [c.req("POST", "/app/login", ip, urllib.parse.urlencode({"passphrase": "nope"}))[0] for _ in range(11)]
    check("login throttled after 10 failures", codes[:10] == [401] * 10 and codes[10] == 429, codes)
    st, _, _ = c.req("POST", "/app/login", ip, urllib.parse.urlencode({"passphrase": passphrase}))
    check("throttle blocks even the right passphrase", st == 429, st)
    st, h, _ = c.req("POST", "/app/logout", session)
    check("logout clears the cookie", st == 303 and "Max-Age=0" in h.get("set-cookie", ""), h.get("set-cookie"))
    st, h, _ = c.req("GET", "/app/app.css")
    check("static app files load signed out", st == 200)
    st, h, body = c.req("GET", "/app/manifest.webmanifest")
    check("manifest", st == 200 and json.loads(body)["start_url"] == "/app/", st)

    print("delete")
    if args.keep:
        print("  (kept %s and %s)" % (item_id, mp_id))
    else:
        out = run_publish(args, "delete", mp_id, "--yes")
        check("delete multipart item", out.get("deleted") == mp_id)
        out = run_publish(args, "delete", item_id, "--yes")
        check("delete removes four R2 objects", out.get("objects") == 4, out)
        st, _, _ = c.req("GET", path)
        check("audio gone after delete", st == 404, st)
        st, _, body = c.req("GET", "/feed/%s.xml" % feed_token)
        check("feed no longer lists it", item_id.encode() not in body)

    print("\n%d passed, %d failed" % (passed, len(failed)))
    if failed:
        print("failed: " + "; ".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
