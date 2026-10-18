#!/usr/bin/env python3
"""Turn documents into clean readings: for narration (audiobooks of papers) and as the source
for conversations. Claude does the reading; this script does the bookkeeping.

A PDF is split into page ranges, and each range goes to a reader agent (.claude/agents/
reader.md). Before the readers start, Claude surveys the document (`map` helps) and writes a
short brief, sources/<doc>/brief.md, saying how this document should sound: what to read and
leave out, how to treat citations, notes, figures and tables. Every reader follows the same
brief, so eleven readers narrate one article one way. The readers read their pages directly,
layout and images included, and write them out word for word. This script then joins the
ranges, places footnotes, builds a narration script, and checks the reading against the PDF's
own text layer.

Commands (each prints JSON):
  plan RUN --source FILE [--source FILE ...]   set up the reading; lists the reader tasks
       [--pages 1-12] [--pages-per-task 8]       (FILE may be a folder, or file.pdf:15-24)
  map RUN [--doc d1]   a page-by-page sketch from the text layer (words, opening and closing
                   words, contents-page dots, footnote-like lines) to help write the brief
  stitch RUN       join the readers' output into sources/<doc>/reading.json and reading.md,
                   and sources/combined.md (every document, for conversations)
  audit RUN        compare each reading with the PDF's text layer: how much of the reading is
                   the author's own words, and which passages may have been dropped or reworded
  script RUN [--notes after-paragraph|section-end|none] [--figures describe|skip]
                   write RUN/narration.txt: "[Narrator]: ..." turns with footnotes and figure
                   descriptions as "[Notes]: ..." turns, for render_audio.py
  package RUN --audio FILE   an .enTalkenator bundle: the PDF, the reading as its transcript,
                   and the narration as the transcript's audio (as the app names them)
  quick RUN --source FILE ...   skip the readers: plain text extraction into combined.md
  status RUN
  meta RUN DOC [--title T] [--author A]   name a document whose title page wasn't read; then stitch again
  prefs [--notes ...] [--citations ...] [--figures ...]   the person's usual narration choices

Text, Markdown, Word, RTF, HTML and OpenDocument files need no reader; they're converted to
text (macOS textutil) and used as they are. Python 3.9+, standard library only.
"""

import argparse
import datetime as _dt
import difflib
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prefs as preferences  # noqa: E402

TEXTUTIL_TYPES = {".doc", ".docx", ".rtf", ".rtfd", ".html", ".htm", ".odt", ".webarchive", ".wordml"}
TEXT_TYPES = {".txt", ".md", ".markdown", ".text"}
END_MARKER = "=== END OF TASK ==="

JXA_PAGES = r"""
ObjC.import('PDFKit');
function run(argv) {
  var doc = $.PDFDocument.alloc.initWithURL($.NSURL.fileURLWithPath(argv[0]));
  if (!doc || doc.isNil()) { return 'ERROR'; }
  var out = [];
  for (var i = 0; i < doc.pageCount; i++) {
    var s = doc.pageAtIndex(i).string;
    out.push((s && !s.isNil()) ? s.js : '');
  }
  return out.join('\f');
}
"""


# Cut pages a..b of a PDF into a new file. Claude Code's Read tool reads a small PDF whole (text and
# page images) with nothing installed, but reading selected pages of a long one needs poppler.
JXA_SPLIT = r"""
ObjC.import('PDFKit');
function run(argv) {
  var src = $.PDFDocument.alloc.initWithURL($.NSURL.fileURLWithPath(argv[0]));
  if (!src || src.isNil()) { return 'ERROR'; }
  var a = parseInt(argv[2]), b = parseInt(argv[3]);
  var out = $.PDFDocument.alloc.init;
  for (var i = a - 1; i < b; i++) { out.insertPageAtIndex(src.pageAtIndex(i).copy, out.pageCount); }
  return out.writeToFile(argv[1]) ? 'ok' : 'ERROR';
}
"""
MAX_PAGES_PER_TASK = 10  # the Read tool reads a PDF of up to 10 pages without a page range


def split_pdf(src, out, first, last):
    if sys.platform != "darwin" or not shutil.which("osascript"):
        return False
    os.makedirs(os.path.dirname(out), exist_ok=True)
    p = subprocess.run(["osascript", "-l", "JavaScript", "-e", JXA_SPLIT, src, out, str(first), str(last)],
                       capture_output=True, text=True, timeout=300)
    return p.returncode == 0 and p.stdout.strip() == "ok" and os.path.isfile(out)


