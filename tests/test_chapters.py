#!/usr/bin/env python3
"""Checks that render_audio.py embeds chapters in the .m4a (for Overcast and Apple Podcasts),
that Apple's AVFoundation reads them, and that embedding again replaces rather than duplicates.

    python3 tests/test_chapters.py

macOS only: it renders with the built-in `say` voices and reads the file with AVFoundation.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import mp4chapters  # noqa: E402

passed, failed = 0, []


def check(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print("  ok   " + name)
    else:
        failed.append(name)
        print("  FAIL %s %s" % (name, detail))


SCRIPT = """[Narrator]: # Introduction

Courts have long treated silence as ambiguous.

[Notes]: Footnote 1. The rule is older than the Restatement.

[Narrator]: The argument proceeds in three parts.

[Narrator]: # II. Procédure — “quoted” titles

Procedure is harsher. A litigant who stays silent may forfeit a claim.
"""


def afinfo_seconds(path):
    out = subprocess.run(["afinfo", path], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if "estimated duration" in line:
            return float(line.split(":")[1].split()[0])
    return None


def main():
    if sys.platform != "darwin":
        raise SystemExit("macOS only.")
    tmp = tempfile.mkdtemp(prefix="entalk-chapters-")
    try:
        t = os.path.join(tmp, "narration.txt")
        with open(t, "w", encoding="utf-8") as f:
            f.write(SCRIPT)
        out = os.path.join(tmp, "Test.m4a")
        p = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "render_audio.py"), "--transcript", t,
                            "--out", out, "--engine", "say"], capture_output=True, text=True)
        res = json.loads(p.stdout) if p.returncode == 0 else {}
        check("render embeds chapters", res.get("embeddedChapters") is True, p.stderr[-300:])
        doc = json.load(open(os.path.splitext(out)[0] + ".chapters.json"))
        want = [(c["startTime"], c["title"]) for c in doc["chapters"]]
        got = mp4chapters.read_chapters(out)
        check("the embedded chapters match the chapters file", [g[1] for g in got] == [w[1] for w in want]
              and all(abs(g[0] - w[0]) < 0.01 for g, w in zip(got, want)), (got, want))
        check("chapters are titled by section, footnote, and continuation",
              [w[1] for w in want] == ["Introduction", "Footnote 1. The rule is older than the Restatement.",
                                       "Introduction (continued)", "II. Procédure — “quoted” titles"], [w[1] for w in want])
        v = mp4chapters.verify(out)
        check("AVFoundation reads every chapter title", v and v.get("chapters") == [w[1] for w in want], v)
        dur = afinfo_seconds(out)
        check("the audio is intact", dur and abs(dur - res.get("durationSeconds", 0)) < 1.0, (dur, res.get("durationSeconds")))

        mp4chapters.add_chapters(out, [(0, "One"), (1.0, "Two")])
        check("embedding again replaces the chapters", [c[1] for c in mp4chapters.read_chapters(out)] == ["One", "Two"]
              and (mp4chapters.verify(out) or {}).get("chapters") == ["One", "Two"])
        check("...and the audio is still intact", abs((afinfo_seconds(out) or 0) - dur) < 0.05)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d passed, %d failed" % (passed, len(failed)))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
