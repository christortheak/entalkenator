#!/usr/bin/env python3
"""Checks scripts/sources.py without Claude: a generated three-page PDF, stand-in reader output
(including a dropped paragraph and an invented sentence for the audit to catch), stitching across
pages, footnote placement, the narration script, quick mode, and the app bundle.

    python3 tests/test_sources.py

macOS only (cupsfilter makes the test PDF; PDFKit reads and cuts it).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SOURCES = os.path.join(ROOT, "scripts", "sources.py")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import sources  # noqa: E402

passed, failed = 0, []


def check(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print("  ok   " + name)
    else:
        failed.append(name)
        print("  FAIL %s %s" % (name, detail))


P1 = """THE LAW OF QUIET THINGS   3
INTRODUCTION
Courts have long treated silence as ambiguous. A party who says nothing may be agreeing,
objecting, or simply absent.1 This Article argues that the ambiguity is a feature, not a flaw,
and that doctrine should stop pretending otherwise.
The argument proceeds in three parts. First, it surveys how contract law handles silence.
Second, it turns to procedure, where silence can forfeit a claim. Third, it offers a theory
that explains both and predicts where the law will
1 See Smith v. Jones, 123 F.3d 456, 460 (9th Cir. 1999)."""
P2 = """4   THE LAW OF QUIET THINGS
go next. Each part builds on the one before it, and the conclusion returns to the opening puzzle.
I. CONTRACT
In contract, silence is usually not acceptance.2 The offeror cannot bind the offeree by saying
that silence will count as yes. But there are exceptions, and they are revealing. When parties
have dealt with each other before, silence can carry meaning that a stranger's silence cannot.
Figure 1 shows how often courts found acceptance by silence over five decades.
2 Indeed, the rule is older than the Restatement; it appears in cases from the eighteenth
century, where courts worried that merchants would be trapped by unsolicited offers."""
P3 = """THE LAW OF QUIET THINGS   5
II. PROCEDURE
Procedure is harsher. A litigant who stays silent at trial may lose the right to complain on appeal.
This rule protects the trial court's chance to fix its own mistakes, and it discourages sandbagging.
The forfeiture doctrine has its own exceptions for plain error, which courts apply sparingly."""

READ1 = """@title The Law of Quiet Things
@author A. Scholar
# INTRODUCTION

Courts have long treated silence as ambiguous. A party who says nothing may be agreeing, objecting, or simply absent.[^1] This Article argues that the ambiguity is a feature, not a flaw, and that doctrine should stop pretending otherwise.

The argument proceeds in three parts. First, it surveys how contract law handles silence. Second, it turns to procedure, where silence can forfeit a claim. Third, it offers a theory that explains both and predicts where the law will
@note 1 citation
@incomplete
"""
READ2 = """@continues
go next. Each part builds on the one before it, and the conclusion returns to the opening puzzle.

# I. CONTRACT

In contract, silence is usually not acceptance.[^2] The offeror cannot bind the offeree by saying that silence will count as yes. But there are exceptions, and they are revealing. When parties have dealt with each other before, silence can carry meaning that a stranger's silence cannot. Figure 1 shows how often courts found acceptance by silence over five decades.

@figure Figure 1 is a line chart of acceptance-by-silence findings by decade, rising slowly over fifty years.
@note 2 substantive | Indeed, the rule is older than the Restatement; it appears in cases from the eighteenth century, where courts worried that merchants would be trapped by unsolicited offers.
"""
# Page 3's reader drops a paragraph and invents a sentence: the audit should notice both.
READ3 = """# II. PROCEDURE

Procedure is harsher. A litigant who stays silent at trial may lose the right to complain on appeal. This rule protects the trial court's chance to fix its own mistakes, and it discourages sandbagging.