def emit(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def fail(msg, code=1):
    emit({"ok": False, "error": msg})
    sys.exit(code)


def read_text(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def write_text(path, text):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def write_json(path, obj):
    write_text(path, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def sources_dir(run):
    return os.path.join(os.path.abspath(run), "sources")


def load_manifest(run):
    p = os.path.join(sources_dir(run), "manifest.json")
    if not os.path.isfile(p):
        fail("No reading set up in %s. Run `sources.py plan` first." % run)
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_manifest(run, m):
    write_json(os.path.join(sources_dir(run), "manifest.json"), m)


# ---------------------------------------------------------------------------
# Extraction (the PDF's own text layer: for the audit, quick mode, and page counts)


def pdf_pages(path):
    """Text of each page via PDFKit (macOS), else pdftotext. [] if neither works."""
    if sys.platform == "darwin" and shutil.which("osascript"):
        try:
            p = subprocess.run(["osascript", "-l", "JavaScript", "-e", JXA_PAGES, path],
                               capture_output=True, text=True, timeout=600)
            if p.returncode == 0 and p.stdout != "ERROR\n" and p.stdout.strip("\n") != "ERROR":
                return p.stdout.rstrip("\n").split("\f")
        except (OSError, subprocess.SubprocessError):
            pass
    if shutil.which("pdftotext"):
        p = subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True, text=True)
        if p.returncode == 0:
            pages = p.stdout.split("\f")
            return pages[:-1] if pages and not pages[-1].strip() else pages
    return []


def convert_to_text(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in TEXT_TYPES:
        return read_text(path)
    if ext in TEXTUTIL_TYPES and shutil.which("textutil"):
        p = subprocess.run(["textutil", "-convert", "txt", "-stdout", path], capture_output=True, text=True)
        if p.returncode == 0:
            return p.stdout
    fail("Can't read %s. Supported: PDF, text, Markdown, Word, RTF, HTML, OpenDocument." % os.path.basename(path))


def parse_pages(spec, count):
    if not spec:
        return 1, count
    m = re.match(r"^\s*(\d+)\s*(?:-\s*(\d+))?\s*$", spec)
    if not m:
        fail("--pages should look like 3-12")
    a, b = int(m.group(1)), int(m.group(2) or m.group(1))
    if not (1 <= a <= b <= count):
        fail("--pages %s is outside the document's %d pages" % (spec, count))
    return a, b


# ---------------------------------------------------------------------------
# plan


def expand_sources(paths):
    """[(file, pages or None)]: files as given ("brief.pdf:15-24" for some pages), and a
    folder's readable files, sorted by name."""
    out = []
    for p in paths:
        pages = None
        m = re.match(r"^(.*\.pdf):(\d+(?:-\d+)?)$", p, re.I)
        if m and not os.path.exists(p):
            p, pages = m.group(1), m.group(2)
        p = os.path.abspath(os.path.expanduser(p))
        if os.path.isdir(p):
            found = sorted(f for f in glob.glob(os.path.join(glob.escape(p), "*"))
                           if os.path.isfile(f) and os.path.splitext(f)[1].lower() in ({".pdf"} | TEXT_TYPES | TEXTUTIL_TYPES))
            if not found:
                fail("No readable documents in %s" % p)
            out += [(f, None) for f in found]
        else:
            out.append((p, pages))
    return out


def task_sheet(doc, task, total):
    if task.get("chunk"):
        where = ["Your pages are %d to %d of the document, cut into a PDF of their own. Read all of it with the "
                 "Read tool (no page range needed):" % (task["first"], task["last"]), task["chunk"]]
    else:
        where = ["Read pages %d to %d of this PDF with the Read tool (pages: \"%d-%d\"):"
                 % (task["first"], task["last"], task["first"], task["last"]), doc["pdf"]]
    return "\n".join([
        "READING TASK %s (%d of %d for this document)" % (task["id"], task["n"], total),
        "",
        "Document: %s, %d pages." % (doc["name"], doc["pages"]),
        "",
        "First read the brief for this document, which says how it should sound:",
        doc["brief"],
        "(If that file doesn't exist, use your own judgment toward the goal, and report your calls.)",
        "",
    ] + where + [
        "",
        "Other readers handle the pages before and after yours, so start and stop exactly at your pages.",
        "",
        "Write your reading, in the format your instructions describe, to:",
        task["output"],
        "",
        END_MARKER,
        "",
    ])


def cmd_plan(args):
    run = os.path.abspath(args.run)
    sdir = sources_dir(run)
    if os.path.exists(os.path.join(sdir, "manifest.json")) and not args.force:
        fail("%s already has a reading. Use --force to start over." % run)
    if args.pages and len(args.source) > 1:
        fail("--pages works with one source; for several, write file.pdf:15-24 for each.")
    os.makedirs(sdir, exist_ok=True)
    m = {"version": 2, "created": _dt.datetime.now().isoformat(timespec="seconds"),
         "options": {"pagesPerTask": args.pages_per_task}, "docs": []}
    tasks_out = []
    for i, (src, src_pages) in enumerate(expand_sources(args.source), 1):
        if not os.path.isfile(src):
            fail("Not found: %s" % src)
        did = "d%d" % i
        ddir = os.path.join(sdir, did)
        os.makedirs(ddir, exist_ok=True)
        name = os.path.basename(src)
        doc = {"id": did, "source": src, "name": name, "title": None, "author": None}
        if src.lower().endswith(".pdf"):
            pdf = os.path.join(ddir, "original.pdf")
            shutil.copyfile(src, pdf)
            pages = pdf_pages(pdf)
            if not pages:
                fail("Couldn't open %s as a PDF." % name)
            for n, text in enumerate(pages, 1):
                write_text(os.path.join(ddir, "pages", "%04d.txt" % n), text)
            first, last = parse_pages(src_pages or args.pages, len(pages))
            layer = sum(len(p.split()) for p in pages[first - 1:last])
            doc.update({"type": "pdf", "pdf": pdf, "pages": len(pages), "range": [first, last],
                        "textLayerWords": layer, "scanned": layer < 20 * (last - first + 1),
                        "brief": os.path.join(ddir, "brief.md"), "tasks": []})
            os.makedirs(os.path.join(ddir, "read"), exist_ok=True)
            per = max(1, min(MAX_PAGES_PER_TASK, args.pages_per_task))
            starts = list(range(first, last + 1, per))
            for n, a in enumerate(starts, 1):
                b = min(a + per - 1, last)
                t = {"id": "%s-%02d" % (did, n), "n": n, "first": a, "last": b,
                     "sheet": os.path.join(ddir, "tasks", "%02d-p%03d-%03d.md" % (n, a, b)),
                     "output": os.path.join(ddir, "read", "%02d-p%03d-%03d.txt" % (n, a, b))}
                chunk = os.path.join(ddir, "chunks", "%02d-p%03d-%03d.pdf" % (n, a, b))
                if split_pdf(pdf, chunk, a, b):
                    t["chunk"] = chunk
                doc["tasks"].append(t)
            for t in doc["tasks"]:
                write_text(t["sheet"], task_sheet(doc, t, len(doc["tasks"])))
                tasks_out.append({"doc": did, "task": t["id"], "pages": "%d-%d" % (t["first"], t["last"]),
                                  "sheet": t["sheet"], "output": t["output"]})
        else:
            text = convert_to_text(src)
            write_text(os.path.join(ddir, "text.txt"), text)
            doc.update({"type": "text", "words": len(text.split()), "tasks": []})
        m["docs"].append(doc)
    save_manifest(run, m)
    emit({"ok": True, "run": run, "docs": [{k: d.get(k) for k in ("id", "name", "type", "pages", "range", "scanned", "words", "brief")}
                                          for d in m["docs"]],
          "tasks": tasks_out,
          "note": "Survey each PDF (`sources.py map`, and read a chunk or two) and write its brief; then spawn one "
                  "reader agent per task (subagent_type reader), several at a time; then `sources.py stitch`."
                  if tasks_out else "No PDFs, so no readers are needed; run `sources.py stitch`."})


def cmd_map(args):
    """A cheap sketch of each page from the text layer, for writing the brief. Hints, not decisions."""
    run = os.path.abspath(args.run)
    m = load_manifest(run)
    out = []
    for doc in m["docs"]:
        if doc["type"] != "pdf" or (args.doc and args.doc not in (doc["id"], doc["name"])):
            continue
        ddir = os.path.join(sources_dir(run), doc["id"])
        rows = []
        for n in range(doc["range"][0], doc["range"][1] + 1):
            text = read_text(os.path.join(ddir, "pages", "%04d.txt" % n))
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            flat = " ".join(text.split())
            rows.append({"page": n, "words": len(flat.split()),
                         "starts": flat[:90], "ends": flat[-60:] if len(flat) > 90 else "",
                         "dotLeaders": len(re.findall(r"\.{6,}", text)),
                         "numberedLines": sum(1 for l in lines if re.match(r"^\d{1,4}\s+\S", l))})
        tasks = [{"task": t["id"], "pages": "%d-%d" % (t["first"], t["last"]), "chunk": t.get("chunk")} for t in doc["tasks"]]
        out.append({"doc": doc["id"], "name": doc["name"], "pages": doc["pages"], "range": doc["range"],
                    "scanned": doc.get("scanned"), "brief": doc["brief"], "briefWritten": os.path.isfile(doc["brief"]),
                    "tasks": tasks, "map": rows})
    emit({"docs": out, "note": "dotLeaders suggest contents pages; numberedLines at a page's foot suggest footnotes "
                               "or a reference list. Look at the pages themselves (the chunk PDFs) where it matters."})


# ---------------------------------------------------------------------------
# Reader output: parse and stitch

NOTE_RE = re.compile(r"^@note\s+(\S+)\s+(substantive|citation)\s*(?:\|\s*(.*))?$", re.I)
NOTECONT_RE = re.compile(r"^@note-continues\s+(substantive|citation)?\s*(?:\|\s*(.*))?$", re.I)
MARK_RE = re.compile(r"\[\^([^\]\s]{1,8})\]")


def parse_reading(text):
    """Blocks from one reader's output."""
    blocks, para = [], []
    flags = {"continues": False, "incomplete": False}
    meta = {}

    def flush():
        if para:
            blocks.append({"type": "para", "text": " ".join(" ".join(para).split())})
            para.clear()

    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line:
            flush()
            continue
        low = line.lower()
        if low == "@continues":
            flags["continues"] = True
        elif low == "@incomplete":
            flush()
            flags["incomplete"] = True
        elif low.startswith("@title "):
            meta["title"] = line[7:].strip()
        elif low.startswith("@author "):
            meta["author"] = line[8:].strip()
        elif low.startswith("@intro "):
            meta["intro"] = line[7:].strip()
        elif low.startswith("@skipped"):
            flush()
            blocks.append({"type": "skipped", "text": line[8:].strip(" :")})
        elif low.startswith("@figure") or low.startswith("@table"):
            flush()
            kind = "figure" if low.startswith("@figure") else "table"
            blocks.append({"type": kind, "text": line[len("@" + kind):].strip(" :|")})
        elif NOTECONT_RE.match(line):
            flush()
            mm = NOTECONT_RE.match(line)
            blocks.append({"type": "notecont", "cls": (mm.group(1) or "").lower() or None, "text": (mm.group(2) or "").strip()})
        elif low.startswith("@note"):
            flush()
            mm = NOTE_RE.match(line)
            if mm:
                blocks.append({"type": "note", "n": mm.group(1), "cls": mm.group(2).lower(), "text": (mm.group(3) or "").strip()})
            else:
                blocks.append({"type": "bad", "text": line})
        elif re.match(r"^#{1,4}\s+\S", line):
            flush()
            level = len(line) - len(line.lstrip("#"))
            blocks.append({"type": "heading", "level": level, "text": line.lstrip("#").strip()})
        elif line.startswith("@"):
            flush()
            blocks.append({"type": "bad", "text": line})
        else:
            para.append(line)
    flush()
    return blocks, flags, meta


def join_paragraphs(a, b):
    if re.search(r"[A-Za-z]-$", a) and re.match(r"^[a-z]", b):
        return a[:-1] + b
    return a + " " + b


def stitch_doc(doc):
    """(blocks, notes, problems) for one PDF document."""
    blocks, notes, problems = [], {}, []
    last_note = None
    prev_incomplete = False
    for t in doc["tasks"]:
        if not os.path.isfile(t["output"]):
            problems.append({"task": t["id"], "problem": "missing", "pages": "%d-%d" % (t["first"], t["last"])})
            prev_incomplete = False
            continue
        bl, flags, meta = parse_reading(read_text(t["output"]))
        for k in ("title", "author", "intro"):
            if meta.get(k) and not (doc.get(k) and doc.get("metaSet")):
                doc[k] = meta[k]
        bad = [b["text"] for b in bl if b["type"] == "bad"]
        if bad:
            problems.append({"task": t["id"], "problem": "unrecognized lines", "lines": bad[:5]})
        merge = (flags["continues"] or prev_incomplete) and blocks and blocks[-1]["type"] == "para"
        for b in bl:
            if b["type"] == "bad":
                continue
            b["task"] = t["id"]
            if b["type"] == "note":
                key = b["n"]
                if key in notes:  # numbering restarted (book chapters): keep both
                    key = "%s@%s" % (b["n"], t["id"])
                b["key"] = key
                notes[key] = {"n": b["n"], "cls": b["cls"], "text": b["text"], "task": t["id"]}
                last_note = key
                continue
            if b["type"] == "notecont":
                if last_note:
                    note = notes[last_note]
                    note["text"] = (note["text"] + " " + b["text"]).strip()
                    if b["cls"] == "substantive":
                        note["cls"] = "substantive"
                continue
            if merge and b["type"] == "para":
                blocks[-1]["text"] = join_paragraphs(blocks[-1]["text"], b["text"])
                blocks[-1]["parts"].append([t["id"], b["text"]])
                merge = False
                continue
            merge = False
            if b["type"] == "para":
                b["parts"] = [[t["id"], b["text"]]]
            blocks.append(b)
        prev_incomplete = flags["incomplete"]
    # Which notes are cited where.
    cited = set()
    for b in blocks:
        if b["type"] == "para":
            refs = []
            for n in MARK_RE.findall(b["text"]):
                key = n if n in notes else next((k for k in notes if k.startswith(n + "@") and k not in cited), None)
                if key and key not in cited:
                    refs.append(key)
                    cited.add(key)
            b["notes"] = refs
    orphans = [k for k, v in notes.items() if k not in cited and v["cls"] == "substantive"]
    if orphans:
        problems.append({"problem": "substantive notes never cited in the text (placed at their page instead)", "notes": orphans[:10]})
        # Attach an orphan to the last paragraph from its own task.
        for k in orphans:
            for b in reversed(blocks):
                if b["type"] == "para" and b["task"] == notes[k]["task"]:
                    b.setdefault("notes", []).append(k)
                    break
    return blocks, notes, problems


def reading_markdown(doc, blocks, notes, with_notes=True):
    out = []
    for b in blocks:
        if b["type"] == "heading":
            out.append("#" * b["level"] + " " + b["text"])
        elif b["type"] == "para":
            out.append(MARK_RE.sub("", b["text"]).replace("  ", " ").strip())
            if with_notes:
                for k in b.get("notes", []):
                    nt = notes[k]
                    if nt["cls"] == "substantive" and nt["text"]:
                        out.append("[Footnote %s: %s]" % (nt["n"], nt["text"]))
        elif b["type"] in ("figure", "table"):
            out.append("[%s: %s]" % (b["type"].capitalize(), b["text"]))
    return "\n\n".join(out) + "\n"


def doc_label(doc):
    title = doc.get("title") or os.path.splitext(doc["name"])[0]
    return "%s%s" % (title, (" by " + doc["author"]) if doc.get("author") else "")


def cmd_stitch(args):
    run = os.path.abspath(args.run)
    m = load_manifest(run)
    report, problems, parts = [], [], []
    for doc in m["docs"]:
        ddir = os.path.join(sources_dir(run), doc["id"])
        if doc["type"] == "pdf":
            blocks, notes, probs = stitch_doc(doc)
            for p in probs:
                p["doc"] = doc["id"]
            problems += probs
            write_json(os.path.join(ddir, "reading.json"), {"title": doc.get("title"), "author": doc.get("author"),
                                                            "intro": doc.get("intro"), "blocks": blocks, "notes": notes})
            md = reading_markdown(doc, blocks, notes)
            write_text(os.path.join(ddir, "reading.md"), md)
            body = sum(len(MARK_RE.sub("", b["text"]).split()) for b in blocks if b["type"] == "para")
            report.append({"doc": doc["id"], "name": doc["name"], "title": doc.get("title"), "author": doc.get("author"),
                           "bodyWords": body, "headings": sum(b["type"] == "heading" for b in blocks),
                           "figures": sum(b["type"] in ("figure", "table") for b in blocks),
                           "notes": {"substantive": sum(n["cls"] == "substantive" for n in notes.values()),
                                     "citation": sum(n["cls"] == "citation" for n in notes.values())},
                           "skipped": [b["text"] for b in blocks if b["type"] == "skipped"]})
        else:
            md = read_text(os.path.join(ddir, "text.txt"))
            write_text(os.path.join(ddir, "reading.md"), md)
            report.append({"doc": doc["id"], "name": doc["name"], "bodyWords": len(md.split())})
        parts.append((doc, md))
    save_manifest(run, m)
    # One file with every document, labelled the way the app's Topics label them.
    if len(parts) == 1:
        combined = parts[0][1]
    else:
        combined = "".join("What follows is the text entitled %s:\n\n%s\n[End of the text entitled %s]\n\n"
                           % (doc_label(d), md.strip(), doc_label(d)) for d, md in parts)
    write_text(os.path.join(sources_dir(run), "combined.md"), combined)
    emit({"ok": not any(p.get("problem") == "missing" for p in problems), "docs": report, "problems": problems,
          "combined": os.path.join(sources_dir(run), "combined.md"), "combinedWords": len(combined.split())})


# ---------------------------------------------------------------------------
# audit: the reading against the PDF's own words

SPOKEN = [(r"§§", " sections "), (r"§", " section "), (r"¶", " paragraph "), (r"\be\.g\.,?", " for example "),
          (r"\bi\.e\.,?", " that is "), (r"\bet al\.", " and others "), (r"\bcf\.", " compare "),
          (r"&", " and "), (r"±", " plus or minus "), (r"[×✕]", " by "), (r"[~≈∼]", " approximately "),
          (r"%", " percent "), (r"°\s?C\b", " degrees celsius "), (r"°", " degrees "), (r"≤", " less than or equal to "),
          (r"≥", " greater than or equal to "), (r"<", " less than "), (r">", " greater than "), (r"=", " equals ")]
# Units read aloud, canonicalized on both sides: the reader says "micrometers", the page says "μm".
UNITS = {}
for canon, forms in {"um": "μm µm um micrometer micrometers micrometre micrometres micron microns",
                     "nm": "nm nanometer nanometers nanometre nanometres", "mm": "mm millimeter millimeters millimetre millimetres",
                     "cm": "cm centimeter centimeters centimetre centimetres", "mw": "mw milliwatt milliwatts",
                     "ghz": "ghz gigahertz", "mhz": "mhz megahertz", "khz": "khz kilohertz", "hz": "hz hertz",
                     "ms": "ms millisecond milliseconds", "us": "μs µs microsecond microseconds",
                     "ns": "ns nanosecond nanoseconds", "ml": "ml milliliter milliliters millilitre millilitres",
                     "ul": "μl µl ul microliter microliters microlitre microlitres", "kda": "kda kilodalton kilodaltons",
                     "mg": "mg milligram milligrams", "ug": "μg µg ug microgram micrograms", "kg": "kg kilogram kilograms"}.items():
    for f in forms.split():
        UNITS[f] = canon
AFTER_NUMBER = {"h": "h", "hr": "h", "hrs": "h", "hour": "h", "hours": "h", "min": "min", "mins": "min", "minute": "min",
                "minutes": "min", "s": "s", "sec": "s", "second": "s", "seconds": "s", "d": "d", "day": "d", "days": "d",
                "w": "w", "watt": "w", "watts": "w", "m": "m", "meter": "m", "meters": "m", "metre": "m", "metres": "m"}
LIGATURES = ("ffi", "ffl", "ff", "fi", "fl")


def norm_words(text, spoken=True):
    t = text.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    t = t.replace("\u00ad", "")
    t = re.sub(r"(\w)-\n\s*(\w)", r"\1\2", t)  # line-end hyphenation in the text layer
    t = MARK_RE.sub(" ", t)
    if spoken:
        for pat, rep in SPOKEN:
            t = re.sub(pat, rep, t, flags=re.I)
    t = t.lower()
    t = re.sub(r"[\u2010-\u2015\-/]", " ", t)
    t = re.sub(r"[^\w\s']", " ", t)
    t = re.sub(r"\b([a-z]{3,})(\d{1,3})\b", r"\1 \2", t)  # reference numbers fused to a word: "cameras43"
    words = [w.strip("'") for w in t.split() if w.strip("'")]
    out = []
    for w in words:
        if w in UNITS:
            w = UNITS[w]
        elif out and out[-1].isdigit() and w in AFTER_NUMBER:
            w = AFTER_NUMBER[w]
        out.append(w)
    return out


def segment(token, vocab, longest=30):
    """Split a run-together token ("microscopeequippedwithahalogen") into words the reading uses,
    with as few words as possible; None if it can't be done."""
    n = len(token)
    if n > 200:
        return None
    best = [None] * (n + 1)
    best[0] = []
    for i in range(1, n + 1):
        for j in range(max(0, i - longest), i):
            if best[j] is not None and token[j:i] in vocab and (best[i] is None or len(best[j]) + 1 < len(best[i])):
                best[i] = best[j] + [token[j:i]]
    return best[n] if best[n] and len(best[n]) > 1 else None


def repair_ligatures(src, vocab):
    """Repair a PDF text layer against the reading's own vocabulary. Text layers often drop the
    fi/fl/ff ligatures ("speci city", "uorescence") and the spaces of justified lines
    ("theneedformore"). Re-insert a ligature, or split a run, where that makes words the reading uses."""
    out, i = [], 0
    while i < len(src):
        w = src[i]
        if w not in vocab:
            nxt = src[i + 1] if i + 1 < len(src) else None
            fixed = None
            for lig in LIGATURES:
                if nxt is not None and w + lig + nxt in vocab:
                    fixed, i = w + lig + nxt, i + 1
                    break
                inner = [w[:k] + lig + w[k:] for k in range(len(w) + 1)]  # also "brighteld", "uorescence"
                hit = next((x for x in inner if x in vocab), None)
                if hit:
                    fixed = hit
                    break
            if not fixed and len(w) > 6:
                # Run-together words, possibly with a lost ligature at the join to the next token.
                for cand, extra in ([(w, 0)] + ([(w + lig + nxt, 1) for lig in LIGATURES] if nxt else [])):
                    parts = segment(cand, vocab)
                    if parts:
                        out.extend(parts)
                        i += 1 + extra
                        break
                else:
                    out.append(w)
                    i += 1
                continue
            if fixed:
                out.append(fixed)
                i += 1
                continue
        out.append(w)
        i += 1
    return out


def spans(words, opcodes, side, min_len, bridge=3):
    """Unmatched stretches on one side, at least min_len words long. A run of `bridge` or fewer
    matching words inside a stretch ("for", "the") doesn't split it."""
    merged = []
    for tag, i1, i2, j1, j2 in opcodes:
        a, b = (i1, i2) if side == "src" else (j1, j2)
        if tag == "equal":
            if merged and b - a <= bridge and merged[-1][2] == "open":
                merged[-1][1] = b
            elif merged:
                merged[-1][2] = "closed"
            continue
        if b <= a:
            continue
        if merged and merged[-1][2] == "open":
            merged[-1][1] = b
        else:
            merged.append([a, b, "open"])
    return [(a, b) for a, b, _ in merged if b - a >= min_len]


def task_text(blocks, task_id):
    """The reading's words that came from one task's pages (a paragraph can span two tasks)."""
    out = []
    for b in blocks:
        if b["type"] == "heading" and b.get("task") == task_id:
            out.append(b["text"])
        elif b["type"] == "para":
            out += [text for tid, text in b.get("parts", [[b.get("task"), b["text"]]]) if tid == task_id]
    return " ".join(out)


def audit_task(src_text, nar_text, note_texts, title_words=(), page_starts=None, skipped=()):
    """page_starts: [(page number, offset in src words)] so missing text can be traced to a page
    the reader said it skipped."""
    nar = norm_words(nar_text)
    vocab = set(nar)
    if page_starts:
        src, starts = [], []
        for page, text in page_starts:
            starts.append((page, len(src)))
            src += repair_ligatures(norm_words(text), vocab)
    else:
        src, starts = repair_ligatures(norm_words(src_text), vocab), []
    if not nar:
        return {"readWords": 0, "sourceWords": len(src)}
    sm = difflib.SequenceMatcher(None, src, nar, autojunk=False)
    ops = sm.get_opcodes()
    matched = sum(i2 - i1 for tag, i1, i2, j1, j2 in ops if tag == "equal")
    added = [" ".join(nar[a:b]) for a, b in spans(nar, ops, "nar", 6)]
    note_words = set(norm_words(" ".join(note_texts), spoken=False))
    title_words = set(title_words)
    dropped = []
    for a, b in spans(src, ops, "src", 12):
        piece = src[a:b]
        content = [w for w in piece if len(w) >= 4 and not w.isdigit()] or piece
        overlap = sum(w in note_words for w in content) / float(len(content))
        excerpt = " ".join(piece[:24])
        page = max([p for p, off in starts if off <= a] or [None])
        said = [x for x in skipped if page is not None and re.search(r"\b%d\b" % page, x)]
        if title_words and sum(w in title_words for w in piece) / float(len(piece)) > 0.5:
            kind = "title page"
        elif any(w in ("fig", "figure", "table") for w in piece[:12]):
            kind = "figure caption or labels"
        elif said:
            kind = "skipped by the reader: " + "; ".join(said)
        elif overlap > 0.5 or re.match(r"^\d+\s", " ".join(piece)):
            kind = "footnotes"
        elif _looks_citation(piece):
            kind = "citations"
        else:
            kind = "possibly dropped"
        dropped.append({"kind": kind, "words": b - a, "page": page, "excerpt": excerpt + (" …" if b - a > 24 else "")})
    return {"readWords": len(nar), "sourceWords": len(src), "matchedWords": matched,
            "faithfulness": round(matched / float(len(nar)), 3),
            "added": added[:8], "dropped": dropped}


def _looks_citation(piece):
    digits = sum(w.isdigit() for w in piece)
    return digits / float(len(piece)) > 0.12 or sum(w in ("v", "see", "id", "at", "supra", "cir", "ct", "f") for w in piece) / float(len(piece)) > 0.08


def cmd_audit(args):
    run = os.path.abspath(args.run)
    m = load_manifest(run)
    out = []
    for doc in m["docs"]:
        if doc["type"] != "pdf":
            continue
        if doc.get("scanned"):
            out.append({"doc": doc["id"], "name": doc["name"], "checked": False,
                        "why": "The PDF has no usable text layer (a scan), so there's nothing to compare with."})
            continue
        ddir = os.path.join(sources_dir(run), doc["id"])
        rj = os.path.join(ddir, "reading.json")
        if not os.path.isfile(rj):
            fail("Run `sources.py stitch` first.")
        with open(rj, encoding="utf-8") as f:
            reading = json.load(f)
        tasks = []
        tot_read = tot_match = 0
        title_words = norm_words(" ".join(reading.get(k) or "" for k in ("title", "author", "intro")))
        for t in doc["tasks"]:
            pages = [(n, read_text(os.path.join(ddir, "pages", "%04d.txt" % n))) for n in range(t["first"], t["last"] + 1)]
            nt = [n["text"] for n in reading["notes"].values() if n.get("task") == t["id"]]
            skipped = [b["text"] for b in reading["blocks"] if b["type"] == "skipped" and b.get("task") == t["id"]]
            r = audit_task("", task_text(reading["blocks"], t["id"]), nt, title_words, pages, skipped)
            r.update({"task": t["id"], "pages": "%d-%d" % (t["first"], t["last"])})
            if skipped:
                r["readerSkipped"] = skipped
            tot_read += r.get("readWords", 0)
            tot_match += r.get("matchedWords", 0)
            tasks.append(r)
        flags = [dict(d, task=t["task"], pages=t["pages"], readerSkipped=t.get("readerSkipped"))
                 for t in tasks for d in t.get("dropped", []) if d["kind"] == "possibly dropped"]
        added = [{"task": t["task"], "pages": t["pages"], "text": a} for t in tasks for a in t.get("added", [])]
        empty = [t["task"] for t in tasks if t.get("readWords", 0) == 0 and t.get("sourceWords", 0) > 150]
        faith = round(tot_match / float(tot_read), 3) if tot_read else None
        out.append({"doc": doc["id"], "name": doc["name"], "checked": True, "faithfulness": faith,
                    "verdict": ("good" if faith and faith >= 0.97 and not flags and not empty else "review"),
                    "possiblyDropped": flags, "addedOrReworded": added[:15], "emptyTasks": empty,
                    "tasks": [{k: t.get(k) for k in ("task", "pages", "readWords", "sourceWords", "faithfulness")} for t in tasks]})
    write_json(os.path.join(sources_dir(run), "audit.json"), out)
    emit({"docs": out, "note": "faithfulness = share of the reading's words that match the PDF's own text, in order. "
                               "Footnotes, citations, headers and page numbers are expected to be missing from the reading."})


# ---------------------------------------------------------------------------
# narration script


def clean_para(text):
    t = MARK_RE.sub("", text)
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)
    return " ".join(t.split())


SMALL_WORDS = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "nor", "of", "on", "or", "the", "to", "v", "v.", "vs."}


def spoken_heading(text):
    """ALL-CAPS headings ("STATEMENT OF THE CASE") read and title better in title case."""
    letters = [c for c in text if c.isalpha()]
    if not letters or not all(c.isupper() for c in letters) or len(letters) < 4:
        return text
    words = text.lower().split()
    out = []
    first = 0  # the first word after a label such as "II." or "A." is capitalized too
    for i, w in enumerate(words):
        if i == first and re.fullmatch(r"(?:[ivxlc]+|[a-z]|\d+)[.)]", w):
            out.append(w.upper())
            first = i + 1
        elif re.fullmatch(r"[ivxlc]+\.?", w) and len(w.rstrip(".")) <= 4:   # roman numerals
            out.append(w.upper())
        elif i != first and w in SMALL_WORDS:
            out.append(w)
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out)


