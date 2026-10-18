#!/usr/bin/env python3
"""Embed chapters in an .m4a the way Apple's apps read them ("enhanced AAC" chapters).

Podcast apps differ in where they look for chapters. Pocket Casts and the enTalkenator library
read the feed's Podcasting 2.0 chapters file; Overcast and Apple Podcasts read chapters
embedded in the audio. Here that's a QuickTime chapter track: a disabled text track holding
one sample per chapter title, referenced from the audio track with tref/chap. The layout
follows ffmpeg's movenc.c.

  mp4chapters.py add FILE.m4a CHAPTERS.json     embed (replacing any earlier chapter track)
  mp4chapters.py show FILE.m4a                  list the embedded chapters (our own parser)
  mp4chapters.py verify FILE.m4a                list them as AVFoundation sees them (macOS)

CHAPTERS.json is render_audio.py's Podcasting 2.0 file ({"chapters": [{"startTime", "title"}]}).
Python 3.9+, standard library only.
"""

import json
import os
import shutil
import struct
import subprocess
import sys

CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"dinf", b"edts", b"udta", b"tref"}


# ---------------------------------------------------------------------------
# Atom helpers


def atoms(data, start=0, end=None):
    """[(type, start, header_size, total_size)] of the atoms between start and end."""
    end = len(data) if end is None else end
    out, pos = [], start
    while pos + 8 <= end:
        size, kind = struct.unpack(">I4s", data[pos:pos + 8])
        head = 8
        if size == 1:
            size = struct.unpack(">Q", data[pos + 8:pos + 16])[0]
            head = 16
        elif size == 0:
            size = end - pos
        if size < head or pos + size > end:
            raise ValueError("Malformed MP4 atom %r at %d" % (kind, pos))
        out.append((kind, pos, head, size))
        pos += size
    return out


def box(kind, payload):
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def child(data, parent, kind):
    for k, pos, head, size in atoms(data, parent[1] + parent[2], parent[1] + parent[3]):
        if k == kind:
            return (k, pos, head, size)
    return None


def handler_type(data, trak):
    mdia = child(data, trak, b"mdia")
    hdlr = mdia and child(data, mdia, b"hdlr")
    return data[hdlr[1] + hdlr[2] + 8:hdlr[1] + hdlr[2] + 12] if hdlr else None


def track_id(data, trak):
    tkhd = child(data, trak, b"tkhd")
    p = tkhd[1] + tkhd[2]
    version = data[p]
    return struct.unpack(">I", data[p + (20 if version == 1 else 12):p + (24 if version == 1 else 16)])[0]


# ---------------------------------------------------------------------------
# Rewriting


def shift_offsets(raw, delta):
    """A trak's bytes with every stco/co64 chunk offset moved by delta."""
    buf = bytearray(raw)

    def walk(start, end):
        for kind, pos, head, size in atoms(buf, start, end):
            if kind in CONTAINERS:
                walk(pos + head, pos + size)
            elif kind in (b"stco", b"co64"):
                p = pos + head + 4
                n = struct.unpack(">I", buf[p:p + 4])[0]
                p += 4
                width, fmt = (8, ">Q") if kind == b"co64" else (4, ">I")
                for i in range(n):
                    q = p + i * width
                    value = struct.unpack(fmt, buf[q:q + width])[0] + delta
                    if kind == b"stco" and not 0 <= value < 2 ** 32:
                        raise ValueError("chunk offset out of range for stco")
                    buf[q:q + width] = struct.pack(fmt, value)

    walk(0, len(buf))
    return bytes(buf)


def add_tref_chap(raw_trak, chapter_id):
    """The audio trak with tref/chap pointing at the chapter track (replacing any old one)."""
    out = [raw_trak[:8]]
    inserted = False
    for kind, pos, head, size in atoms(raw_trak, 8, len(raw_trak)):
        piece = raw_trak[pos:pos + size]
        if kind == b"tref":
            continue  # rebuilt below
        out.append(piece)
        if kind == b"tkhd" and not inserted:
            out.append(box(b"tref", box(b"chap", struct.pack(">I", chapter_id))))
            inserted = True
    body = b"".join(out[1:])
    return struct.pack(">I4s", 8 + len(body), b"trak") + body


