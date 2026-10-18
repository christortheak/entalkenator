#!/usr/bin/env python3
"""The person's lasting preferences, so Claude doesn't ask twice and can work without asking at all.

  prefs.py show
  prefs.py set KEY=VALUE [KEY=VALUE ...]     e.g. publish=always questions=none chapters.narration=main-parts
  prefs.py note "TEXT"                       a preference in the person's own words
  prefs.py forget N                          drop note N (as numbered by show)
  prefs.py unset KEY

Settings (anything else goes in a note):
  questions           ask-when-it-matters (default) | none (use best judgment, never ask)
  publish             ask (default when a library exists) | always | never
  tags                comma-separated tags to add when publishing
  narration.notes     after-paragraph | section-end | none        (footnotes in narrations)
  narration.citations drop | names | keep
  narration.figures   describe | skip
  chapters.narration  all-headings (default) | main-parts (top two levels) | top-level
  chapters.conversation  topics (default: a chapter per question or phase) | few (just the big parts)
  turns               natural (default: whoever would speak up next) | template (a template's random pick)

Kept in ~/.config/entalkenator/preferences.json. Python 3.9+, standard library only.
"""

import json
import os
import sys
import time

CONFIG_DIR = os.environ.get("ENTALKENATOR_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".config", "entalkenator")
PATH = os.path.join(CONFIG_DIR, "preferences.json")
OLD_NARRATION = os.path.join(CONFIG_DIR, "narration.json")

CHOICES = {
    "questions": {"ask-when-it-matters", "none"},
    "publish": {"ask", "always", "never"},
    "narration.notes": {"after-paragraph", "section-end", "none"},
    "narration.citations": {"drop", "names", "keep"},
    "narration.figures": {"describe", "skip"},
    "chapters.narration": {"all-headings", "main-parts", "top-level"},
    "chapters.conversation": {"topics", "few"},
    "turns": {"natural", "template"},
}
FREE = {"tags"}
# How deep a narration's chapters go, by chapters.narration.
CHAPTER_DEPTH = {"all-headings": 4, "main-parts": 2, "top-level": 1}


def load():
    try:
        with open(PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    data = {}
    try:  # the earlier, narration-only file
        with open(OLD_NARRATION, encoding="utf-8") as f:
            old = json.load(f)
        for k in ("notes", "citations", "figures"):
            if old.get(k):
                data["narration." + k] = old[k]
    except (OSError, ValueError):
        pass
    return data


def save(data):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, PATH)


def get(key, default=None):
    return load().get(key, default)


def set_values(pairs):
    data = load()
    changed = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError("Write settings as KEY=VALUE: %r" % pair)
        k, v = pair.split("=", 1)
        k, v = k.strip(), v.strip()
        if k in CHOICES and v not in CHOICES[k]:
            raise ValueError("%s must be one of: %s" % (k, ", ".join(sorted(CHOICES[k]))))
        if k not in CHOICES and k not in FREE:
            raise ValueError("Unknown setting %r; record it as a note instead." % k)
        data[k] = v
        changed[k] = v
    save(data)
    return changed


def emit(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def main():
    args = sys.argv[1:]
    if not args or args[0] == "show":
        data = load()
        notes = data.pop("notes", [])
        emit({"settings": data, "notes": [{"n": i + 1, **n} for i, n in enumerate(notes)], "file": PATH})
        return
    cmd = args[0]
    try:
        if cmd == "set":
            emit({"ok": True, "set": set_values(args[1:])})
        elif cmd == "unset":
            data = load()
            removed = [k for k in args[1:] if data.pop(k, None) is not None]
            save(data)
            emit({"ok": True, "removed": removed})
        elif cmd == "note":
            text = " ".join(args[1:]).strip()
            if not text:
                raise ValueError("note needs some text")
            data = load()
            data.setdefault("notes", []).append({"text": text, "added": time.strftime("%Y-%m-%d")})
            save(data)
            emit({"ok": True, "notes": len(data["notes"])})
        elif cmd == "forget":
            data = load()
            notes = data.get("notes", [])
            n = int(args[1])
            if not 1 <= n <= len(notes):
                raise ValueError("No note %d" % n)
            gone = notes.pop(n - 1)
            save(data)
            emit({"ok": True, "forgot": gone["text"]})
        else:
            raise SystemExit(__doc__)
    except ValueError as e:
        emit({"ok": False, "error": str(e)})
        sys.exit(1)


if __name__ == "__main__":
    main()
