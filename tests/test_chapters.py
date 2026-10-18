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
        check("a narration's chapters are its headings only, not its footnotes",
              [w[1] for w in want] == ["Introduction", "II. Procédure — “quoted” titles"], [w[1] for w in want])
        tt = doc["entalkenator"]["turnTimes"]
        check("every turn's timing is kept for the library's follow-along transcript",
              len(tt) == 4 and [t["speaker"] for t in tt] == ["Narrator", "Notes", "Narrator", "Narrator"]
              and tt[0]["start"] == 0 and all(a["end"] <= b["start"] for a, b in zip(tt, tt[1:])), tt)
        check("the second chapter starts where its heading's turn starts", abs(want[1][0] - tt[3]["start"]) < 0.01, (want, tt))

        deep = os.path.join(tmp, "deep.txt")
        with open(deep, "w") as f:
            body = "This paragraph is long enough to take several seconds to read aloud, so headings stay apart. " * 2
            f.write("[Narrator]: # Part One\n\n## A. First\n\n%s\n\n[Narrator]: ## B. Second\n\n%s\n\n"
                    "[Narrator]: # Part Two\n\n%s\n" % (body, body, body))
        dout = os.path.join(tmp, "Deep.m4a")
        subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "render_audio.py"), "--transcript", deep,
                        "--out", dout, "--engine", "say", "--chapter-depth", "1"], capture_output=True, text=True)
        ddoc = json.load(open(os.path.splitext(dout)[0] + ".chapters.json"))
        check("--chapter-depth 1 keeps only top-level headings", [c["title"] for c in ddoc["chapters"]] == ["Part One", "Part Two"],
              ddoc["chapters"])

        conv = os.path.join(tmp, "conv.txt")
        with open(conv, "w") as f:
            f.write("[Moderator]: Welcome to the debate.\n\n[Ada]: Thank you. Silence is consent.\n\n[Ben]: It is not.\n")
        cout = os.path.join(tmp, "Conv.m4a")
        subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "render_audio.py"), "--transcript", conv,
                        "--out", cout, "--engine", "say"], capture_output=True, text=True)
        cdoc = json.load(open(os.path.splitext(cout)[0] + ".chapters.json"))
        check("a conversation keeps one chapter per speaker turn", [c["title"] for c in cdoc["chapters"]] ==
              ["Moderator: Welcome to the debate.", "Ada: Thank you. Silence is consent.", "Ben: It is not."], cdoc["chapters"])
        plan = os.path.join(tmp, "plan.json")
        with open(plan, "w") as f:
            json.dump({"chapters": [{"turn": 1, "title": "Welcome"}, {"turn": 2, "title": "Is silence consent?"}]}, f)
        subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "render_audio.py"), "--transcript", conv,
                        "--out", cout, "--engine", "say", "--chapter-plan", plan], capture_output=True, text=True)
        pdoc = json.load(open(os.path.splitext(cout)[0] + ".chapters.json"))
        tts = pdoc["entalkenator"]["turnTimes"]
        check("with a plan, a conversation's chapters are the showrunner's choices",
              [(c["title"], c["startTime"]) for c in pdoc["chapters"]] == [("Welcome", 0), ("Is silence consent?", tts[1]["start"])]
              and len(tts) == 3, pdoc["chapters"])
        check("...embedded in the .m4a too", [c[1] for c in mp4chapters.read_chapters(cout)] == ["Welcome", "Is silence consent?"])
        with open(os.path.join(tmp, "delivery.json"), "w") as f:
            json.dump({"turns": [{"turn": 3, "role": "Ben", "note": "cutting in", "cutsIn": True}]}, f)
        subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "render_audio.py"), "--transcript", conv,
                        "--out", cout, "--engine", "say"], capture_output=True, text=True)
        ct = json.load(open(os.path.splitext(cout)[0] + ".chapters.json"))["entalkenator"]["turnTimes"]
        check("a turn marked as cutting in overlaps the one before it", ct[2]["start"] < ct[1]["end"]
              and ct[1]["start"] - ct[0]["end"] > 0.1, ct)
        v = mp4chapters.verify(out)
        check("AVFoundation reads every chapter title", v and v.get("chapters") == [w[1] for w in want], v)
        dur = afinfo_seconds(out)
        check("the audio is intact", dur and abs(dur - res.get("durationSeconds", 0)) < 1.0, (dur, res.get("durationSeconds")))

        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import render_audio
        jobs = [{"text": " ".join(["word"] * 60)} for _ in range(5)]
        spans = [(0, 20), (20, 40), (40, 60), (60, 72), (72, 92)]  # turn 4: 60 words in 12 s
        flagged = render_audio.pace_outliers(jobs, spans, ["Gene"] * 5)
        check("a turn spoken far faster than the speaker's usual pace is flagged",
              [(f["turn"], f["wpm"], f["usual"]) for f in flagged] == [(4, 300, 180)] and "missing" in flagged[0]["why"], flagged)
        check("...and the say render above has nothing flagged", res.get("checkTurns") == [], res.get("checkTurns"))
        t = "Well (laughs) that is fair. (Sighs) Fine. He (clears his throat) said so (coughs)."
        check("Breeze gets the vocal sounds in its own spelling",
              render_audio.vocal_events(t, True) == "Well (laugh) that is fair. (sigh) Fine. He (clears throat) said so (cough).")
        check("...and the other voices leave them out", render_audio.vocal_events(t, False) == "Well that is fair. Fine. He said so.")

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