def chapter_trak(new_id, chapters, total_ms, movie_timescale, movie_duration, samples_offset):
    """A disabled QuickTime text track: one sample (title) per chapter, timescale 1000."""
    starts = [int(round(c[0] * 1000)) for c in chapters]
    durations = [max(1, b - a) for a, b in zip(starts, starts[1:] + [total_ms])]
    sizes = [len(sample(c[1])) for c in chapters]
    tkhd = box(b"tkhd", struct.pack(">I", 0) + struct.pack(">IIII", 0, 0, new_id, 0) +
               struct.pack(">I", movie_duration) + b"\0" * 8 + struct.pack(">hhhh", 0, 0, 0, 0) +
               struct.pack(">9I", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000) + struct.pack(">II", 0, 0))
    mdhd = box(b"mdhd", struct.pack(">IIIII", 0, 0, 0, 1000, total_ms) + struct.pack(">HH", 0x55C4, 0))
    hdlr = box(b"hdlr", struct.pack(">II4sIII", 0, 0, b"text", 0, 0, 0) + b"Chapters\0")
    gmin = box(b"gmin", struct.pack(">IHHHHhH", 0, 0x40, 0x8000, 0x8000, 0x8000, 0, 0))
    text = box(b"text", struct.pack(">H8IH", 1, 0, 0, 0, 1, 0, 0, 0, 0x4000, 0))
    gmhd = box(b"gmhd", gmin + text)
    dinf = box(b"dinf", box(b"dref", struct.pack(">II", 0, 1) + box(b"url ", struct.pack(">I", 1))))
    # 'text' sample entry: reserved(6), data reference index 1, then the 43 bytes ffmpeg uses
    # ("required to make QT recognize the chapter track").
    entry = box(b"text", b"\0" * 6 + struct.pack(">H", 1) + bytes([0, 0, 0, 0, 0, 0, 0, 1]) + b"\0" * 35)
    stsd = box(b"stsd", struct.pack(">II", 0, 1) + entry)
    stts = box(b"stts", struct.pack(">II", 0, len(durations)) + b"".join(struct.pack(">II", 1, d) for d in durations))
    stsc = box(b"stsc", struct.pack(">II", 0, 1) + struct.pack(">III", 1, len(chapters), 1))
    stsz = box(b"stsz", struct.pack(">III", 0, 0, len(sizes)) + b"".join(struct.pack(">I", s) for s in sizes))
    stco = box(b"stco", struct.pack(">II", 0, 1) + struct.pack(">I", samples_offset))
    stbl = box(b"stbl", stsd + stts + stsc + stsz + stco)
    minf = box(b"minf", gmhd + dinf + stbl)
    return box(b"trak", tkhd + box(b"mdia", mdhd + hdlr + minf))


def sample(title):
    """A QuickTime text sample: 16-bit length, UTF-8 text, and an 'encd' atom marking UTF-8."""
    t = title.encode("utf-8")[:1000]
    return struct.pack(">H", len(t)) + t + struct.pack(">I4sI", 12, b"encd", 0x00000100)