Scholars have debated this rule vigorously for many decades without any consensus emerging at all.
"""


def run(*args, env=None):
    p = subprocess.run([sys.executable, SOURCES] + list(args), capture_output=True, text=True, env=env)
    try:
        return p.returncode, json.loads(p.stdout)
    except ValueError:
        return p.returncode, {"raw": p.stdout + p.stderr}


def main():
    if sys.platform != "darwin" or not os.path.exists("/usr/sbin/cupsfilter"):
        raise SystemExit("These tests need macOS (cupsfilter and PDFKit).")
    tmp = tempfile.mkdtemp(prefix="entalk-sources-")
    env = dict(os.environ, ENTALKENATOR_CONFIG_DIR=os.path.join(tmp, "config"))
    try:
        txt = os.path.join(tmp, "paper.txt")
        with open(txt, "w") as f:
            f.write(P1 + "\f" + P2 + "\f" + P3 + "\n")
        pdf = os.path.join(tmp, "paper.pdf")
        with open(pdf, "wb") as out:
            subprocess.run(["/usr/sbin/cupsfilter", "-i", "text/plain", "-m", "application/pdf", txt],
                           stdout=out, stderr=subprocess.DEVNULL, check=True)
        notes_md = os.path.join(tmp, "notes.md")
        with open(notes_md, "w") as f:
            f.write("Some notes on silence.\n")

        print("plan")
        runp = os.path.join(tmp, "run")
        code, out = run("plan", runp, "--source", pdf, "--pages-per-task", "1", env=env)
        check("plans one task per page", code == 0 and len(out["tasks"]) == 3, out)
        m = json.load(open(os.path.join(runp, "sources", "manifest.json")))
        tasks = m["docs"][0]["tasks"]
        check("each task gets its own one-page PDF", all(t.get("chunk") and len(sources.pdf_pages(t["chunk"])) == 1 for t in tasks))
        sheet = open(tasks[0]["sheet"]).read()
        check("the sheet points at the small PDF, with no page range", "no page range needed" in sheet)
        check("the sheet points every reader at the document's brief", m["docs"][0]["brief"] in sheet)
        code, out = run("map", runp, env=env)
        rows = out["docs"][0]["map"] if code == 0 else []
        check("map sketches every page", len(rows) == 3 and rows[0]["starts"].startswith("THE LAW OF QUIET THINGS")
              and out["docs"][0]["briefWritten"] is False, out)
        code, out = run("plan", os.path.join(tmp, "run2"), "--source", pdf + ":2-3", "--source", notes_md, env=env)
        check("per-source page ranges and text files", code == 0 and len(out["tasks"]) == 1 and out["docs"][0]["range"] == [2, 3]
              and out["docs"][1]["type"] == "text", out)

        print("stitch")
        for t, text in zip(tasks, (READ1, READ2, READ3)):
            with open(t["output"], "w") as f:
                f.write(text)
        code, out = run("stitch", runp, env=env)
        doc = out["docs"][0]
        check("stitches with the title and author", code == 0 and doc["title"] == "The Law of Quiet Things" and doc["author"] == "A. Scholar", out)
        reading = json.load(open(os.path.join(runp, "sources", "d1", "reading.json")))
        paras = [b["text"] for b in reading["blocks"] if b["type"] == "para"]
        check("a paragraph split across pages is joined", any("predicts where the law will go next." in p for p in paras), paras[:3])
        check("notes are sorted: 1 substantive, 1 citation", doc["notes"] == {"substantive": 1, "citation": 1}, doc["notes"])
        md = open(os.path.join(runp, "sources", "d1", "reading.md")).read()
        check("the reading carries substantive notes and figures inline", "[Footnote 2: Indeed" in md and "[Figure: Figure 1" in md
              and "See Smith" not in md, md[:300])

        print("audit")
        code, out = run("audit", runp, env=env)
        a = out["docs"][0]
        dropped = " ".join(x["excerpt"] for x in a["possiblyDropped"])
        check("catches the dropped paragraph", "plain error" in dropped or "forfeiture doctrine" in dropped, a["possiblyDropped"])
        check("catches the invented sentence", any("scholars have debated" in x["text"] for x in a["addedOrReworded"]), a["addedOrReworded"])
        check("doesn't flag running heads or the citation footnote", "smith v jones" not in dropped and "quiet things 3" not in dropped)
        check("verdict says review", a["verdict"] == "review")
        with open(tasks[2]["output"], "w") as f:
            f.write(READ3.replace("Scholars have debated this rule vigorously for many decades without any consensus emerging at all.",
                                  "The forfeiture doctrine has its own exceptions for plain error, which courts apply sparingly."))
        run("stitch", runp, env=env)
        code, out = run("audit", runp, env=env)
        check("a faithful reading passes", out["docs"][0]["verdict"] == "good" and out["docs"][0]["faithfulness"] >= 0.97, out["docs"][0])

        print("narration script")
        code, out = run("script", runp, "--notes", "after-paragraph", env=env)
        script = open(os.path.join(runp, "narration.txt")).read()
        check("headings open chapters, in title case", "[Narrator]: # The Law of Quiet Things" in script and "# Introduction" in script
              and "# I. Contract" in script, script[:200])
        i_note, i_next = script.find("[Notes]: Footnote 2."), script.find("# II. Procedure")
        check("the substantive note follows its paragraph, before the next section", 0 < i_note < i_next)
        notes_turns = [t for t in script.split("\n\n[") if t.startswith("Notes]:") or t.startswith("[Notes]:")]
        check("the figure is described in the notes voice", any("Figure 1 is a line chart" in t for t in notes_turns), notes_turns)
        check("citation-only notes and markers are gone", "Footnote 1." not in script and "[^" not in script)
        code, out = run("script", runp, "--notes", "section-end", env=env)
        script = open(os.path.join(runp, "narration.txt")).read()
        check("section-end gathers notes before the next heading", 0 < script.find("Notes for this section.") < script.find("# II. Procedure"))
        code, out = run("script", runp, "--notes", "none", "--figures", "skip", env=env)
        script = open(os.path.join(runp, "narration.txt")).read()
        check("notes and figures can be left out", "[Notes]" not in script and out["notesRead"] == 0)

        print("package")
        audio = os.path.join(tmp, "a.m4a")
        subprocess.run(["say", "-o", audio, "--file-format=m4af", "test"], check=True)
        code, out = run("package", runp, "--audio", audio, env=env)
        entries = (out.get("appImport") or {}).get("libraryEntries", {})
        check("the bundle imports as the PDF with its text and narration", code == 0 and entries.get("transcript") == ["m4a", "txt"]
              and out["info"]["sourceFileType"] == "pdf", out)

        print("quick, folders, prefs")
        folder = os.path.join(tmp, "set")
        os.makedirs(folder)
        shutil.copy(pdf, os.path.join(folder, "a.pdf"))
        shutil.copy(notes_md, os.path.join(folder, "b.md"))
        code, out = run("quick", os.path.join(tmp, "run3"), "--source", folder, env=env)
        combined = open(out["combined"]).read() if code == 0 else ""
        check("a folder becomes a labelled set", code == 0 and out["docs"] == ["a", "b"] and "What follows is the text entitled a:" in combined, out)
        code, out = run("prefs", "--notes", "section-end", env=env)
        code, out = run("prefs", env=env)
        check("preferences are remembered", out["prefs"].get("notes") == "section-end")
        PREFS = os.path.join(ROOT, "scripts", "prefs.py")

        def pr(*a):
            p = subprocess.run([sys.executable, PREFS] + list(a), capture_output=True, text=True, env=env)
            return p.returncode, json.loads(p.stdout)
        code, out = pr("show")
        check("narration choices live in the shared preferences", out["settings"].get("narration.notes") == "section-end", out)
        code, out = pr("set", "questions=none", "publish=always", "chapters.narration=main-parts")
        check("settings are recorded", code == 0 and out["set"]["questions"] == "none")
        code, out = pr("set", "publish=sometimes")
        check("an invalid value is refused", code == 1 and "publish must be one of" in out["error"], out)
        code, out = pr("note", "Likes a chapter every 5 to 10 minutes")
        code, out = pr("show")
        check("notes in the person's own words are kept", out["notes"][0]["text"].startswith("Likes a chapter") and out["notes"][0]["n"] == 1)
        code, out = pr("forget", "1")
        code, out = pr("show")
        check("...and can be forgotten", out["notes"] == [] and out["settings"]["publish"] == "always")

        print("heading levels")
        reading = json.load(open(os.path.join(runp, "sources", "d1", "reading.json")))
        for b in reading["blocks"]:
            if b["type"] == "heading" and b["text"].startswith("I."):
                b["level"] = 2
        json.dump(reading, open(os.path.join(runp, "sources", "d1", "reading.json"), "w"))
        run("script", runp, env=env)
        script = open(os.path.join(runp, "narration.txt")).read()
        check("the narration script keeps heading levels", "## I. Contract" in script and "\n[Narrator]: # Introduction" not in script
              or "## I. Contract" in script, script[:300])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d passed, %d failed" % (passed, len(failed)))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
