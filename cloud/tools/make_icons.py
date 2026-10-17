#!/usr/bin/env python3
"""Draw the library's icons and podcast cover (two speech bubbles) as PNGs, stdlib only.

    python3 tools/make_icons.py            # writes public/app/icons/*.png

The output is committed, so this only needs re-running to change the artwork.
"""

import math
import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "public", "app", "icons")

BG_TOP = (0x1B, 0x26, 0x4F)
BG_BOTTOM = (0x0C, 0x12, 0x2A)
AMBER = (0xF2, 0xA5, 0x41)
TEAL = (0x3F, 0xB8, 0xAF)


def png(path, size, rows):
    raw = b"".join(b"\x00" + bytes(r) for r in rows)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
    data += chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(data)


def seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def tri_sdf(px, py, pts):
    (ax, ay), (bx, by), (cx, cy) = pts
    d = min(seg_dist(px, py, ax, ay, bx, by), seg_dist(px, py, bx, by, cx, cy), seg_dist(px, py, cx, cy, ax, ay))

    def side(x1, y1, x2, y2):
        return (px - x2) * (y1 - y2) - (x1 - x2) * (py - y2)

    s1, s2, s3 = side(ax, ay, bx, by), side(bx, by, cx, cy), side(cx, cy, ax, ay)
    inside = (s1 < 0 and s2 < 0 and s3 < 0) or (s1 > 0 and s2 > 0 and s3 > 0)
    return -d if inside else d


def bubble(cx, cy, r, tail, color, dots):
    """A speech bubble: a circle plus a triangular tail, with three dots inside."""
    return [("bubble", cx, cy, r, tail, color), *[("dot", cx + dx * r, cy, r * 0.12, None, None) for dx in dots]]


def render(size, scale=1.0):
    # Shapes in unit coordinates; scale < 1 shrinks the artwork toward the centre
    # (for maskable icons, whose outer 20% may be cropped).
    def s(v, c=0.5):
        return c + (v - c) * scale

    a_c, a_r = (s(0.40), s(0.42)), 0.235 * scale
    b_c, b_r = (s(0.62), s(0.61)), 0.215 * scale
    a_tail = [(s(0.25), s(0.56)), (s(0.37), s(0.63)), (s(0.18), s(0.76))]
    b_tail = [(s(0.76), s(0.74)), (s(0.66), s(0.79)), (s(0.84), s(0.89))]
    shapes = [
        bubble(a_c[0], a_c[1], a_r, a_tail, AMBER, (-0.56, -0.16, 0.24)),
        # A ring of background around the front bubble separates the two.
        [("ring", b_c[0], b_c[1], b_r + 0.028 * scale, [(x + (x - b_c[0]) * 0.18, y + (y - b_c[1]) * 0.18) for x, y in b_tail], None)],
        bubble(b_c[0], b_c[1], b_r, b_tail, TEAL, (-0.42, 0.0, 0.42)),
    ]
    flat = [shape for group in shapes for shape in group]
    px = 1.0 / size
    rows = []
    for j in range(size):
        y = (j + 0.5) / size
        t = j / max(1, size - 1)
        bg = tuple(round(BG_TOP[k] + (BG_BOTTOM[k] - BG_TOP[k]) * t) for k in range(3))
        row = bytearray(bg * size)
        for kind, cx, cy, r, tail, color in flat:
            ymin = cy - r - 2 * px
            ymax = cy + r + 2 * px
            if tail:
                ymin = min(ymin, min(p[1] for p in tail) - 2 * px)
                ymax = max(ymax, max(p[1] for p in tail) + 2 * px)
            if not (ymin <= y <= ymax):
                continue
            xmin = cx - r - 2 * px
            xmax = cx + r + 2 * px
            if tail:
                xmin = min(xmin, min(p[0] for p in tail) - 2 * px)
                xmax = max(xmax, max(p[0] for p in tail) + 2 * px)
            for i in range(max(0, int(xmin * size)), min(size, int(xmax * size) + 1)):
                x = (i + 0.5) / size
                d = math.hypot(x - cx, y - cy) - r
                if tail:
                    d = min(d, tri_sdf(x, y, tail))
                cover = max(0.0, min(1.0, 0.5 - d * size))
                if cover <= 0:
                    continue
                c = bg if kind in ("ring", "dot") else color
                o = i * 3
                for k in range(3):
                    row[o + k] = round(row[o + k] * (1 - cover) + c[k] * cover)
        rows.append(row)
    return rows


def main():
    os.makedirs(OUT, exist_ok=True)
    targets = [
        ("icon-192.png", 192, 1.0),
        ("icon-512.png", 512, 1.0),
        ("maskable-512.png", 512, 0.72),
        ("apple-touch-icon.png", 180, 0.86),
        ("cover.png", 1400, 0.9),
    ]
    for name, size, scale in targets:
        png(os.path.join(OUT, name), size, render(size, scale))
        print("wrote", name, file=sys.stderr)


if __name__ == "__main__":
    main()