def note_line(nt):
    label = nt["n"] if not nt["n"].startswith("*") else ""
    lead = "Footnote %s. " % label if label else "A note. "
    return lead + nt["text"].strip()


def cmd_script(args):
    run = os.path.abspath(args.run)
    m = load_manifest(run)
    turns = []  # [role, text]

    def say(role, text):
        text = text.strip()
        if not text:
            return
        if turns and turns[-1][0] == role:
            turns[-1][1] += "\n\n" + text
        else:
            turns.append([role, text])

    stats = {"notesRead": 0, "figuresDescribed": 0, "sections": 0}
    for doc in m["docs"]:
        ddir = os.path.join(sources_dir(run), doc["id"])
        if doc["type"] != "pdf":
            say("Narrator", read_text(os.path.join(ddir, "reading.md")))
            continue
        with open(os.path.join(ddir, "reading.json"), encoding="utf-8") as f:
            reading = json.load(f)
        notes = reading["notes"]
        title = reading.get("title") or os.path.splitext(doc["name"])[0]
        opening = ["# " + title]
        if reading.get("author"):
            opening.append("By %s." % reading["author"])
        if reading.get("intro"):
            opening.append(reading["intro"])
        turns.append(["Narrator", "\n\n".join(opening)])
        pending = []  # notes held to the end of the section

        def release():
            if pending:
                say("Notes", "Notes for this section.\n\n" + "\n\n".join(note_line(notes[k]) for k in pending))
                stats["notesRead"] += len(pending)
                pending.clear()

        for b in reading["blocks"]:
            if b["type"] == "heading":
                if args.notes == "section-end":
                    release()
                turns.append(["Narrator", "#" * max(1, min(4, b.get("level", 1))) + " " + spoken_heading(b["text"])])
                stats["sections"] += 1
            elif b["type"] == "para":
                say("Narrator", clean_para(b["text"]))
                subst = [k for k in b.get("notes", []) if notes[k]["cls"] == "substantive" and notes[k]["text"]]
                if args.notes == "after-paragraph" and subst:
                    say("Notes", "\n\n".join(note_line(notes[k]) for k in subst))
                    stats["notesRead"] += len(subst)
                elif args.notes == "section-end":
                    pending.extend(subst)
            elif b["type"] in ("figure", "table") and args.figures == "describe":
                say("Notes", b["text"])
                stats["figuresDescribed"] += 1
        release()
    # A heading turn on its own would be a one-line chapter: fold the text that follows into it.
    merged = []
    for role, text in turns:
        if merged and merged[-1][0] == "Narrator" and role == "Narrator" and re.match(r"^#{1,4} [^\n]*$", merged[-1][1]):
            merged[-1][1] += "\n\n" + text
        else:
            merged.append([role, text])
    out = os.path.join(run, "narration.txt")
    write_text(out, "\n\n".join("[%s]: %s" % (r, t) for r, t in merged) + "\n")
    words = sum(len(t.split()) for _, t in merged)
    emit({"ok": True, "script": out, "turns": len(merged), "words": words, "minutes": round(words / 175.0, 1),
          "roles": sorted({r for r, _ in merged}), **stats})