def add_chapters(path, chapters, out=None):
    """chapters: [(start_seconds, title)], in order, the first at 0. Rewrites the file in place
    (or to out): ftyp, a new moov, then the original media data, then the chapter samples."""
    with open(path, "rb") as f:
        data = f.read()
    top = atoms(data)
    moov = next((a for a in top if a[0] == b"moov"), None)
    if not moov:
        raise ValueError("No moov atom; not an MP4/M4A file?")
    mvhd = child(data, moov, b"mvhd")
    p = mvhd[1] + mvhd[2]
    v1 = data[p] == 1
    timescale = struct.unpack(">I", data[p + (20 if v1 else 12):p + (24 if v1 else 16)])[0]
    duration = struct.unpack(">Q" if v1 else ">I", data[p + (24 if v1 else 16):p + (32 if v1 else 20)])[0]
    total_ms = int(round(duration * 1000.0 / timescale))

    traks = [a for a in atoms(data, moov[1] + moov[2], moov[1] + moov[3]) if a[0] == b"trak"]
    old_chapter_ids = set()
    for t in traks:
        tref = child(data, t, b"tref")
        chap = tref and child(data, tref, b"chap")
        if chap:
            old_chapter_ids.update(struct.unpack(">%dI" % ((chap[3] - chap[2]) // 4), data[chap[1] + chap[2]:chap[1] + chap[3]]))
    keep = [t for t in traks if track_id(data, t) not in old_chapter_ids]
    audio = next((t for t in keep if handler_type(data, t) == b"soun"), None)
    if audio is None:
        raise ValueError("No audio track")
    new_id = max(track_id(data, t) for t in traks) + 1

    chapters = [(max(0.0, float(s)), str(t)) for s, t in chapters]
    if not chapters:
        raise ValueError("No chapters")
    chapters[0] = (0.0, chapters[0][1])
    samples = b"".join(sample(t) for _, t in chapters)

    # Every top-level atom except moov keeps its order; moov goes right after ftyp (fast start).
    # Padding before the media data is dropped; anything after it stays, so all of it shifts alike.
    head = [a for a in top if a[0] == b"ftyp"]
    rest = [a for a in top if a[0] not in (b"moov", b"ftyp")]
    while rest and rest[0][0] in (b"free", b"skip", b"wide"):
        rest.pop(0)
    head_bytes = b"".join(data[a[1]:a[1] + a[3]] for a in head)

    def build(chapter_offset, delta):
        parts = []
        for kind, pos, hsz, size in atoms(data, moov[1] + moov[2], moov[1] + moov[3]):
            raw = data[pos:pos + size]
            if kind == b"mvhd":
                raw = bytearray(raw)
                q = hsz + (108 if v1 else 96)
                raw[q:q + 4] = struct.pack(">I", new_id + 1)
                raw = bytes(raw)
            elif kind == b"trak":
                tid = track_id(data, (kind, pos, hsz, size))
                if tid in old_chapter_ids:
                    continue
                raw = shift_offsets(raw, delta)
                if tid == track_id(data, audio):
                    raw = add_tref_chap(raw, new_id)
            parts.append(raw)
        parts.append(chapter_trak(new_id, chapters, total_ms, timescale, duration, chapter_offset))
        return box(b"moov", b"".join(parts))

    old_media_start = rest[0][1] if rest else len(data)
    moov_bytes = build(0, 0)
    for _ in range(2):  # the offsets don't change the moov's size, so this settles at once
        media_start = len(head_bytes) + len(moov_bytes)
        delta = media_start - old_media_start
        chapter_offset = media_start + sum(a[3] for a in rest) + 8
        moov_bytes = build(chapter_offset, delta)
    tmp = (out or path) + ".chapters.tmp"
    with open(tmp, "wb") as f:
        f.write(head_bytes)
        f.write(moov_bytes)
        for a in rest:
            f.write(data[a[1]:a[1] + a[3]])
        f.write(box(b"mdat", samples))
    os.replace(tmp, out or path)
    return {"chapters": len(chapters), "trackId": new_id}


def read_chapters(path):
    """[(start_seconds, title)] from the chapter track (our own parser, for tests)."""
    with open(path, "rb") as f:
        data = f.read()
    moov = next(a for a in atoms(data) if a[0] == b"moov")
    traks = [a for a in atoms(data, moov[1] + moov[2], moov[1] + moov[3]) if a[0] == b"trak"]
    ids = set()
    for t in traks:
        tref = child(data, t, b"tref")
        chap = tref and child(data, tref, b"chap")
        if chap:
            ids.add(struct.unpack(">I", data[chap[1] + chap[2]:chap[1] + chap[2] + 4])[0])
    for t in traks:
        if track_id(data, t) not in ids:
            continue
        mdia = child(data, t, b"mdia")
        mdhd = child(data, mdia, b"mdhd")
        ts = struct.unpack(">I", data[mdhd[1] + mdhd[2] + 12:mdhd[1] + mdhd[2] + 16])[0]
        stbl = child(data, child(data, mdia, b"minf"), b"stbl")
        stts, stsz, stco = (child(data, stbl, k) for k in (b"stts", b"stsz", b"stco"))
        n = struct.unpack(">I", data[stts[1] + stts[2] + 4:stts[1] + stts[2] + 8])[0]
        durs = []
        for i in range(n):
            c, d = struct.unpack(">II", data[stts[1] + stts[2] + 8 + 8 * i:stts[1] + stts[2] + 16 + 8 * i])
            durs += [d] * c
        count = struct.unpack(">I", data[stsz[1] + stsz[2] + 8:stsz[1] + stsz[2] + 12])[0]
        sizes = struct.unpack(">%dI" % count, data[stsz[1] + stsz[2] + 12:stsz[1] + stsz[2] + 12 + 4 * count])
        off = struct.unpack(">I", data[stco[1] + stco[2] + 8:stco[1] + stco[2] + 12])[0]
        out, t0 = [], 0
        for size, d in zip(sizes, durs):
            ln = struct.unpack(">H", data[off:off + 2])[0]
            out.append((t0 / float(ts), data[off + 2:off + 2 + ln].decode("utf-8", "replace")))
            off += size
            t0 += d
        return out
    return []


JXA_CHAPTERS = r"""
ObjC.import('Foundation');
function run(argv) {
  $.NSBundle.bundleWithPath('/System/Library/Frameworks/AVFoundation.framework').load;
  var AVURLAsset = $.NSClassFromString('AVURLAsset');
  var asset = AVURLAsset.URLAssetWithURLOptions($.NSURL.fileURLWithPath(argv[0]), $());
  var locales = asset.availableChapterLocales;
  if (locales.count == 0) { return JSON.stringify({chapters: [], playable: asset.playable}); }
  var keys = $.NSArray.arrayWithObject($('title'));
  var groups = asset.chapterMetadataGroupsWithTitleLocaleContainingItemsWithCommonKeys(locales.objectAtIndex(0), keys);
  var out = [];
  for (var i = 0; i < groups.count; i++) {
    var g = groups.objectAtIndex(i);
    var items = g.items;
    var title = items.count > 0 ? ObjC.unwrap(items.objectAtIndex(0).stringValue) : '';
    out.push(title);  // CMTime structs don't bridge to JXA; read_chapters() gives the times
  }
  return JSON.stringify({chapters: out, playable: asset.playable});
}
"""


def verify(path):
    """What AVFoundation (the framework behind Apple Podcasts and Overcast on iOS) sees."""
    if sys.platform != "darwin" or not shutil.which("osascript"):
        return None
    p = subprocess.run(["osascript", "-l", "JavaScript", "-e", JXA_CHAPTERS, os.path.abspath(path)],
                       capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        return {"error": p.stderr.strip()[-300:]}
    return json.loads(p.stdout)


def main():
    if len(sys.argv) < 3 or sys.argv[1] not in ("add", "show", "verify"):
        raise SystemExit(__doc__)
    cmd, path = sys.argv[1], sys.argv[2]
    if cmd == "add":
        with open(sys.argv[3], encoding="utf-8") as f:
            doc = json.load(f)
        chs = [(c["startTime"], c["title"]) for c in doc["chapters"]]
        print(json.dumps(add_chapters(path, chs)))
    elif cmd == "show":
        print(json.dumps(read_chapters(path), ensure_ascii=False, indent=1))
    else:
        print(json.dumps(verify(path), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
