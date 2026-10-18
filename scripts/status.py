#!/usr/bin/env python3
"""What's set up for enTalkenator on this computer. Local checks only: no network.

  status.py              JSON
  status.py --hook       a few lines of plain text for Claude at session start
  status.py --welcomed   remember that the first-run welcome has been given

Python 3.9+, standard library only.
"""

import glob
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import voices  # noqa: E402
ENT_HOME = os.environ.get("ENTALKENATOR_HOME", os.path.expanduser("~/.cache/entalkenator"))
CONFIG_DIR = os.environ.get("ENTALKENATOR_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".config", "entalkenator")
STATE = os.path.join(CONFIG_DIR, "setup.json")


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def version():
    try:
        with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "?"


def node():
    exe = shutil.which("node")
    if not exe:
        return None
    try:
        return subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=5).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def where():
    """'mac', 'cowork' (Cowork mounts connected folders at /sessions/<name>/mnt/ in a Linux VM), or 'other'."""
    if sys.platform == "darwin":
        return "mac"
    for path in (ROOT, os.getcwd()):
        if path.startswith("/sessions/") and "/mnt/" in path:
            return "cowork"
    return "other"


START_HERE = ("In the Claude desktop app on their Mac, open the Code tab, choose the enTalkenator folder, "
              "say yes when it asks whether to trust the folder, and say \"Set me up\". "
              "(Or, in Terminal: cd into the folder and run claude.)")


def _prefs():
    import prefs
    data = prefs.load()
    notes = data.pop("notes", [])
    return {"settings": data, "notes": notes}


def gather():
    if sys.platform != "darwin":
        # Nothing else here applies off the Mac, and listing it would invite setting it up.
        return {"version": version(), "folder": ROOT, "mac": False, "where": where(), "startHere": START_HERE}
    lib_path = os.environ.get("ENTALKENATOR_LIBRARY_CONFIG") or os.path.join(CONFIG_DIR, "library.json")
    lib = read_json(lib_path)
    kokoro = bool(glob.glob(os.path.join(ENT_HOME, "venv", "lib", "python3.*", "site-packages", "mlx_audio")))
    wrangler = os.path.exists(os.path.join(ENT_HOME, "wrangler", "node_modules", ".bin", "wrangler"))
    runs = sorted((p for p in glob.glob(os.path.join(ROOT, "programs", "*", "state.json"))), key=os.path.getmtime)
    apple_silicon = sys.platform == "darwin" and os.uname().machine == "arm64"
    hw = voices.hardware()
    ready = [o["id"] for o in voices.OPTIONS if not voices.needs_for(o, hw)]
    chosen = voices.chosen_default()
    return {
        "version": version(),
        "voiceChoice": chosen,
        "voiceChoiceLabel": voices.BY_ID[chosen]["label"] if chosen else None,
        "voicesReady": ready,
        "folder": ROOT,
        "mac": sys.platform == "darwin",
        "where": where(),
        "startHere": None if sys.platform == "darwin" else START_HERE,
        "appleSilicon": apple_silicon,
        "voices": "kokoro" if kokoro else "none",
        "geminiKey": bool(os.environ.get("GEMINI_API_KEY")),
        "library": {"configured": bool(lib.get("url") and lib.get("token")), "url": lib.get("url"),
                    "passphraseSet": lib.get("passphraseSet"), "authMode": lib.get("authMode"),
                    "deployedVersion": lib.get("deployedVersion")},
        "node": node(),
        "wrangler": wrangler,
        "programs": len(runs),
        "latestProgram": os.path.relpath(os.path.dirname(runs[-1]), ROOT) if runs else None,
        "welcomed": bool(read_json(STATE).get("welcomed")),
        "preferences": _prefs(),
    }


def not_here_text(s):
    sandbox = " This looks like Cowork, which runs commands in a Linux sandbox." if s["where"] == "cowork" else ""
    return ("enTalkenator %s: NOT on the person's Mac.%s enTalkenator runs only in Claude Code on a Mac: it needs "
            "the Mac's Keychain, PDF tools and voices, and anything installed here would never reach their Mac. "
            "Don't install, set up, or make anything here. Tell them, in plain words, where to run it instead: %s"
            % (s["version"], sandbox, START_HERE))


def hook_text(s):
    if not s["mac"]:
        return not_here_text(s)
    lines = ["enTalkenator %s, status at session start (local checks, no network):" % s["version"]]
    ready = ", ".join(s["voicesReady"]) or "none"
    if s["voiceChoice"]:
        state = "ready" if s["voiceChoice"] in s["voicesReady"] else "NOT ready on this Mac (falls back to free voices)"
        lines.append("- Voices: the person chose %s (%s). Ready here: %s." % (s["voiceChoiceLabel"], state, ready))
    else:
        lines.append("- Voices: no choice made yet (`voices.py list` has the options). Ready here: %s." % ready)
    lib = s["library"]
    if lib["configured"]:
        extra = ""
        if lib["authMode"] == "passphrase" and not lib["passphraseSet"]:
            extra = " Its web app passphrase hasn't been set yet."
        if lib["deployedVersion"] and lib["deployedVersion"] != s["version"]:
            extra += " It runs enTalkenator %s; this folder is %s, so offer to update it (deploy again)." % (
                lib["deployedVersion"], s["version"])
        lines.append("- Library: set up at %s.%s" % (lib["url"], extra))
    else:
        lines.append("- Library: not set up (optional; it's what lets programs play on a phone or in a podcast app).")
    lines.append("- Programs made in this folder: %d%s" % (
        s["programs"], " (latest: %s)" % s["latestProgram"] if s["latestProgram"] else ""))
    pr = s.get("preferences") or {}
    if pr.get("settings") or pr.get("notes"):
        bits = ["%s=%s" % kv for kv in sorted(pr["settings"].items())] + ['"%s"' % n["text"] for n in pr["notes"]]
        lines.append("- Preferences (follow them; `prefs.py` to change): " + "; ".join(bits))
    if not s["welcomed"]:
        lines.append("- First visit: the person hasn't been welcomed yet. Follow \"First visit\" in CLAUDE.md.")
    return "\n".join(lines)


def main():
    if "--welcomed" in sys.argv:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        state = read_json(STATE)
        state["welcomed"] = True
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        print(json.dumps({"welcomed": True}))
        return
    s = gather()
    if "--hook" in sys.argv:
        print(hook_text(s))
    else:
        print(json.dumps(s, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # a session-start hook must never get in the way
        if "--hook" in sys.argv:
            print("enTalkenator: couldn't read the setup status (%s)." % e)
        else:
            raise