# ---------------------------------------------------------------------------
# package, quick, status


def cmd_package(args):
    import engine  # the bundle checker lives there
    run = os.path.abspath(args.run)
    m = load_manifest(run)
    pdfs = [d for d in m["docs"] if d["type"] == "pdf"]
    if len(m["docs"]) != 1:
        fail("package makes a bundle for one document; this reading has %d." % len(m["docs"]))
    doc = m["docs"][0]
    ddir = os.path.join(sources_dir(run), doc["id"])
    if not os.path.isfile(args.audio):
        fail("Audio not found: %s" % args.audio)
    reading_path = os.path.join(ddir, "reading.json")
    title = doc.get("title") or os.path.splitext(doc["name"])[0]
    author = doc.get("author") or "Unknown Author"
    if os.path.isfile(reading_path):
        with open(reading_path, encoding="utf-8") as f:
            r = json.load(f)
        title, author = r.get("title") or title, r.get("author") or author
    display = engine.safe_component(args.title or title).replace("-", "\u2011")
    base = "%s-original" % str(uuid.uuid4()).upper()
    stype = "pdf" if pdfs else "txt"
    info = {"displayName": display, "author": author, "baseFileName": base, "sourceFileType": stype}
    src = doc["pdf"] if pdfs else os.path.join(ddir, "text.txt")
    files = [("%s.%s" % (base, stype), src),
             ("%s-transcript.txt" % base, os.path.join(ddir, "reading.md")),
             ("%s-transcript.m4a" % base, os.path.abspath(args.audio))]
    bundle = os.path.join(run, "%s.enTalkenator" % display)
    tmp = bundle + ".tmp"
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for arc, path in files:
            z.write(path, "%s/%s" % (display, arc))
        zi = zipfile.ZipInfo("%s/info.txt" % display, date_time=_dt.datetime.now().timetuple()[:6])
        zi.compress_type = zipfile.ZIP_DEFLATED
        zi.external_attr = 0o644 << 16
        z.writestr(zi, json.dumps(info, ensure_ascii=False, separators=(",", ":")))
    os.replace(tmp, bundle)
    try:
        check = engine.simulate_import(bundle)
    except (ValueError, KeyError) as e:
        check = {"error": str(e)}
    emit({"ok": True, "bundle": bundle, "info": info, "appImport": check})


