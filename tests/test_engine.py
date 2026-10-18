#!/usr/bin/env python3
"""Checks scripts/engine.py without Claude: natural turn-taking in a template (the showrunner
gives the floor where the template allows several speakers), and a directed program (no fixed
steps: the showrunner writes the plan and directs every turn). Stand-in text plays the speakers.

    python3 tests/test_engine.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENGINE = os.path.join(ROOT, "scripts", "engine.py")

passed, failed = 0, []


def check(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print("  ok   " + name)
    else:
        failed.append(name)
        print("  FAIL %s %s" % (name, detail))


def eng(*args, stdin=None):
    p = subprocess.run([sys.executable, ENGINE] + list(args), capture_output=True, text=True, input=stdin)
    try:
        return p.returncode, json.loads(p.stdout)
    except ValueError:
        return p.returncode, {"raw": p.stdout + p.stderr}


def speak(out, text):
    """Play every speaker the engine just issued."""
    for item in out.get("issue", []):
        with open(item["outputFile"], "w") as f:
            f.write(text if item["visible"] else "Private notes.")


DOC = "Silence in law is ambiguous. Contract treats it one way and procedure another.\n" * 20

PROGRAM = {
    "title": "Faculty Workshop",
    "form": "workshop",
    "setting": "This is a faculty workshop at a law school. Everyone has read the paper:\n\n{{transcript}}\n\n-----",
    "lead": "Gene Alford",
    "roles": [
        {"name": "Gene Alford", "description": "You moderate the workshop."},
        {"name": "Janson Skullbottom", "description": "You present the paper."},
        {"name": "Abney Weathertop", "description": "You are a skeptical colleague."},
    ],
    "segments": [
        {"title": "The talk", "minutes": 20, "plan": "Janson presents in three parts."},
        {"title": "Questions", "minutes": 40, "plan": "Colleagues question Janson."},
    ],
}


def main():
    tmp = tempfile.mkdtemp(prefix="entalk-engine-")
    try:
        src = os.path.join(tmp, "doc.txt")
        with open(src, "w") as f:
            f.write(DOC)

        print("natural turn-taking in a template")
        run = os.path.join(tmp, "round")
        code, out = eng("init", "--template", "roundtable", "--source", src, "--run-dir", run, "--floor", "natural",
                        "--loop-scale", "0.02")
        check("init reports the steps where the floor is open", code == 0 and out["floor"] == "natural"
              and out["floorSteps"] == 9, out)
        code, out = eng("advance", run)                       # the moderator's private outline
        speak(out, "")
        code, out = eng("advance", run)                       # the moderator's welcome
        check("fixed steps are issued as before", out["issue"][0]["role"] == "Reenus Lewis", out)
        sheet = open(out["issue"][0]["sheet"]).read()
        check("speakers are told the floor is open, and may address one another", "Who speaks next isn't fixed" in sheet)
        speak(out, "Welcome, everyone. Abney, you've written on this; where do you want to start?")
        code, out = eng("advance", run)
        ch = out.get("choose") or {}
        names = [c["role"] for c in ch.get("candidates", [])]
        check("where several could speak, the showrunner is asked to choose", "issue" not in out
              and sorted(names) == ["Abney Weathertop", "Hillford Johns", "Janson Skullbottom"], out)
        check("...with the last speaker left out and who was addressed marked",
              ch.get("lastSpeaker") == "Reenus Lewis" and [c["role"] for c in ch["candidates"] if c["addressed"]]
              == ["Abney Weathertop"] and "where do you want to start?" in ch.get("lastWords", ""), ch)
        code, out = eng("advance", run, "--speaker", "Reenus Lewis")
        check("the person who just spoke can't take the floor again", code == 1 and "can't take this turn" in out.get("error", ""), out)
        code, out = eng("advance", run, "--speaker", "abney")
        check("a first name is enough, and that speaker gets the turn", code == 0
              and out["issue"][0]["role"] == "Abney Weathertop", out)
        run2 = os.path.join(tmp, "round2")
        eng("init", "--template", "roundtable", "--source", src, "--run-dir", run2, "--loop-scale", "0.02")
        code, out = eng("advance", run2)
        speak(out, "")
        code, out = eng("advance", run2)
        speak(out, "Welcome.")
        code, out = eng("advance", run2)
        check("without --floor natural, the template's random pick stands", "issue" in out and "choose" not in out, out)

        print("a directed program")
        prog = os.path.join(tmp, "program.json")
        with open(prog, "w") as f:
            json.dump(PROGRAM, f)
        bad = os.path.join(tmp, "bad.json")
        with open(bad, "w") as f:
            json.dump({"setting": "x", "roles": [{"name": "A"}], "segments": []}, f)
        code, out = eng("init", "--program", bad, "--source", src, "--run-dir", os.path.join(tmp, "bad"))
        check("an incomplete program is refused with what's missing", code == 1 and "needs a description" in out["error"]
              and "segments" in out["error"], out)
        run3 = os.path.join(tmp, "workshop")
        code, out = eng("init", "--program", prog, "--source", src, "--run-dir", run3)
        check("init takes a program instead of a template", code == 0 and out["directed"] and out["form"] == "workshop"
              and [s["title"] for s in out["segments"]] == ["The talk", "Questions"], out)
        code, out = eng("advance", run3)
        check("with nothing directed yet, the engine shows where things stand", "direct" in out and "issue" not in out
              and out["progress"]["program"]["targetMinutes"] == 60, out)
        code, out = eng("advance", run3, "--speaker", "Janson", "--private", "outline",
                        "--say", "Outline your 20-minute talk in three parts.")
        check("private preparation is issued and kept out of the transcript", code == 0 and out["issue"][0]["visible"] is False)
        speak(out, "")
        code, out = eng("advance", run3, "--speaker", "Gene Alford", "--segment", "1", "--say", "-",
                        stdin="Welcome everyone and introduce Janson in two sentences.")
        sheet = open(out["issue"][0]["sheet"]).read()
        check("the instruction (from stdin) reaches the speaker with the setting and the paper",
              "introduce Janson in two sentences" in sheet and "faculty workshop at a law school" in sheet
              and "Silence in law is ambiguous" in sheet, sheet[:300])
        speak(out, "Welcome. Janson will present today. " * 10)
        code, out = eng("advance", run3, "--speaker", "Janson", "--say",
                        "Give the first part of your talk, from {{outline}}, about 900 words.")
        check("the full text of each spoken turn comes back to the showrunner",
              out["committed"][0].get("text", "").startswith("Welcome. Janson will present today."), out["committed"])
        seg = out["progress"]["program"]["segment"]
        check("time is kept against the segment's target", seg["n"] == 1 and seg["targetMinutes"] == 20
              and seg["minutesSoFar"] > 0, seg)
        speak(out, "Here is the first part of the talk. " * 30)
        revised = dict(PROGRAM, segments=PROGRAM["segments"] + [{"title": "Lightning round", "minutes": 5, "plan": "x"}],
                       roles=PROGRAM["roles"] + [{"name": "Rigel Milford", "description": "A visiting scholar."}])
        with open(prog, "w") as f:
            json.dump(revised, f)
        code, out = eng("program", run3, "--file", prog)
        check("the plan can change mid-run: a new segment and a new voice", code == 0
              and out["addedRoles"] == ["Rigel Milford"] and len(out["segments"]) == 3, out)
        recast = dict(revised, roles=[dict(r, description="Changed.") if r["name"] == "Gene Alford" else r
                                      for r in revised["roles"]])
        with open(prog, "w") as f:
            json.dump(recast, f)
        code, out = eng("program", run3, "--file", prog)
        check("...but a character who has spoken keeps their card", code == 1 and "Gene Alford" in out["error"], out)
        code, out = eng("advance", run3, "--speaker", "Abney", "--speaker", "Rigel", "--private", "question",
                        "--say", "Note the question you most want to ask.")
        check("several speakers can prepare at once, privately", code == 0 and len(out["issue"]) == 2
              and {i["role"] for i in out["issue"]} == {"Abney Weathertop", "Rigel Milford"}, out)
        speak(out, "")
        code, out = eng("advance", run3, "--speaker", "Abney", "--speaker", "Rigel", "--say", "Ask.")
        check("...but spoken turns go one at a time", code == 1 and "one at a time" in out["error"], out)
        code, out = eng("advance", run3, "--speaker", "Rigel", "--segment", "2", "--say", "Ask {{question_rigel-milford}}.")
        speak(out, "Janson, Rigel here. Doesn't silence sometimes mean yes?")
        code, out = eng("advance", run3, "--speaker", "Gene", "--last", "--say", "Thank everyone and close.")
        check("the program doesn't end until the showrunner says so", out.get("done") is False)
        speak(out, "Thank you all.")
        code, out = eng("advance", run3)
        check("...and ends after the turn marked --last", out.get("done") is True, out)
        code, out = eng("finalize", run3)
        transcript = open(out["transcript"]).read() if code == 0 else ""
        check("finalize writes the transcript, spoken turns only", transcript.startswith("[Gene Alford]: Welcome.")
              and "Private notes" not in transcript and transcript.rstrip().endswith("[Gene Alford]: Thank you all."),
              transcript[:200])
        check("...and lists where each segment began, for chapters",
              [(s["title"], s["startTurn"]) for s in out["segments"]] == [("The talk", 2), ("Questions", 6)], out.get("segments"))
        check("...and every speaker saw the whole conversation", out["viewAudit"].get("ok") is True, out["viewAudit"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d passed, %d failed" % (passed, len(failed)))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