def cmd_quick(args):
    run = os.path.abspath(args.run)
    parts = []
    for src, src_pages in expand_sources(args.source):
        if src.lower().endswith(".pdf"):
            pages = pdf_pages(src)
            a, b = parse_pages(src_pages, len(pages))
            text = "\n\n".join(pages[a - 1:b])
            if len(text.split()) < 50:
                fail("%s has no usable text layer (a scan?). Use `plan` so Claude reads it instead." % os.path.basename(src))
        else:
            text = convert_to_text(src)
        parts.append((os.path.splitext(os.path.basename(src))[0], re.sub(r"\n{3,}", "\n\n", text).strip()))
    combined = parts[0][1] + "\n" if len(parts) == 1 else "".join(
        "What follows is the text entitled %s:\n\n%s\n[End of the text entitled %s]\n\n" % (n, t, n) for n, t in parts)
    out = os.path.join(sources_dir(run), "combined.md")
    write_text(out, combined)
    emit({"ok": True, "combined": out, "words": len(combined.split()), "docs": [n for n, _ in parts]})


def cmd_status(args):
    run = os.path.abspath(args.run)
    m = load_manifest(run)
    docs = []
    for d in m["docs"]:
        done = [t["id"] for t in d.get("tasks", []) if os.path.isfile(t["output"])]
        docs.append({"doc": d["id"], "name": d["name"], "tasks": len(d.get("tasks", [])), "done": len(done),
                     "missing": [{"task": t["id"], "sheet": t["sheet"], "output": t["output"]}
                                 for t in d.get("tasks", []) if not os.path.isfile(t["output"])]})
    emit({"docs": docs})


def cmd_meta(args):
    run = os.path.abspath(args.run)
    m = load_manifest(run)
    doc = next((d for d in m["docs"] if d["id"] == args.doc or d["name"] == args.doc), None)
    if not doc:
        fail("No document %r; they are %s" % (args.doc, ", ".join(d["id"] for d in m["docs"])))
    if args.title:
        doc["title"] = args.title
    if args.author:
        doc["author"] = args.author
    doc["metaSet"] = True
    save_manifest(run, m)
    emit({"ok": True, "doc": doc["id"], "title": doc.get("title"), "author": doc.get("author"),
          "note": "Run `sources.py stitch` again to relabel combined.md."})


def cmd_prefs(args):
    """The narration settings in prefs.py's preferences (kept here for older skill text)."""
    pairs = ["narration.%s=%s" % (k, v) for k, v in (("notes", args.notes), ("citations", args.citations),
                                                     ("figures", args.figures)) if v]
    if pairs:
        preferences.set_values(pairs)
    data = preferences.load()
    emit({"prefs": {k.split(".", 1)[1]: v for k, v in data.items() if k.startswith("narration.")},
          "saved": bool(pairs), "file": preferences.PATH})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("run")
    p.add_argument("--source", action="append", required=True)
    p.add_argument("--pages", help="only these pages, e.g. 3-12 (one source)")
    p.add_argument("--pages-per-task", type=int, default=8)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_plan)
    p = sub.add_parser("map")
    p.add_argument("run")
    p.add_argument("--doc")
    p.set_defaults(func=cmd_map)
    for name, fn in (("stitch", cmd_stitch), ("audit", cmd_audit), ("status", cmd_status)):
        p = sub.add_parser(name)
        p.add_argument("run")
        p.set_defaults(func=fn)
    p = sub.add_parser("script")
    p.add_argument("run")
    p.add_argument("--notes", default="after-paragraph", choices=["after-paragraph", "section-end", "none"])
    p.add_argument("--figures", default="describe", choices=["describe", "skip"])
    p.set_defaults(func=cmd_script)
    p = sub.add_parser("package")
    p.add_argument("run")
    p.add_argument("--audio", required=True)
    p.add_argument("--title")
    p.set_defaults(func=cmd_package)
    p = sub.add_parser("quick")
    p.add_argument("run")
    p.add_argument("--source", action="append", required=True)
    p.set_defaults(func=cmd_quick)
    p = sub.add_parser("meta")
    p.add_argument("run")
    p.add_argument("doc")
    p.add_argument("--title")
    p.add_argument("--author")
    p.set_defaults(func=cmd_meta)
    p = sub.add_parser("prefs")
    p.add_argument("--notes", choices=["after-paragraph", "section-end", "none"])
    p.add_argument("--citations", choices=["drop", "names", "keep"])
    p.add_argument("--figures", choices=["describe", "skip"])
    p.set_defaults(func=cmd_prefs)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
