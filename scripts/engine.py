#!/usr/bin/env python3
"""enTalkenator template engine for Claude Code.

Runs an enTalkenator template (enTalkenator/Templates/*.json) one step at a time
so that Claude, not this script, writes every line. The script owns the
bookkeeping the Swift app does in Template.generateTranscript: placeholder
substitution, hidden steps, outputKey variables, and duration-driven loops. It
writes a prompt file for each step and folds the written answer back in.

Standard library only; runs on the macOS system Python (3.9) and newer.

Modes (init --mode):
  cast    one persistent speaker agent per role (default). A role's first turn gets
          the full sheet (*.prompt.md); later turns get a delta sheet (*.delta.md)
          with only what was said since that role last spoke plus its instruction.
  fresh   a new speaker agent every turn, full sheet each time (the app's behaviour)
  single  the showrunner writes every turn from a short per-step instruction

Directed programs (init --program FILE) have no fixed steps. The showrunner writes the
program (setting, roles, and segments with target minutes, see PROGRAM below), then directs
every turn: who speaks and what the moment calls for (`advance --speaker NAME --say -`, the
instruction on stdin). It can revise the plan as the conversation goes (`program RUN --file`).
The engine keeps the bookkeeping it keeps for templates: the speakers' sheets and deltas,
director's notes, timing against each segment's target, and the transcript.

Turn-taking (init --floor):
  template  where a step lists several roles, pick one at random (the app's behaviour)
  natural   the showrunner gives the floor to whoever would naturally speak next: `next`
            returns `choose` (the candidates, how much and how recently each has spoken,
            whether the last speaker addressed them), and `next --speaker NAME` issues it.
            A step's candidates are every role listed on a visible step with the same
            prompt: the template's interchangeable seats.

Subcommands (all print JSON on stdout):
  templates                       list the bundled templates
  init     --template T | --program FILE, --source --run-dir [...]
                                  extract the document, create the run
  program  RUN --file FILE        revise a directed program: segments, new roles, lead
  meta     RUN [--title] [--author] [--user-input]
                                  fix title, author, or user input before the first step
  roles    RUN                    show the characters as they'll be played
  role     RUN --role R [--description | --sketch | --reset]
                                  review a character before its first turn
  next     RUN [--batch] [--speaker NAME]
                                  issue the next step(s); writes turn sheets
  commit   RUN                    fold the written outputs of the pending step(s) in
  advance  RUN [--batch] [--cast ROLE=AGENT_ID ...] [--speaker NAME]
           [--say TEXT|-] [--private KEY] [--segment N] [--last]   (directed programs)
           [--delivery NOTE] [--cuts-in]     how the turn should sound, for the voices
                                  record speaker agents, commit, then issue the next step(s)
  status   RUN                    progress summary
  finalize RUN                    write transcript.txt in the app's "[Name]: text" format
  package  RUN --audio M4A [--out DIR]
                                  build an .enTalkenator bundle the app can import
  verify   BUNDLE                 replay the app's import logic on a bundle
  watchdog RUN                    background stall detector (exits on a stall or at the end)
  nudge    RUN --turn N           restart a stalled turn's clock; prints the reminder to send
  recast   RUN --turn N           re-issue a stalled turn to a fresh speaker
"""

import argparse
import datetime as _dt
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import textwrap
import time
import uuid
import zipfile

WORDS_PER_MINUTE = 175.0  # Template.handleLoopForDuration
END_MARKER = "=== END OF TURN SHEET ==="

# Constants.outputTranscriptPromptAddition, verbatim from the app.
OUTPUT_TRANSCRIPT_PROMPT_ADDITION = (
    '      You may notice that speakers are indicated as "[Speaker]:". That designation is added to the '
    "transcript automatically by others and will include the name of the speaker. You should not include "
    "that designation in your output. Rather, your output should consist solely of the words spoken (or "
    "written if the instructions make it clear you have been asked to produce a written paper)."
)
NO_CONVERSATION_YET = "There has been no conversation yet."

# Directed programs: the shared instruction and transcript framing the bundled templates use.
DIRECTED_GENERAL = ("Everything you output will be turned into audio. Output ONLY the words you say out loud: "
                    "no descriptions of actions or gestures, no stage directions, no speaker labels, no headings "
                    "or markdown. Don't comment on your output or on these instructions. The different speakers "
                    "will be voiced by different people, so there is no need to say who is speaking.")
DIRECTED_TRANSCRIPT = ("Here is everything that has been said so far: \n\n{{output}}\n\n-----\n\n"
                       "And that is everything that has been said so far. ")

# The enTalkenator folder: scripts/, templates/, .claude/agents/ ...
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEAKER_AGENT = "speaker"


# ---------------------------------------------------------------------------
# helpers


def emit(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def fail(msg, code=1):
    emit({"error": msg})
    sys.exit(code)


def now():
    return time.time()


def slug(text, maxlen=40):
    s = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return s[:maxlen] or "role"


def word_count(text):
    return len(text.split())


def read_text(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def write_text(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def wrap_long_lines(text, limit=1500, width=1000):
    """Claude Code's Read tool truncates very long lines, so hard-wrap them."""
    out = []
    for line in text.split("\n"):
        if len(line) > limit:
            out.append(textwrap.fill(line, width=width, break_long_words=False, break_on_hyphens=False))
        else:
            out.append(line)
    return "\n".join(out)


def find_templates_dir(explicit=None):
    candidates = []
    if explicit:
        candidates.append(explicit)
    if os.environ.get("ENTALKENATOR_TEMPLATES"):
        candidates.append(os.environ["ENTALKENATOR_TEMPLATES"])
    # The templates that ship with enTalkenator (a snapshot of the app's).
    candidates.append(os.path.join(ROOT, "templates"))
    for c in candidates:
        if c and os.path.isdir(c) and any(n.endswith(".json") for n in os.listdir(c)):
            return c
    fail("No templates directory found. Pass --templates-dir or set ENTALKENATOR_TEMPLATES.")


def load_template_file(path):
    with open(path, "r", encoding="utf-8") as f:
        t = json.load(f)
    for key in ("id", "displayName", "initialContext", "outputTranscriptPrompt", "roles", "steps"):
        if key not in t:
            fail("Template %s is missing %r" % (path, key))
    return t


def resolve_template(name, templates_dir):
    if name.endswith(".json") and os.path.isfile(name):
        return os.path.abspath(name), load_template_file(name)
    want = name.strip().lower()
    for fn in sorted(os.listdir(templates_dir)):
        if not fn.endswith(".json"):
            continue
        path = os.path.join(templates_dir, fn)
        try:
            t = load_template_file(path)
        except SystemExit:
            continue
        keys = {fn[:-5].lower(), str(t.get("id", "")).lower(), str(t.get("displayName", "")).lower()}
        if want in keys:
            return path, t
    fail("Template %r not found in %s. Run `engine.py templates` to list them." % (name, templates_dir))


def loop_value(step):
    """RawStep.loop: an Int is .start(minutes), the string "end" is .end."""
    v = step.get("loop")
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return ("start", float(v))
    if isinstance(v, str):
        if v.strip().lower() == "end":
            return ("end", None)
        try:
            return ("start", float(v))
        except ValueError:
            return None
    return None


def is_visible(step):
    # The app compares the string to "false"; anything else (or missing) is visible.
    return step.get("includeInTranscript") != "false"


PLACEHOLDER_RE = re.compile(r"\{\{([^{}]+)\}\}")


def replace_placeholders(text, variables, passes=3):
    """PlaceholderHelper.replacePlaceholders, made order-independent.

    The app substitutes each known key once, in dictionary order, so a value that
    itself contains {{key}} may or may not be expanded. Here known keys are
    expanded repeatedly (up to `passes` rounds). Unknown keys are left as is,
    exactly as the app does.
    """
    for _ in range(passes):
        changed = [False]

        def sub(m):
            key = m.group(1)
            if key in variables:
                changed[0] = True
                return variables[key]
            return m.group(0)

        text = PLACEHOLDER_RE.sub(sub, text)
        if not changed[0]:
            break
    return text


# ---------------------------------------------------------------------------
# document extraction


JXA_PDF = r"""
ObjC.import('PDFKit');
function run(argv) {
  var doc = $.PDFDocument.alloc.initWithURL($.NSURL.fileURLWithPath(argv[0]));
  if (!doc || doc.isNil()) { return ''; }
  var s = doc.string;
  return (s && !s.isNil()) ? s.js : '';
}
"""


def extract_pdf_text(path):
    """PDFKit via JavaScript for Automation (no installs needed on macOS)."""
    if sys.platform == "darwin" and shutil.which("osascript"):
        try:
            out = subprocess.run(
                ["osascript", "-l", "JavaScript", "-e", JXA_PDF, path],
                capture_output=True, text=True, timeout=300,
            )
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout
        except Exception:
            pass
    if shutil.which("pdftotext"):
        out = subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True, text=True)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout
    return ""


def clean_document_text(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


# ---------------------------------------------------------------------------
# state


def state_path(run):
    return os.path.join(run, "state.json")


def load_state(run):
    p = state_path(run)
    if not os.path.isfile(p):
        fail("No run at %s (missing state.json). Run `engine.py init` first." % run)
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(run, st):
    tmp = state_path(run) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2, ensure_ascii=False)
    os.replace(tmp, state_path(run))


def template_of(st):
    return load_template_file(st["template_path"])


def document_variable(st):
    """Template.generateTranscript prepends author then title to the document."""
    text = read_text(os.path.join(st["run_dir"], "document.txt"))
    author = st.get("author") or ""
    title = st.get("title") or ""
    if author and author not in ("Unknown Author",):
        text = "The author of the text is %s.\n" % author + text
    if title and title not in ("Untitled Text",):
        text = "The title of the text is %s.\n" % title + text
    return text


def variables_of(st, template):
    v = dict(template["initialContext"])
    v["transcript"] = document_variable(st)
    v.update(st["variables"])  # outputKey values written so far
    if st.get("user_input"):
        v["userInput"] = st["user_input"]
    elif "userInput" not in v:
        # The app leaves "{{userInput}}" in the prompt when nothing was entered.
        # Templates already say "if it was blank...", so substitute an empty string.
        v["userInput"] = ""
    return v


def language_prompt(st):
    lang = st.get("language") or ""
    return (" All of your output should be in the %s language, translating if necessary. " % lang) if lang else ""


def static_content(st, template, variables):
    joined = "\n\n".join(template["initialContext"].values())
    return language_prompt(st) + replace_placeholders(joined, variables)


def transcript_block(template, history):
    """handleCachedGeneration: outputTranscriptPrompt split on {{output}}."""
    if not history:
        return NO_CONVERSATION_YET
    otp = template["outputTranscriptPrompt"]
    parts = otp.split("{{output}}")
    if len(parts) != 2:
        prefix, suffix = "Here is everything that has been said so far:\n\n", "\n\nAnd that is everything that has been said so far."
    else:
        prefix, suffix = parts
    body = "\n\n".join("[%s]: %s" % (h["role"], h["text"]) for h in history)
    return prefix + body + suffix + OUTPUT_TRANSCRIPT_PROMPT_ADDITION


FORMS = ("debate", "hearing", "conversation", "workshop", "class", "quiz", "written")

FORM_LABELS = {
    "debate": "a moderated debate",
    "hearing": "a courtroom hearing (oral argument)",
    "conversation": "a conversation (podcast, chat, or panel)",
    "workshop": "an academic workshop",
    "class": "a class (lecture or seminar)",
    "quiz": "a quiz show",
    "written": "a written piece read aloud as narration",
}

PERFORMANCE = {
    "debate": [
        "React before you argue: say what you make of what was just said, in your own words. Don't open by quoting the other side's line back at them.",
        "Let the moment set the length. A quick jab can be a sentence or two; a real question deserves a fuller answer.",
        "Make one or two points, not a numbered rebuttal. It's fine to let a minor point go.",
        "Concede only when you're actually persuaded, and say it your own way.",
        "Use a memorable image or phrase once; don't keep calling back to it.",
        "Talk like you talk: contractions, sentences of different lengths, the odd \"Look,\" or \"Well,\".",
    ],
    "hearing": [
        "This is a courtroom. Counsel address the bench (\"Your Honor\") and answer the question actually asked, first and directly, before returning to their argument. They never interrupt a judge.",
        "Judges ask short, pointed questions, follow up when an answer dodges, and test arguments with hypotheticals. They don't make speeches, and they rarely argue with each other at length.",
        "Counsel speak formally but plainly: complete sentences, few contractions, no rhetorical flourishes.",
        "Concede what has to be conceded and move to the ground you can hold; that's what good advocates do.",
    ],
    "conversation": [
        "This is a conversation between people who enjoy ideas, not a contest. Build on what was just said as often as you push back; agreeing and adding is natural.",
        "Follow the other person's thread and ask real questions. Bring your own examples.",
        "Casual spoken register: contractions, short sentences, the odd aside. Vary the length of your turns.",
        "Don't summarize the conversation back to each other, don't wrap up every turn, and skip gushing filler (\"Great point!\", \"Absolutely!\", \"I love that\").",
    ],
    "workshop": [
        "An academic workshop among colleagues who've read the paper. Discussants raise one sharp, specific question or comment at a time, and may disagree with each other.",
        "The presenter answers the question asked, concedes what's fair, and defends what matters, without re-delivering the talk.",
        "Collegial but candid, and spoken: contractions, plain words, no reading from notes.",
        "Skip praise filler (\"Great question\", \"Thank you for that wonderful paper\") beyond a brief courtesy.",
    ],
    "class": [
        "A class. The teacher explains, signposts, and checks understanding; repeating a key idea in new words is good teaching, not a fault.",
        "Students sound like real students: some confident, some unsure. They ask genuine questions, sometimes get things partly wrong, and keep their turns short.",
        "The teacher answers the actual question, by name, and ties it back to the material. Don't open every reply with \"Great question\".",
        "Spoken register: contractions, varied sentence length, no reading out of lists.",
    ],
    "quiz": [
        "A quiz show. The host keeps it brisk and warm: one clear question at a time, the right answer confirmed, the score kept aloud when the format calls for it.",
        "Contestants answer briefly. They sometimes hesitate, guess, or get it wrong, and react to the result. Banter is short.",
    ],
    "written": [
        "This is a finished written piece that will be read aloud as narration. Write polished prose in the author's voice: clear topic sentences, signposting between sections, varied sentence length.",
        "No headings, bullet lists, bracketed citations, or footnote-style asides. Say in words anything a reader would otherwise see on the page.",
        "Don't address the listener as an audience or mention that this is audio. A formal register is fine; no conversational tics.",
    ],
}

LEAD_NOTES = {
    "debate": "You're the moderator: steer, ask, summarize both sides fairly, and keep time. Don't argue either side's case or make a rebuttal for one of them.",
    "hearing": "You're presiding: keep the argument moving, manage time, and make sure each side answers the bench's real concerns.",
    "conversation": "You're the host: draw the other person out, keep it moving, and share your own view sparingly.",
    "workshop": "You're moderating: keep the queue moving, call on people, and don't evaluate the paper yourself.",
    "class": "You're the teacher: you lead, but leave room for students to think aloud.",
    "quiz": "You're the host: keep time and score, and keep the energy up without hogging the floor.",
}

# Director checks that make sense for each form.
FORM_CHECKS = {
    "debate": {"echo", "stock_adversarial", "device", "self", "length", "time"},
    "hearing": {"echo", "device", "self", "length", "time"},
    "conversation": {"echo", "stock_gush", "device", "self", "length", "time"},
    "workshop": {"echo", "stock_gush", "device", "self", "length", "time"},
    "class": {"stock_gush", "device", "length", "time"},
    "quiz": {"length", "time"},
    "written": {"device", "self", "length"},
}


def guess_form(template):
    """Best guess at what kind of program a template makes (the showrunner can override)."""
    text = " ".join([template.get("displayName", "")] + list(template["initialContext"].values())
                    + [st_["promptTemplate"] for st_ in template["steps"]]).lower()
    roles = " ".join(r["name"] + " " + r["roleDescription"] for r in template["roles"]).lower()
    if len(template["roles"]) == 1:
        return "written" if not re.search(r"\b(lecture|class|podcast|talk show)\b", text) else "class"
    if re.search(r"\b(quiz|contestants?)\b", text):
        return "quiz"
    if re.search(r"\bjudge", roles) and re.search(r"\b(attorney|counsel|appellant|appellee)", roles):
        return "hearing"
    if re.search(r"\bdebate\b", text):
        return "debate"
    if re.search(r"\b(workshop|hot bench|presenter)\b", text + " " + roles):
        return "workshop"
    if re.search(r"\b(professor|teacher|instructor|lecturer)\b", roles) and re.search(r"\b(class|lecture|seminar|students?)\b", text):
        return "class"
    return "conversation"


OPENER_RE = re.compile(r"\b(welcom\w*|introduc\w*|announc\w*|has just begun|is just beginning|is beginning|"
                       r"get it started|call (?:on|the))\b", re.I)


def lead_role(template):
    """The role that opens the program as its host: whoever speaks the first visible step,
    if that step welcomes, introduces, or announces. (Role descriptions mention each other,
    so they're a poor guide.)"""
    if template.get("lead"):
        return template["lead"]
    for step in template["steps"]:
        if is_visible(step):
            name = step["roleName"].strip()
            return name if "," not in name and OPENER_RE.search(step["promptTemplate"]) else None
    return None


def is_lead(template, role):
    return role["name"] == lead_role(template)


FLOOR_NOTE = ("Who speaks next isn't fixed: the floor goes to whoever would naturally come in. You may answer "
              "someone directly, address a colleague by name, or put a question to them, and they'll usually "
              "speak next. This replaces any instruction not to put questions to particular people.")


def performance_block(st, template, role):
    form = st.get("form") or guess_form(template)
    lines = list(PERFORMANCE.get(form, []))
    if form in LEAD_NOTES and len(template["roles"]) > 1 and is_lead(template, role):
        lines.append(LEAD_NOTES[form])
    if (st.get("directed") and len(template["roles"]) > 1) or (st.get("floor") == "natural" and any(
            is_floor_step(template, i) and role["name"] in floor_candidates(template, i)
            for i in range(len(template["steps"])))):
        lines.append(FLOOR_NOTE)
    return ("PERFORMANCE NOTES (this production is %s; follow these all the way through):\n%s\n"
            % (FORM_LABELS.get(form, form), "\n".join("- " + l for l in lines)))


def effective_role(st, template, name):
    """The template's role with the user's reviewed character applied.

    A replaced `description` stands in for roleDescription everywhere the app would use
    it; a `sketch` (voice, background, habits) is added to the speaker's role card only.
    """
    role = role_by_name(template, name)
    if role is None:
        return None
    role = dict(role)
    ov = st.get("role_overrides", {}).get(name, {})
    if ov.get("description"):
        role["roleDescription"] = ov["description"]
    role["sketch"] = ov.get("sketch", "")
    return role


STOPWORDS = set("""a an the of to in on for and or but is are was were be been being it its it's that that's
this these those with as at by from into about than then so if not no do does did don't doesn't didn't have has
had i i'm i'll i've i'd you you're you'll you've he she they we me my your our their his her them us what which
who whom whose when where why how can can't could would should will won't shall may might must just also very
more most much many some any all each every one two three there here there's let let's get got make made say said
says go going know think well yes now only even still because like over out up down off same other another own
again too back way thing things something nothing anything everyone someone people""".split())

STOCK_ADVERSARIAL = ["fair hit", "fair point", "fair question", "fair challenge", "fair enough", "i'll grant you",
                     "i'll take that", "i'll give you that", "take the hit", "credit where due", "let me be clear",
                     "here's the thing", "make no mistake", "at the end of the day", "to be fair"]
STOCK_GUSH = ["great point", "great question", "good question", "excellent question", "excellent point",
              "absolutely", "i love that", "that's fascinating", "so interesting", "couldn't agree more",
              "exactly right", "you're so right", "what a great"]
NOT_IT_IS = re.compile(r"\b(not|n't)\b[^.?!]{0,80}[.?!]\s+(It|That)('s| is| was)\b")


def _words(text):
    return re.findall(r"[a-z][a-z']*", text.lower())


def _content_ngrams(text, n):
    w = _words(text)
    out = []
    for i in range(len(w) - n + 1):
        g = w[i:i + n]
        if g[0] in STOPWORDS or g[-1] in STOPWORDS or sum(x not in STOPWORDS for x in g) < 2:
            continue
        out.append(" ".join(g))
    return out


def _sentences(text):
    return [x for x in re.split(r"(?<=[.?!])\s+", " ".join(text.split())) if x]


COURTESY = set("thank thanks evening morning afternoon welcome good great glad pleasure sure okay right indeed "
               "hello honored delighted appreciate".split())


def _is_echo_opener(turn_text, prev_text, names=()):
    """Does the turn open by quoting the previous speaker's words back at them?"""
    sents = _sentences(turn_text)
    if not sents or not prev_text:
        return False
    first = sents[0]
    if len(first.split()) > 12:
        return False
    cw = [x for x in _words(first) if x not in STOPWORDS and x not in names and x not in COURTESY and len(x) > 2]
    if not cw:
        return False
    prev = set(x for x in _words(prev_text) if x not in STOPWORDS)
    return sum(x in prev for x in cw) / float(len(cw)) >= 0.6


def director_notes(st, template, step, role_name):
    """Short private notes for the speaker about to take a spoken turn.

    Deterministic, high-precision checks for habits blind judges flagged: opening by
    quoting the other side back, stock concessions, one rhetorical device on repeat,
    repeating one's own earlier line, and turns running long where the template asks
    for brevity. (Telling a decorative refrain from a term the argument is about takes
    judgment, so that is left to the speaker brief.) Each note goes to a speaker once
    (length notes at most twice). A moderator or host also gets the running time.
    """
    if not st.get("director", True) or not is_visible(step):
        return []
    form = st.get("form") or guess_form(template)
    checks = FORM_CHECKS.get(form, set())
    hist = st["history"]
    given = st.setdefault("director_given", {}).setdefault(role_name, [])
    notes = []
    role = effective_role(st, template, role_name) or {}
    if "time" in checks and len(template["roles"]) > 1 and is_lead(template, role):
        minutes = st["total_words"] / WORDS_PER_MINUTE
        if minutes >= 1:
            notes.append("Running time so far: about %d minutes." % round(minutes))
    mine_idx = [i for i, h in enumerate(hist) if h["role"] == role_name]
    if not mine_idx:
        return notes
    mine = [hist[i]["text"] for i in mine_idx]
    names = set(_words(" ".join(r["name"] for r in template["roles"])))

    # 1. Echo openers: starting by quoting the other side's words back.
    recent = mine_idx[-3:]
    echoes = sum(1 for i in recent if i > 0 and _is_echo_opener(hist[i]["text"], hist[i - 1]["text"], names))
    if "echo" in checks and echoes >= 2 and "echo" not in given:
        notes.append("You've opened %d of your last %d turns by quoting the previous speaker's words back. Start "
                     "this one somewhere else: your reaction, a question, or your own point." % (echoes, len(recent)))
        given.append("echo")

    # 2. Stock concessions and set phrases.
    joined = " ".join(mine).lower().replace("\u2019", "'")
    stock = (STOCK_ADVERSARIAL if "stock_adversarial" in checks else []) + (STOCK_GUSH if "stock_gush" in checks else [])
    used = [ph for ph in stock if re.search(r"\b%s\b" % re.escape(ph), joined)]
    if stock and sum(len(re.findall(r"\b%s\b" % re.escape(ph), joined)) for ph in stock) >= 2 and "stock" not in given:
        notes.append("You've leaned on set phrases (%s). Say it in your own words, or skip it." % (
            ", ".join("'%s'" % u for u in used[:3])))
        given.append("stock")

    # 3. One rhetorical device on repeat.
    dev = sum(len(NOT_IT_IS.findall(t)) for t in mine)
    if "device" in checks and dev >= (4 if form == "written" else 3) and "device" not in given:
        notes.append("You've used the 'That's not X. It's Y.' construction %d times. Vary how you make a point." % dev)
        given.append("device")

    # 4. Repeating one's own earlier line.
    if "self" in checks and len(mine) >= 2:
        def grams(t, n=7):
            w = _words(t)
            return set(" ".join(w[i:i + n]) for i in range(len(w) - n + 1))
        doc = " " + " ".join(_words(read_text(os.path.join(st["run_dir"], "document.txt")))) + " "
        earlier = set().union(*[grams(t) for t in mine[:-1]])
        # Quoting the source document twice is fine; repeating your own wording isn't.
        dup = sorted(g for g in grams(mine[-1]) & earlier if (" %s " % g) not in doc)
        key = "self:%d" % len(mine)
        if dup and key not in given:
            notes.append("Your last turn repeated a line you'd already said (\"%s...\"). Don't make the same point "
                         "in the same words again." % dup[0])
            given.append(key)

    # 5. Length where the template asks for brevity.
    if "length" in checks and re.search(r"\b(brief|short|succinct|concise)\b", step["promptTemplate"], re.I):
        lens = sorted(len(h["text"].split()) for h in hist)
        median = lens[len(lens) // 2]
        last = len(mine[-1].split())
        if last > max(1.5 * median, 220) and given.count("length") < 2:
            notes.append("Your last turn ran %d words; this part of the program asks for brief replies. Keep this "
                         "one well under %d." % (last, int(median)))
            given.append("length")
    return notes


def notes_block(notes):
    if not notes:
        return ""
    return ("Director's notes (private; act on them, never mention them or read them out):\n"
            + "\n".join("- " + n for n in notes) + "\n\n")


def role_by_name(template, name):
    for r in template["roles"]:
        if r["name"] == name:
            return r
    return None


def step_roles(step):
    return [n.strip() for n in step["roleName"].split(",") if n.strip()]


def pick_role(st, step):
    names = step_roles(step)
    if not names:
        fail("Step has no roleName: %r" % step)
    if len(names) == 1:
        return names[0]
    rng = random.Random("%s:%d" % (st["seed"], st["turn_counter"]))
    return rng.choice(names)


def floor_candidates(template, index):
    """Who could take this step: the roles it lists, plus every role listed on another visible
    step with the same prompt (the template's interchangeable seats)."""
    step = template["steps"][index]
    names = step_roles(step)
    if len(names) < 2 or not is_visible(step):
        return names
    for s in template["steps"]:
        if is_visible(s) and s["promptTemplate"] == step["promptTemplate"] and len(step_roles(s)) > 1:
            names += [n for n in step_roles(s) if n not in names]
    return names


def is_floor_step(template, index):
    return is_visible(template["steps"][index]) and len(floor_candidates(template, index)) > 1


def floor_steps(template):
    return sum(1 for i in range(len(template["steps"])) if is_floor_step(template, i))


TITLES = {"the", "judge", "justice", "professor", "prof", "dr", "mr", "mrs", "ms", "host", "guest", "author",
          "moderator", "chair"}


def addressed(text, name):
    """Whether the text names this role (a hint for the showrunner, not a rule)."""
    words = [w for w in name.split() if w.lower().strip(".") not in TITLES and len(w) > 2] or [name]
    return any(re.search(r"\b%s\b" % re.escape(w), text) for w in words)


def floor_rows(st, names):
    """How much and how recently each of these roles has spoken, and whether the last turn named them."""
    history = st["history"]
    last = history[-1] if history else {"role": None, "text": ""}
    rows = []
    for name in names:
        mine = [i for i, h in enumerate(history) if h["role"] == name]
        rows.append({"role": name, "turns": len(mine), "words": sum(word_count(history[i]["text"]) for i in mine),
                     "turnsSince": (len(history) - 1 - mine[-1]) if mine else None,
                     "addressed": bool(last["text"]) and name != last["role"] and addressed(last["text"], name)})
    return rows, last


def floor_choice(st, template, index):
    """The showrunner's choice of the next speaker, or None where there's nothing to choose."""
    if st.get("floor") != "natural" or not is_floor_step(template, index):
        return None
    rows, last = floor_rows(st, floor_candidates(template, index))
    if any(r["role"] != last["role"] for r in rows):
        rows = [r for r in rows if r["role"] != last["role"]]
    words = last["text"].split()
    return {"step": index, "candidates": rows, "lastSpeaker": last["role"],
            "lastWords": ("..." if len(words) > 60 else "") + " ".join(words[-60:])}


def resolve_speaker(choice, step, wanted):
    names = [r["role"] for r in choice["candidates"]] if choice else step_roles(step)
    w = wanted.strip().lower()
    hits = [n for n in names if n.lower() == w] or [n for n in names if w in [x.lower() for x in n.split()]]
    if len(hits) != 1:
        fail("%r can't take this turn. Choose one of: %s" % (wanted, ", ".join(names)))
    return hits[0]


# ---------------------------------------------------------------------------
# directed programs


def load_program(path):
    try:
        with open(path, encoding="utf-8") as f:
            prog = json.load(f)
    except (OSError, ValueError) as e:
        fail("Can't read the program %s: %s" % (path, e))
    problems = []
    if not isinstance(prog.get("setting"), str) or not prog["setting"].strip():
        problems.append("`setting` (who is where, and why) is required")
    roles = prog.get("roles") or []
    names = [r.get("name", "").strip() for r in roles if isinstance(r, dict)]
    if not roles or len(names) != len(roles) or not all(names):
        problems.append("`roles` needs at least one {name, description}")
    elif len(set(names)) != len(names):
        problems.append("role names must be unique")
    for r in roles:
        if isinstance(r, dict) and not (r.get("description") or "").strip():
            problems.append("role %r needs a description" % r.get("name"))
    for i, seg in enumerate(prog.get("segments") or [], 1):
        if not isinstance(seg, dict) or not seg.get("title") or not isinstance(seg.get("minutes"), (int, float)) \
                or seg["minutes"] <= 0:
            problems.append("segment %d needs a title and minutes > 0" % i)
    if not prog.get("segments"):
        problems.append("`segments` needs at least one {title, minutes, plan}")
    if prog.get("form", "conversation") not in FORMS:
        problems.append("`form` must be one of %s" % ", ".join(FORMS))
    if prog.get("lead") and prog["lead"] not in names:
        problems.append("`lead` must be one of the roles")
    if problems:
        fail("The program isn't complete: " + "; ".join(problems) + ".")
    prog.setdefault("form", "conversation")
    return prog


def program_template(prog, old=None):
    """A directed program as a template whose steps are the turns directed so far."""
    setting = prog["setting"].strip()
    if "{{transcript}}" not in setting:
        setting += "\n\nHere is the source material everyone has read:\n\n{{transcript}}\n\n-----\n\n"
    ids = {r["name"]: r.get("id") for r in (old or {}).get("roles", [])}
    return {
        "id": (old or {}).get("id") or str(uuid.uuid4()).upper(),
        "displayName": prog.get("title") or "Custom Program",
        "initialContext": {"generalInstruction": DIRECTED_GENERAL, "setting": setting},
        "outputTranscriptPrompt": DIRECTED_TRANSCRIPT,
        "roles": [{"id": ids.get(r["name"]) or str(uuid.uuid4()).upper(), "name": r["name"].strip(),
                   "roleDescription": r["description"].strip()} for r in prog["roles"]],
        "steps": (old or {}).get("steps", []),
        "lead": prog.get("lead"),
        "useCaching": True,
        "directed": True,
    }


def save_template(st, template):
    with open(st["template_path"], "w", encoding="utf-8") as f:
        json.dump(template, f, indent=2, ensure_ascii=False)


def program_segments(st):
    return [{"n": i, "title": s["title"], "minutes": s["minutes"]} for i, s in enumerate(st["program"]["segments"], 1)]


def program_progress(st):
    segs = st["program"]["segments"]
    p = {"targetMinutes": round(sum(s["minutes"] for s in segs), 1)}
    n = st.get("segment")
    if n:
        start = st["segment_starts"][-1]["atWords"]
        p["segment"] = {"n": n, "title": segs[n - 1]["title"], "targetMinutes": segs[n - 1]["minutes"],
                        "minutesSoFar": round((st["total_words"] - start) / WORDS_PER_MINUTE, 1)}
    if (n or 0) < len(segs):
        p["nextSegment"] = {"n": (n or 0) + 1, "title": segs[n or 0]["title"]}
    return p


def read_say(args):
    say = getattr(args, "say", None)
    if say == "-":
        say = sys.stdin.read()
    return (say or "").strip()


def directed_next(args, st, template, run, mode):
    """Issue the turn(s) the showrunner directs, or show where things stand so it can decide."""
    names = [r["name"] for r in template["roles"]]
    wanted = getattr(args, "speaker", None) or []
    if not wanted:
        rows, last = floor_rows(st, names)
        save_state(run, st)
        emit({"done": False, "mode": mode, "direct": {"roles": rows, "lastSpeaker": last["role"]},
              "progress": progress(st, template),
              "next": "Decide who speaks next and what the moment calls for, then run `engine.py advance %s "
                      "--speaker NAME --say -` with the instruction on stdin (add --segment N when a new "
                      "segment begins, --private KEY for preparation nobody hears, --last for the final turn)." % run})
        return
    say = read_say(args)
    if not say:
        fail("--speaker needs --say: what this speaker is to do in this turn.")
    if len(wanted) > 1 and not args.private:
        fail("Several speakers at once only for private preparation (--private KEY); spoken turns go one at a time.")
    if args.last and (args.private or len(wanted) > 1):
        fail("--last marks the final spoken turn.")
    every = {"roleName": ", ".join(names)}
    chosen = [resolve_speaker(None, every, w) for w in wanted]
    if len(set(chosen)) != len(chosen):
        fail("Each speaker once per batch.")
    if args.segment:
        if not 1 <= args.segment <= len(st["program"]["segments"]):
            fail("--segment must be 1 to %d." % len(st["program"]["segments"]))
        if args.segment != st.get("segment"):
            st["segment"] = args.segment
            st["segment_starts"].append({"segment": args.segment, "turn": st["turn_counter"] + 1,
                                         "atWords": st["total_words"]})
    batch = []
    for name in chosen:
        key = args.private if not args.private or len(chosen) == 1 else "%s_%s" % (args.private, slug(name))
        template["steps"].append({"roleName": name, "promptTemplate": say, "outputKey": key or "output",
                                  "includeInTranscript": "false" if args.private else "true"})
        if not args.private and (args.delivery or args.cuts_in):
            # For the voices, not the speaker: how the line should sound, and whether it cuts in.
            template["steps"][-1]["delivery"] = {k: v for k, v in (("note", (args.delivery or "").strip()),
                                                                   ("cutsIn", bool(args.cuts_in))) if v}
        idx = len(template["steps"]) - 1
        st["current_index"] = idx
        pend, _ = issue(st, template, idx, name)
        pend["last"] = bool(args.last)
        batch.append(pend)
    save_template(st, template)
    st["pending"] = batch
    save_state(run, st)
    emit({"done": False, "mode": mode, "issue": [describe(st, p, mode) for p in batch],
          "progress": progress(st, template)})


def cmd_program(args):
    """Revise a directed program mid-run: segments, lead, new roles. Started roles keep their cards."""
    st = load_state(args.run_dir)
    if not st.get("directed"):
        fail("This run follows a template; only a directed program (init --program) can be revised.")
    prog = load_program(args.file)
    template = template_of(st)
    old = {r["name"]: r["roleDescription"] for r in template["roles"]}
    new = {r["name"].strip(): r["description"].strip() for r in prog["roles"]}
    gone = [n for n in old if n not in new and role_started(st, n)]
    if gone:
        fail("These roles have spoken and can't leave the program: %s" % ", ".join(gone))
    changed = [n for n in old if n in new and new[n] != old[n] and role_started(st, n)]
    if changed:
        fail("These roles have spoken, so their descriptions are fixed (use `role --sketch` to add colour): %s"
             % ", ".join(changed))
    if st.get("segment") and st["segment"] > len(prog["segments"]):
        st["segment"] = len(prog["segments"])
    setting_changed = prog["setting"].strip() != st["program"]["setting"].strip()
    st["program"] = prog
    st["form"] = prog["form"]
    save_template(st, program_template(prog, template))
    save_state(args.run_dir, st)
    emit({"ok": True, "addedRoles": [n for n in new if n not in old], "segments": program_segments(st),
          "note": "The new setting reaches only speakers who haven't had their first turn; tell the others what "
                  "changed in their next instruction." if setting_changed else None})


# ---------------------------------------------------------------------------
# commands


def cmd_templates(args):
    d = find_templates_dir(args.templates_dir)
    out = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".json"):
            continue
        try:
            t = load_template_file(os.path.join(d, fn))
        except SystemExit:
            continue
        loops = [loop_value(s) for s in t["steps"]]
        out.append({
            "name": fn[:-5],
            "displayName": t["displayName"],
            "roles": [r["name"] for r in t["roles"]],
            "steps": len(t["steps"]),
            "loopMinutes": [lv[1] for lv in loops if lv and lv[0] == "start"],
            "userInput": (t.get("userInputConfig") or {}).get("label"),
            "form": guess_form(t),
            "floorSteps": floor_steps(t),
        })
    emit({"templatesDir": d, "templates": out})


def cmd_init(args):
    if bool(args.template) == bool(args.program):
        fail("Give either --template (run one as written) or --program (a program you direct).")
    run = os.path.abspath(args.run_dir)
    prog = None
    if args.program:
        prog = load_program(args.program)
        template = program_template(prog)
        tpath = os.path.join(run, "template.json")
    else:
        tpath, template = resolve_template(args.template, find_templates_dir(args.templates_dir))
    if os.path.exists(state_path(run)) and not args.force:
        fail("%s already holds a run. Use --force to start over, or pick another --run-dir." % run)
    os.makedirs(os.path.join(run, "turns"), exist_ok=True)
    os.makedirs(os.path.join(run, "source"), exist_ok=True)
    if prog:
        with open(tpath, "w", encoding="utf-8") as f:
            json.dump(template, f, indent=2, ensure_ascii=False)

    src = os.path.abspath(args.source)
    if not os.path.isfile(src):
        fail("Source file not found: %s" % src)
    ext = os.path.splitext(src)[1].lower().lstrip(".")
    if ext == "pdf":
        text = extract_pdf_text(src)
        if not text.strip():
            fail("Could not extract text from the PDF (scanned?). Read it with the Read tool, save the text "
                 "to a .txt file, and pass that as --source instead.")
        source_type = "pdf"
    else:
        text = read_text(src)
        source_type = "txt"
    text = clean_document_text(text)
    originals = []
    for o in args.original or []:
        o = os.path.abspath(os.path.expanduser(o))
        if not os.path.isfile(o):
            fail("Original not found: %s" % o)
        originals.append(describe_original(o))
    if len(originals) == 1 and originals[0]["path"].lower().endswith(".pdf") and source_type != "pdf":
        # A prepared reading of one PDF: the bundle carries the PDF, with the reading as its transcript.
        src, source_type = originals[0]["path"], "pdf"
    source_copy = os.path.join(run, "source", "original." + source_type)
    if source_type == "pdf":
        shutil.copyfile(src, source_copy)
    else:
        write_text(source_copy, text)
    write_text(os.path.join(run, "document.txt"), text)

    title = args.title or os.path.splitext(os.path.basename(src))[0]
    models = parse_models(args.model, args.models, template)
    form = args.form if args.form and args.form != "auto" else (prog["form"] if prog else guess_form(template))
    st = {
        "version": 1,
        "run_dir": run,
        "template_path": tpath,
        "template_name": template["displayName"],
        "source_path": src,
        "source_type": source_type,
        "originals": originals,
        "title": title,
        "author": args.author or "Unknown Author",
        "user_input": args.user_input or "",
        "language": args.language or "",
        "loop_scale": args.loop_scale,
        "max_turns": args.max_turns,
        "mode": args.mode,
        "models": models,   # role -> "" (session model), an alias, or a full model ID
        "director": not args.no_director,
        "form": form,
        "floor": args.floor,
        "directed": bool(prog),
        "program": prog,
        "segment": None,
        "segment_starts": [],
        "role_overrides": {},
        "seed": uuid.uuid4().hex,
        "current_index": 0,
        "loop_start_index": None,
        "loop_duration": None,
        "accumulated_words": 0,
        "total_words": 0,
        "turn_counter": 0,
        "variables": {},
        "var_author": {},   # outputKey -> role that wrote it
        "history": [],
        "seen": {},         # role -> how many history entries its speaker agent has seen
        "cast": {},         # role -> agent id of its persistent speaker (cast mode)
        "turns": [],
        "pending": [],
        "done": False,
        "created_at": now(),
        "first_issue_at": None,
        "finished_at": None,
    }
    save_state(run, st)
    doc_words = word_count(text)
    emit({
        "runDir": run,
        "template": template["displayName"],
        "templatePath": tpath,
        "roles": [{"name": r["name"], "description": r["roleDescription"]} for r in template["roles"]],
        "steps": len(template["steps"]),
        "loopMinutes": [round(lv[1] * args.loop_scale, 2) for lv in map(loop_value, template["steps"]) if lv and lv[0] == "start"],
        "userInputConfig": template.get("userInputConfig"),
        "documentWords": doc_words,
        "title": title,
        "author": st["author"],
        "models": {r: (m or "session") for r, m in models.items()},
        "form": form,
        "formLabel": FORM_LABELS[form],
        "floor": args.floor,
        "floorSteps": floor_steps(template),
        "directed": bool(prog),
        "segments": program_segments(st) if prog else None,
        "documentHead": text[:800],
    })


def role_started(st, name):
    return any(t["role"] == name for t in st["turns"]) or any(p["role"] == name for p in st["pending"])


def cmd_roles(args):
    st = load_state(args.run_dir)
    template = template_of(st)
    out = []
    for r in template["roles"]:
        eff = effective_role(st, template, r["name"])
        out.append({
            "name": r["name"],
            "description": eff["roleDescription"],
            "edited": eff["roleDescription"] != r["roleDescription"],
            "sketch": eff.get("sketch", ""),
            "model": st.get("models", {}).get(r["name"]) or "session",
            "started": role_started(st, r["name"]),
        })
    emit({"roles": out})


def cmd_role(args):
    """Review a character before its first turn: replace its description, add a sketch, or reset."""
    st = load_state(args.run_dir)
    template = template_of(st)
    if role_by_name(template, args.role) is None:
        fail("No role %r. Roles: %s" % (args.role, ", ".join(r["name"] for r in template["roles"])))
    if role_started(st, args.role):
        fail("%s has already been cast; characters can only change before their first turn." % args.role)
    ov = st.setdefault("role_overrides", {}).setdefault(args.role, {})
    if args.reset:
        st["role_overrides"].pop(args.role, None)
    else:
        if args.description_file:
            args.description = read_text(args.description_file).strip()
        if args.sketch_file:
            args.sketch = read_text(args.sketch_file).strip()
        if args.description is not None:
            ov["description"] = args.description.strip()
        if args.sketch is not None:
            ov["sketch"] = args.sketch.strip()
    save_state(args.run_dir, st)
    eff = effective_role(st, template, args.role)
    emit({"name": args.role, "description": eff["roleDescription"], "sketch": eff.get("sketch", "")})


def cmd_meta(args):
    st = load_state(args.run_dir)
    if st["turns"] or st["pending"]:
        fail("Title, author, and user input are fixed once the first step is issued.")
    if args.title is not None:
        st["title"] = args.title.strip()
    if args.author is not None:
        st["author"] = args.author.strip() or "Unknown Author"
    if args.user_input is not None:
        st["user_input"] = args.user_input
    if args.model is not None or args.models is not None:
        st["models"] = parse_models(args.model, args.models, template_of(st))
    if args.form is not None:
        st["form"] = args.form
    save_state(args.run_dir, st)
    emit({"title": st["title"], "author": st["author"], "userInput": st["user_input"], "form": st.get("form"),
          "models": {r: (m or "session") for r, m in st.get("models", {}).items()}})


def build_prompt(st, template, step, role, variables):
    """The exact text the app sends for one step (LLMCallers.generateTextWithCaching)."""
    base = static_content(st, template, variables)
    history = transcript_block(template, st["history"])
    new_prompt = replace_placeholders(role["roleDescription"] + "\n\n" + step["promptTemplate"], variables) + language_prompt(st)
    return base + "\n" + history + "\n" + new_prompt, new_prompt


def step_prompt_for_known_context(st, template, step, role_name, variables, writer_knows_all=False):
    """The step prompt for a writer that already holds the earlier context.

    The app re-sends everything on every call. A persistent speaker (or the single
    writer) already has the document, the setting, and the notes it wrote, so those
    placeholders become short references instead of being pasted in again.
    """
    static_keys = set(template["initialContext"]) | {"transcript"}

    def sub(m):
        key = m.group(1)
        if key not in variables:
            return m.group(0)
        if key in st["variables"]:
            if writer_knows_all or st.get("var_author", {}).get(key) == role_name:
                return "[your %s, which you wrote earlier in this conversation]" % key
            return variables[key]
        if key in static_keys:
            return "[the %s from your first turn sheet]" % ("source document" if key == "transcript" else key)
        return variables[key]

    text = PLACEHOLDER_RE.sub(sub, step["promptTemplate"])
    return replace_placeholders(text, variables) + language_prompt(st)


def delta_sheet(st, template, step, role_name, variables, n, visible):
    """What a persistent speaker needs for its next turn: what it missed, then its instruction."""
    missed = st["history"][st.get("seen", {}).get(role_name, 0):]
    if missed:
        said = "Since your last turn, this was said:\n\n" + "\n\n".join(
            "[%s]: %s" % (h["role"], h["text"]) for h in missed)
    else:
        said = "Nothing has been said since your last turn."
    return (
        "Turn %d. You are still %s. %s\n\n%s\n\nYour instruction for this turn:\n\n%s\n\n%s%s\n"
    ) % (
        n, role_name,
        "Your words go into the spoken transcript." if visible
        else "This is a private preparation step: nobody hears it, but your notes are saved for later steps.",
        wrap_long_lines(said),
        wrap_long_lines(step_prompt_for_known_context(st, template, step, role_name, variables)),
        notes_block(director_notes(st, template, step, role_name)),
        END_MARKER,
    )


def issue(st, template, index, speaker=None):
    step = template["steps"][index]
    role_name = speaker or pick_role(st, step)
    role = effective_role(st, template, role_name)
    if role is None:
        fail("Step %d names role %r, which the template does not define." % (index, role_name))
    variables = variables_of(st, template)
    full_prompt, role_prompt = build_prompt(st, template, step, role, variables)
    mode = st.get("mode", "cast")
    use_delta = mode == "cast" and bool(st.get("cast", {}).get(role_name))
    # Notes for full sheets and the single writer are computed here; delta sheets compute their own.
    notes = [] if use_delta else director_notes(st, template, step, role_name)
    st["turn_counter"] += 1
    n = st["turn_counter"]
    base = os.path.join(st["run_dir"], "turns", "%03d-%s" % (n, slug(role_name)))
    prompt_file = base + ".prompt.md"
    output_file = base + ".txt"
    visible = is_visible(step)
    sketch = ("\nCHARACTER SKETCH (how you sound and who you are; stay consistent with it):\n%s\n"
              % role["sketch"]) if role.get("sketch") else ""
    sketch += "\n" + performance_block(st, template, role)
    originals = "" if use_delta else originals_block(st)
    sheet = (
        "ROLE CARD: you are %s.\n%s\n%s\n"
        "Turn %d. %s\n\n"
        "=== PROMPT (as the enTalkenator app would send it) ===\n\n%s\n\n%s%s%s\n"
    ) % (
        role_name,
        role["roleDescription"],
        sketch,
        n,
        "Your words go into the spoken transcript." if visible
        else "This is a private preparation step: nobody hears it, but your notes are saved for later steps.",
        wrap_long_lines(full_prompt),
        "" if use_delta else notes_block(notes),
        originals,
        END_MARKER,
    )
    write_text(prompt_file, sheet)
    if os.path.exists(output_file):
        os.remove(output_file)
    pend = {
        "turn": n,
        "index": index,
        "role": role_name,
        "visible": visible,
        "outputKey": step.get("outputKey"),
        "prompt_file": prompt_file,
        "output_file": output_file,
        "issued_at": now(),
    }
    if use_delta:
        # This role already has a persistent speaker: send only what it missed.
        delta_file = base + ".delta.md"
        write_text(delta_file, delta_sheet(st, template, step, role_name, variables, n, visible))
        pend["delta_file"] = delta_file
        pend["agent"] = st["cast"][role_name]
    elif mode == "single":
        first = not any(t["role"] == role_name for t in st["turns"]) and not any(
            p["role"] == role_name for p in st["pending"])
        everyone_first = not st["turns"] and not st["pending"]
        pend["instruction"] = (role["roleDescription"]
                               + ("\n\nCharacter sketch: " + role["sketch"] if first and role.get("sketch") else "")
                               + ("\n\n" + performance_block(st, template, role).strip() if everyone_first else "")
                               + "\n\n" + step_prompt_for_known_context(
                                   st, template, step, role_name, variables, writer_knows_all=True)
                               + ("\n\n" + notes_block(notes).strip() if notes else ""))
    return pend, role_prompt


CHUNK_RE = re.compile(r"^\d+-p(\d+)-(\d+)\.pdf$")


def describe_original(path):
    """An original document for the speakers' sheet: its name and, for a PDF that sources.py
    prepared, the small parts it was cut into (the Read tool reads a PDF of up to 10 pages whole)."""
    o = {"path": path, "name": os.path.basename(path)}
    ddir = os.path.dirname(path)
    try:  # sources.py keeps each document as sources/<doc>/original.pdf; its manifest has the real name.
        with open(os.path.join(os.path.dirname(ddir), "manifest.json"), encoding="utf-8") as f:
            doc = next(d for d in json.load(f)["docs"] if os.path.abspath(d.get("pdf") or "") == path)
        o["name"] = doc["name"]
        o["pages"] = doc.get("pages")
    except (OSError, ValueError, KeyError, StopIteration):
        pass
    cdir = os.path.join(ddir, "chunks")
    if path.lower().endswith(".pdf") and os.path.isdir(cdir):
        parts = []
        for fn in os.listdir(cdir):
            m = CHUNK_RE.match(fn)
            if m:
                parts.append({"first": int(m.group(1)), "last": int(m.group(2)), "path": os.path.join(cdir, fn)})
        if parts:
            o["parts"] = sorted(parts, key=lambda c: c["first"])
    return o


def originals_block(st):
    """Where the speaker may check the source documents themselves."""
    if not st.get("originals"):
        return ""
    lines = ["ORIGINAL DOCUMENTS: the document text above comes from these files. You may open one with the "
             "Read tool, but only to check a figure, a table, or exact wording. Where the text is marked "
             "[PDF page N], open the part that holds that page:"]
    for o in st["originals"]:
        if o.get("parts"):
            lines.append("- %s%s, in parts:" % (o["name"], " (%d pages)" % o["pages"] if o.get("pages") else ""))
            lines += ["  - pages %d-%d: %s" % (c["first"], c["last"], c["path"]) for c in o["parts"]]
        else:
            lines.append("- %s: %s" % (o["name"], o["path"]))
    if any(not o.get("parts") and o["path"].lower().endswith(".pdf") for o in st["originals"]):
        lines.append("(A PDF of more than 10 pages needs a page range, which may fail; then rely on the text above.)")
    return "\n".join(lines) + "\n\n"


def depends_on_batch(step, batch_keys):
    used = set(PLACEHOLDER_RE.findall(step["promptTemplate"]))
    return bool(used & batch_keys)


def describe(st, pend, mode="cast"):
    d = {
        "turn": pend["turn"],
        "step": pend["index"],
        "role": pend["role"],
        "visible": pend["visible"],
        "outputFile": pend["output_file"],
    }
    if mode == "single":
        # The single writer already holds the document and earlier turns in context.
        d["action"] = "write"
        d["instruction"] = pend.get("instruction")
        d["promptFile"] = pend["prompt_file"]
    elif mode == "cast" and pend.get("delta_file"):
        d["action"] = "message"
        d["agent"] = pend["agent"]
        d["sheet"] = pend["delta_file"]
    else:
        # First turn for this role (cast) or every turn (fresh): a new speaker
        # gets the full sheet.
        d["action"] = "spawn"
        d["sheet"] = pend["prompt_file"]
        d.update(speaker_for(st.get("models", {}).get(pend["role"], "")))
    return d


MODEL_ALIASES = ("fable", "opus", "sonnet", "haiku")
SESSION_MODEL = ("", "session", "inherit", "default")


def pinned_agent_name(model_id):
    return "entalkenator-speaker-" + re.sub(r"[^a-z0-9]+", "-", model_id.lower()).strip("-")


def speaker_for(model):
    """How the showrunner spawns a speaker on this model.

    The Agent tool's per-call `model` takes only the four aliases (a continued
    agent keeps it). Older versions need a full model ID in an agent definition's
    frontmatter, so they use a pinned project agent written by `pin-agent`.
    """
    model = (model or "").strip()
    if model.lower() in SESSION_MODEL:
        return {"agentType": SPEAKER_AGENT}
    if model.lower() in MODEL_ALIASES:
        return {"agentType": SPEAKER_AGENT, "model": model.lower()}
    return {"agentType": pinned_agent_name(model), "pinnedModel": model}


def parse_models(single, per_role, template):
    """--model applies to every role; --models "Role=model;*=model" overrides per role."""
    names = [r["name"] for r in template["roles"]]
    models = {n: (single or "").strip() for n in names}
    if per_role:
        for part in re.split(r"[;\n]", per_role):
            if "=" not in part:
                continue
            role, model = [x.strip() for x in part.split("=", 1)]
            if role == "*":
                for n in names:
                    models[n] = model
            elif role in models:
                models[role] = model
            else:
                fail("--models names role %r; the template's roles are: %s" % (role, ", ".join(names)))
    for n, m in models.items():
        if m and m.lower() not in MODEL_ALIASES + SESSION_MODEL and not m.startswith("claude-"):
            fail("Model %r for %s isn't fable, opus, sonnet, haiku, session, or a full model ID like "
                 "claude-opus-4-8." % (m, n))
    return models


def progress(st, template):
    p = {
        "turnsWritten": len(st["turns"]),
        "spokenWords": st["total_words"],
        "spokenMinutes": round(st["total_words"] / WORDS_PER_MINUTE, 1),
        "stepIndex": st["current_index"],
        "stepCount": len(template["steps"]),
    }
    if st.get("directed"):
        p["program"] = program_progress(st)
    if st["loop_start_index"] is not None and st["loop_duration"]:
        p["loop"] = {
            "minutesSoFar": round(st["accumulated_words"] / WORDS_PER_MINUTE, 1),
            "targetMinutes": st["loop_duration"],
        }
    return p


def cmd_next(args, st=None):
    run = args.run_dir
    st = st or load_state(run)
    template = template_of(st)
    mode = st.get("mode", "cast")
    if st["done"]:
        emit({"done": True, "progress": progress(st, template),
              "next": "Run `engine.py finalize %s`." % run})
        return
    if st["pending"]:
        # Re-issue what is still outstanding (idempotent).
        emit({"done": False, "mode": mode, "issue": [describe(st, p, mode) for p in st["pending"]],
              "note": "These steps were already issued and their output files are still missing."})
        return
    if st["turn_counter"] >= st["max_turns"]:
        fail("Reached --max-turns (%d) without finishing the template. A loop may never reach its target length."
             % st["max_turns"])

    if st["first_issue_at"] is None:
        st["first_issue_at"] = now()
    if st.get("directed"):
        return directed_next(args, st, template, run, mode)
    steps = template["steps"]
    idx = st["current_index"]
    choice = floor_choice(st, template, idx)
    wanted = (getattr(args, "speaker", None) or [None])
    if len(wanted) > 1:
        fail("One --speaker per turn.")
    wanted = wanted[0]
    if choice and not wanted:
        save_state(run, st)
        emit({"done": False, "mode": mode, "choose": choice, "progress": progress(st, template),
              "next": "Give the floor to whoever would most naturally speak next, then run "
                      "`engine.py advance %s --speaker NAME`." % run})
        return
    speaker = resolve_speaker(choice, steps[idx], wanted) if wanted else None
    pend, _ = issue(st, template, idx, speaker)
    batch = [pend]
    if getattr(args, "batch", False) and mode != "single" and not pend["visible"] and loop_value(steps[idx]) is None:
        # Consecutive hidden steps that do not read each other's outputKeys can be
        # written in parallel: none of them changes the spoken transcript. A role
        # appears at most once per batch so it keeps a single speaker.
        keys = {steps[idx].get("outputKey")} - {None}
        roles = {pend["role"]}
        j = idx + 1
        while j < len(steps):
            s = steps[j]
            if is_visible(s) or loop_value(s) is not None or depends_on_batch(s, keys):
                break
            if any(r.strip() in roles for r in s["roleName"].split(",")):
                break
            p, _ = issue(st, template, j)
            batch.append(p)
            roles.add(p["role"])
            if s.get("outputKey"):
                keys.add(s["outputKey"])
            j += 1
    st["pending"] = batch
    save_state(run, st)
    emit({
        "done": False,
        "mode": mode,
        "issue": [describe(st, p, mode) for p in batch],
        "progress": progress(st, template),
    })


SPEAKER_LABEL_RE = re.compile(r"^\s*(\*\*)?\[?([^\]\n:]{1,60})\]?(\*\*)?\s*:\s*")


def clean_output(text, role_name):
    t = text.replace("\r\n", "\n").strip()
    # Strip a leading "[Name]:" or "Name:" the writer added despite the instructions.
    m = SPEAKER_LABEL_RE.match(t)
    if m and m.group(2).strip().lower() in (role_name.lower(), "speaker", role_name.split()[-1].lower()):
        t = t[m.end():].lstrip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"“" and t.count(t[0]) == 2:
        t = t[1:-1].strip()
    t = t.replace(END_MARKER, "").strip()
    return t


def commit_one(st, template, pend):
    out_file = pend["output_file"]
    if not os.path.isfile(out_file):
        return "missing"
    raw = read_text(out_file)
    text = clean_output(raw, pend["role"])
    if not text:
        return "empty"
    if text != raw:
        write_text(out_file, text)
    step = template["steps"][pend["index"]]
    idx = pend["index"]

    if step.get("outputKey"):
        st["variables"][step["outputKey"]] = text
        st.setdefault("var_author", {})[step["outputKey"]] = pend["role"]
    if pend["visible"]:
        st["history"].append({"role": pend["role"], "text": text})
    # The speaker that wrote this turn has now seen everything said so far.
    st.setdefault("seen", {})[pend["role"]] = len(st["history"])

    # generateTranscript: loop bookkeeping happens before the step's words count.
    lv = loop_value(step)
    if lv and lv[0] == "start":
        st["loop_duration"] = round(lv[1] * st["loop_scale"], 3)
        if st["loop_start_index"] is None:
            st["accumulated_words"] = 0
    wc = word_count(text)
    st["accumulated_words"] += wc
    if pend["visible"]:
        st["total_words"] += wc

    # handleLoopForDuration
    next_index = idx + 1
    if lv and lv[0] == "start":
        st["loop_start_index"] = idx
    elif lv and lv[0] == "end":
        if st["loop_start_index"] is not None and st["loop_duration"] is not None:
            minutes = st["accumulated_words"] / WORDS_PER_MINUTE
            if minutes < st["loop_duration"]:
                next_index = st["loop_start_index"]
            else:
                st["accumulated_words"] = 0
                st["loop_start_index"] = None
    st["current_index"] = next_index

    st["turns"].append({
        "turn": pend["turn"],
        "index": idx,
        "role": pend["role"],
        "visible": pend["visible"],
        "outputKey": step.get("outputKey"),
        "words": wc,
        "file": out_file,
        "sha1": hashlib.sha1(text.encode("utf-8")).hexdigest(),
        "issued_at": pend["issued_at"],
        "committed_at": now(),
        "write_seconds": round(os.path.getmtime(out_file) - pend["issued_at"], 1),
    })
    if (pend.get("last") if st.get("directed") else st["current_index"] >= len(template["steps"])):
        st["done"] = True
        st["finished_at"] = now()
    return "ok"


def cmd_commit(args, quiet=False):
    run = args.run_dir
    st = load_state(run)
    template = template_of(st)
    if not st["pending"]:
        if quiet:
            return st
        emit({"committed": [], "note": "Nothing pending.", "done": st["done"], "progress": progress(st, template)})
        return st
    committed, problems = [], []
    while st["pending"]:
        pend = st["pending"][0]
        res = commit_one(st, template, pend)
        if res != "ok":
            problems.append({"turn": pend["turn"], "role": pend["role"], "outputFile": pend["output_file"], "problem": res})
            break
        st["pending"].pop(0)
        text = st["variables"].get(pend["outputKey"]) if pend.get("outputKey") else read_text(pend["output_file"])
        text = text or read_text(pend["output_file"])
        item = {"turn": pend["turn"], "role": pend["role"], "visible": pend["visible"], "words": word_count(text)}
        if st.get("directed") and pend["visible"]:
            item["text"] = text  # the showrunner directs from what was actually said
        else:
            item["preview"] = text[:160].replace("\n", " ")
        committed.append(item)
    save_state(run, st)
    if problems:
        emit({"committed": committed, "problems": problems,
              "pending": [describe(st, p, st.get("mode", "cast")) for p in st["pending"]],
              "note": "These turns are still open. Wait for their speakers (the watchdog flags stalls), "
                      "then run advance again."})
        sys.exit(2)
    if not quiet:
        emit({"committed": committed, "done": st["done"], "progress": progress(st, template)})
    return st, committed


def register_cast(run, specs):
    """--cast "Role=agentId": remember the persistent speaker for a role."""
    if not specs:
        return
    st = load_state(run)
    template = template_of(st)
    names = {r["name"] for r in template["roles"]}
    for spec in specs:
        if "=" not in spec:
            fail("--cast expects \"Role=agentId\", got %r" % spec)
        role, agent = [x.strip() for x in spec.rsplit("=", 1)]
        if role not in names:
            fail("Unknown role %r. Roles: %s" % (role, ", ".join(sorted(names))))
        if st.setdefault("cast", {}).get(role) != agent:
            st["cast"][role] = agent
            # A new agent has seen only what its own full sheet showed it, and the
            # notes it wrote itself. Its first commit sets "seen" again.
            st.setdefault("var_author", {})
            for key, who in list(st["var_author"].items()):
                if who == role and not any(p["role"] == role and p.get("outputKey") == key for p in st["pending"]):
                    st["var_author"][key] = role + " (earlier speaker)"
    save_state(run, st)


def cmd_advance(args):
    run = args.run_dir
    register_cast(run, getattr(args, "cast", None))
    st = load_state(run)
    committed = []
    if st["pending"]:
        res = cmd_commit(args, quiet=True)
        st, committed = res if isinstance(res, tuple) else (res, [])
    if st["done"]:
        template = template_of(st)
        emit({"committed": committed, "done": True, "progress": progress(st, template),
              "next": "Run finalize."})
        return
    # Reuse next's output but include what was just committed.
    import io
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        cmd_next(args, st)
    except SystemExit:
        sys.stdout = old
        sys.stdout.write(buf.getvalue())  # next's error, which would otherwise be lost
        raise
    finally:
        sys.stdout = old
    out = json.loads(buf.getvalue())
    out["committed"] = committed
    emit(out)


def cmd_status(args):
    st = load_state(args.run_dir)
    template = template_of(st)
    emit({
        "template": st["template_name"],
        "title": st["title"],
        "author": st["author"],
        "done": st["done"],
        "models": {r: (m or "session") for r, m in st.get("models", {}).items()},
        "pending": [describe(st, p, st.get("mode", "cast")) for p in st["pending"]],
        "progress": progress(st, template),
        "elapsedSeconds": round((st["finished_at"] or now()) - (st["first_issue_at"] or now())),
    })


def transcript_text(st):
    # Template.processStep: "[Role]: output\n\n" for every visible step.
    return "".join("[%s]: %s\n\n" % (h["role"], h["text"]) for h in st["history"])


def _norm(text):
    return " ".join(text.split())


def _entries(block, roles):
    """Split a transcript block into normalized (role, text) entries."""
    if not block.strip():
        return []
    pat = re.compile(r"^\[(%s)\]: " % "|".join(re.escape(r) for r in roles), re.M)
    ms = list(pat.finditer(block))
    out = []
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(block)
        out.append((m.group(1), _norm(block[m.end():end])))
    return out


def audit_views(st, template):
    """Check, from the sheet files themselves, that every speaker saw the whole transcript.

    For each committed turn, rebuild what that role's speaker had been shown: the
    transcript in its full sheet when it was cast (or re-cast), plus every delta sheet
    since, plus its own lines. That must equal the true transcript up to the turn,
    entry for entry and in order. A dropped, duplicated, or reordered line fails.
    """
    if st.get("mode") == "single":
        return {"ok": True, "skipped": "single writer sees everything"}
    roles = [r["name"] for r in template["roles"]]
    otp = template["outputTranscriptPrompt"].split("{{output}}")
    suffix = _norm(otp[1])[:40] if len(otp) == 2 else ""
    truth, hi = {}, 0
    for t in st["turns"]:
        if t["visible"]:
            h = st["history"][hi]
            truth[t["turn"]] = (h["role"], _norm(h["text"]))
            hi += 1
    views, problems, checked = {}, [], 0
    for t in st["turns"]:
        role = t["role"]
        stem = re.sub(r"(\.recast\d+)?\.txt$", "", t["file"])
        delta = stem + ".delta.md"
        used_delta = (st.get("mode", "cast") == "cast" and os.path.exists(delta)
                      and ".recast" not in os.path.basename(t["file"]))
        if used_delta:
            text = read_text(delta)
            if "Nothing has been said since your last turn." in text:
                got = []
            else:
                a = text.index("Since your last turn, this was said:") + len("Since your last turn, this was said:")
                b = text.index("Your instruction for this turn:")
                got = _entries(text[a:b], roles)
            views[role] = views.get(role, []) + got
        else:
            text = read_text(stem + ".prompt.md")
            if NO_CONVERSATION_YET in text.split("=== PROMPT", 1)[-1]:
                views[role] = []
            else:
                body = text.split("=== PROMPT", 1)[-1]
                m = re.search(r"^\[(%s)\]: " % "|".join(re.escape(r) for r in roles), body, re.M)
                block = body[m.start():] if m else ""
                if suffix and block:
                    # Cut at the template's closing words for the history block.
                    flat_idx = None
                    for cut in re.finditer(re.escape(suffix.split()[0]), block):
                        if _norm(block[cut.start():]).startswith(suffix):
                            flat_idx = cut.start()
                            break
                    if flat_idx is not None:
                        block = block[:flat_idx]
                views[role] = _entries(block, roles)
        expected = [truth[n] for n in sorted(truth) if n < t["turn"]]
        checked += 1
        if views[role] != expected:
            first = next((i for i, (x, y) in enumerate(zip(views[role], expected)) if x != y),
                         min(len(views[role]), len(expected)))
            problems.append({"turn": t["turn"], "role": role, "sheet": "delta" if used_delta else "full",
                             "saw": len(views[role]), "expected": len(expected), "firstDifferenceAt": first})
            views[role] = list(expected)   # keep checking later turns against the truth
        if t["visible"]:
            views[role].append(truth[t["turn"]])
    return {"ok": not problems, "turnsChecked": checked, "problems": problems}


def cmd_audit(args):
    st = load_state(args.run_dir)
    emit(audit_views(st, template_of(st)))


def cmd_finalize(args):
    st = load_state(args.run_dir)
    if not st["done"]:
        fail("The template has not finished yet. Keep running advance.")
    path = os.path.join(st["run_dir"], "transcript.txt")
    write_text(path, transcript_text(st))
    # Delivery notes the showrunner gave for spoken turns, numbered as in transcript.txt, for render_audio.py.
    steps = template_of(st)["steps"]
    spoken = [t for t in st["turns"] if t["visible"]]
    deliveries = [dict(turn=i, role=t["role"], **steps[t["index"]]["delivery"])
                  for i, t in enumerate(spoken, 1) if steps[t["index"]].get("delivery")]
    dpath = os.path.join(st["run_dir"], "delivery.json")
    if deliveries:
        write_text(dpath, json.dumps({"turns": deliveries}, indent=1, ensure_ascii=False) + "\n")
    elif os.path.exists(dpath):
        os.remove(dpath)
    # A speaker that rewrote its file after the showrunner committed it would leave
    # the transcript with a stale draft. Report it rather than guess.
    changed = []
    for t in st["turns"]:
        if t.get("sha1") and os.path.isfile(t["file"]):
            disk = clean_output(read_text(t["file"]), t["role"])
            if hashlib.sha1(disk.encode("utf-8")).hexdigest() != t["sha1"]:
                changed.append({"turn": t["turn"], "role": t["role"], "file": t["file"]})
    template = template_of(st)
    elapsed = (st["finished_at"] or now()) - (st["first_issue_at"] or st["created_at"])
    emit({
        "transcript": path,
        "spokenTurns": len(st["history"]),
        "hiddenTurns": len([t for t in st["turns"] if not t["visible"]]),
        "spokenWords": st["total_words"],
        "estimatedMinutes": round(st["total_words"] / WORDS_PER_MINUTE, 1),
        "generationSeconds": round(elapsed),
        "roles": [r["name"] for r in template["roles"]],
        "deliveryNotes": os.path.join(st["run_dir"], "delivery.json") if deliveries else None,
        "segments": [{"segment": x["segment"], "title": st["program"]["segments"][x["segment"] - 1]["title"]
                      if x["segment"] <= len(st["program"]["segments"]) else None, "startTurn": x["turn"]}
                     for x in st.get("segment_starts", [])] if st.get("directed") else None,
        "changedAfterCommit": changed,
        "viewAudit": audit_views(st, template),
    })


def cmd_chapters(args):
    """Save the showrunner's chapter points for the finished transcript: [(turn, title)]."""
    st = load_state(args.run_dir)
    if not st["done"]:
        fail("Run finalize first.")
    turns = st["history"]
    plan = []
    for part in re.split(r"[;\n]", args.plan or ""):
        if not part.strip():
            continue
        if "=" not in part:
            fail("Write each chapter as TURN=Title, separated by semicolons: %r" % part.strip())
        n, title = part.split("=", 1)
        try:
            n = int(n.strip())
        except ValueError:
            fail("%r isn't a turn number" % n.strip())
        title = " ".join(title.split())
        if not 1 <= n <= len(turns):
            fail("Turn %d doesn't exist; the transcript has %d spoken turns." % (n, len(turns)))
        if not title or len(title) > 80:
            fail("Chapter titles should be 1 to 80 characters: %r" % title)
        plan.append({"turn": n, "title": title})
    plan.sort(key=lambda c: c["turn"])
    if len({c["turn"] for c in plan}) != len(plan):
        fail("Two chapters start at the same turn.")
    if not plan:
        fail("Give at least one chapter, e.g. --plan \"1=Opening statements;6=The jurisdiction question\"")
    if plan[0]["turn"] != 1:
        plan.insert(0, {"turn": 1, "title": "Opening"})
    path = os.path.join(st["run_dir"], "chapters-plan.json")
    write_text(path, json.dumps({"chapters": plan}, ensure_ascii=False, indent=1) + "\n")
    emit({"plan": path, "chapters": [dict(c, speaker=turns[c["turn"] - 1]["role"],
                                         opens=" ".join(turns[c["turn"] - 1]["text"].split()[:12])) for c in plan],
          "spokenTurns": len(turns),
          "note": "Pass --chapter-plan with this file to render_audio.py."})


def safe_component(name, fallback="Untitled"):
    s = re.sub(r'[/\\:*?"<>|\x00-\x1f]', " ", name or "")
    s = re.sub(r"\s+", " ", s).strip().strip(".")
    return s[:120] or fallback


def cmd_package(args):
    """Build an .enTalkenator bundle laid out like enTalkenator/Default Items/default.enTalkenator:

        <Display Name>/info.txt                       {"displayName","author","baseFileName","sourceFileType"}
        <Display Name>/<base>.<txt|pdf>               the source document
        <Display Name>/<base>-transcript.txt          the source text (the app's "transcript" content type)
        <Display Name>/<base>-<Template>.txt          generated transcript, "[Name]: text" turns
        <Display Name>/<base>-<Template>.m4a          rendered audio
    where <base> is "<UUID>-original", as the app names imported files.
    """
    st = load_state(args.run_dir)
    run = st["run_dir"]
    transcript = os.path.join(run, "transcript.txt")
    if not os.path.isfile(transcript):
        fail("transcript.txt is missing. Run finalize first.")
    if args.audio and not os.path.isfile(args.audio):
        fail("Audio file not found: %s" % args.audio)
    display = safe_component(args.display_name or st["title"])
    if not args.keep_hyphens:
        # On import the display name becomes the item's file prefix, and
        # scanGeneratedContent names content after the last ASCII "-" in a file
        # name, so "Plain-Language" would add a bogus "Language Summary" entry.
        # U+2011 (non-breaking hyphen) looks the same and doesn't split.
        display = display.replace("-", "‑")
    # LibraryItem.scanGeneratedContent names content after the last "-" in the file name.
    content_type = safe_component(args.content_type or st["template_name"]).replace("-", " ")
    base = "%s-original" % str(uuid.uuid4()).upper()
    info = {
        "displayName": display,
        "author": st.get("author") or "Unknown Author",
        "baseFileName": base,
        "sourceFileType": st["source_type"],
    }
    out_dir = os.path.abspath(args.out or run)
    os.makedirs(out_dir, exist_ok=True)
    bundle = os.path.join(out_dir, "%s.enTalkenator" % display)
    files = [
        ("%s.%s" % (base, st["source_type"]), os.path.join(run, "source", "original." + st["source_type"])),
        ("%s-transcript.txt" % base, os.path.join(run, "document.txt")),
        ("%s-%s.txt" % (base, content_type), transcript),
    ]
    if args.audio:
        files.append(("%s-%s.m4a" % (base, content_type), os.path.abspath(args.audio)))
    tmp = bundle + ".tmp"
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for arcname, src in files:
            z.write(src, "%s/%s" % (display, arcname))
        zi = zipfile.ZipInfo("%s/info.txt" % display, date_time=_dt.datetime.now().timetuple()[:6])
        zi.compress_type = zipfile.ZIP_DEFLATED
        zi.external_attr = 0o644 << 16
        z.writestr(zi, json.dumps(info, ensure_ascii=False, separators=(",", ":")))
    os.replace(tmp, bundle)
    with zipfile.ZipFile(bundle) as z:
        names = z.namelist()
    try:
        check = simulate_import(bundle)
    except (ValueError, KeyError) as e:
        check = {"error": str(e)}
    emit({"bundle": bundle, "contents": names, "info": info, "appImport": check})


def simulate_import(bundle):
    """Replay LibraryItem.importFromEnTalkenator and scanGeneratedContent on a bundle (current app logic).

    Returns the library entries the app would show, or raises on what the app
    would reject (it throws LibraryError.sourceFileMissing for all of these).
    """
    with zipfile.ZipFile(bundle) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        tops = sorted({n.split("/", 1)[0] for n in z.namelist()})
        main = [t for t in tops if not t.startswith("__")]
        if not main:
            raise ValueError("no top-level folder (only __* entries)")
        if len(main) > 1:
            raise ValueError("more than one top-level entry %r; the app picks one arbitrarily" % main)
        folder = main[0]
        files = [n.split("/", 1)[1] for n in names if n.startswith(folder + "/") and "/" not in n.split("/", 1)[1]]
        if "info.txt" not in files:
            raise ValueError("%s/info.txt missing" % folder)
        info = json.loads(z.read(folder + "/info.txt").decode("utf-8"))
    if not isinstance(info, dict) or not all(isinstance(v, str) for v in info.values()):
        raise ValueError("info.txt must be a JSON object of strings")
    base = info.get("baseFileName")
    if not base:
        raise ValueError("info.txt has no baseFileName")
    # Mirrors LibraryItem as of the period/hyphen fix (main 9ccccaa): the source is the file
    # whose name minus its extension is the base name, and the base can contain dots.
    source = [f for f in files if os.path.splitext(f)[0] == base]
    transcript = [f for f in files if f.endswith("-transcript.txt")]
    if not source or not transcript:
        raise ValueError("needs <baseFileName>.<ext> and a *-transcript.txt file")
    # LibraryItem.init: new base = display name with "/" -> "-"; other files keep their suffix,
    # with only the leading "<old base>-" replaced.
    new_base = (info.get("displayName") or "Imported Item").replace("/", "-")
    old_prefix = os.path.splitext(source[0])[0]
    src_ext = os.path.splitext(source[0])[1].lstrip(".")
    imported = ["%s.%s" % (new_base, src_ext), "%s-transcript.txt" % new_base]
    if any(f.endswith("-transcript.m4a") for f in files):
        imported.append("%s-transcript.m4a" % new_base)
    for f in files:
        if os.path.splitext(f)[0] == base or f.endswith("-transcript.txt") or f.endswith("-transcript.m4a") \
                or f.endswith("info.txt"):
            continue
        imported.append(new_base + f[len(old_prefix):] if f.startswith(old_prefix + "-") else f)
    # scanGeneratedContent: the original is "<base>.<ext>"; content is "<base>-<type>.<ext>".
    content = {}
    if any(os.path.splitext(f)[0] == new_base for f in imported):
        content["original"] = {"txt": True}
    prefix = new_base + "-"
    for f in imported:
        stem, ext = os.path.splitext(f)
        if not stem.startswith(prefix) or ext.lower() not in (".txt", ".m4a"):
            continue
        name = stem[len(prefix):]
        if name:
            content.setdefault(name, {})[ext.lower().lstrip(".")] = True
    return {"folder": folder, "itemFiles": imported,
            "libraryEntries": {k: sorted(v) for k, v in sorted(content.items())}}


def cmd_pin_agent(args):
    """Write a project agent that runs the speaker on a full (older) model ID."""
    model = args.model.strip()
    if not model.startswith("claude-"):
        fail("pin-agent takes a full model ID such as claude-opus-4-8. Aliases (fable, opus, sonnet, "
             "haiku) need no pinned agent.")
    src = os.path.join(ROOT, ".claude", "agents", SPEAKER_AGENT + ".md")
    text = read_text(src)
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        fail("Couldn't read the frontmatter of %s" % src)
    body = text[m.end():]
    name = pinned_agent_name(model)
    front = "\n".join([
        "---",
        "name: %s" % name,
        "description: enTalkenator speaker pinned to %s. Written by the entalkenate skill; "
        "safe to delete." % model,
        "tools: Read, Write",
        "model: %s" % model,
        "maxTurns: 8",
        "---",
        "",
    ])
    agents_dir = os.path.join(os.path.abspath(args.project_dir), ".claude", "agents")
    existed = os.path.isdir(agents_dir)
    os.makedirs(agents_dir, exist_ok=True)
    path = os.path.join(agents_dir, name + ".md")
    write_text(path, front + body)
    emit({
        "agentType": name,
        "file": path,
        "model": model,
        "restartNeeded": not existed,
        "note": ("Claude Code only watches .claude/agents/ if it existed when the session started, so "
                 "this agent loads after a restart." if not existed else
                 "Claude Code picks up new agent files within a few seconds."),
    })


def find_pending(st, turn):
    for pend in st["pending"]:
        if pend["turn"] == turn:
            return pend
    fail("Turn %d isn't pending. Pending turns: %s" % (turn, [p["turn"] for p in st["pending"]] or "none"))


def stall_limit(st, pend, min_seconds, factor):
    """How long a speaker may take before its turn counts as stalled.

    Scaled from this run's own history (median time to write the file) so a slow
    model or a long prep step isn't flagged just for being itself. Private prep
    steps write much more and get a longer floor.
    """
    same = [t["write_seconds"] for t in st["turns"]
            if t.get("write_seconds") is not None and t["visible"] == pend["visible"]]
    # Prep notes run to ~1,000 words, several times a spoken turn. A role's first
    # turn is a cold start: a new agent reading the full sheet, document and all.
    floor = min_seconds if pend["visible"] else min_seconds * 2
    if not st.get("cast", {}).get(pend["role"]) or not pend.get("delta_file"):
        floor *= 1.25
    if not same:
        return floor
    same.sort()
    median = same[len(same) // 2] if len(same) % 2 else (same[len(same) // 2 - 1] + same[len(same) // 2]) / 2.0
    return max(floor, factor * median)


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def cmd_watchdog(args):
    """Run in the background; exit (waking the showrunner) only when a turn stalls or the run ends.

    It reads state.json every few seconds and costs no model tokens. A turn counts
    as stalled when its output file hasn't appeared within the limit, measured from
    when it was issued or last nudged.
    """
    run = args.run_dir
    pidfile = os.path.join(run, "watchdog.pid")
    if os.path.exists(pidfile):
        try:
            other = int(read_text(pidfile).strip())
        except ValueError:
            other = 0
        if other and other != os.getpid() and pid_alive(other):
            emit({"event": "already-running", "pid": other})
            return
    write_text(pidfile, str(os.getpid()))
    started = now()
    try:
        while True:
            st = load_state(run)
            if st["done"]:
                emit({"event": "done", "progress": progress(st, template_of(st))})
                return
            if now() - started > args.max_minutes * 60:
                emit({"event": "expired", "note": "Watchdog ran for %d minutes; restart it if the run is still going."
                      % args.max_minutes})
                return
            for pend in st["pending"]:
                if os.path.exists(pend["output_file"]):
                    continue
                since = max(pend["issued_at"], pend.get("nudged_at") or 0)
                waited = now() - since
                limit = stall_limit(st, pend, args.min_seconds, args.factor)
                if waited > limit:
                    nudges = pend.get("nudges", 0)
                    emit({
                        "event": "stall",
                        "turn": pend["turn"],
                        "role": pend["role"],
                        "agent": st.get("cast", {}).get(pend["role"]),
                        "visible": pend["visible"],
                        "waitedSeconds": round(waited),
                        "limitSeconds": round(limit),
                        "nudges": nudges,
                        "recasts": pend.get("recasts", 0),
                        "outputFile": pend["output_file"],
                        "next": "nudge" if nudges == 0 else "recast",
                    })
                    return
            time.sleep(args.interval)
    finally:
        try:
            if read_text(pidfile).strip() == str(os.getpid()):
                os.remove(pidfile)
        except OSError:
            pass


def cmd_nudge(args):
    """Restart a stalled turn's clock and give the showrunner the reminder to send."""
    st = load_state(args.run_dir)
    pend = find_pending(st, args.turn)
    pend["nudged_at"] = now()
    pend["nudges"] = pend.get("nudges", 0) + 1
    save_state(args.run_dir, st)
    sheet = pend.get("delta_file") or pend["prompt_file"]
    emit({
        "turn": pend["turn"],
        "role": pend["role"],
        "agent": st.get("cast", {}).get(pend["role"]),
        "nudges": pend["nudges"],
        "message": ("Turn %d for %s is still open. If you're done, write your lines to the output file now "
                    "and reply with one line; if something is blocking you, say what.\nTurn sheet: %s\n"
                    "Output file: %s") % (pend["turn"], pend["role"], sheet, pend["output_file"]),
    })


def cmd_recast(args):
    """Replace a stalled speaker: same turn, full sheet, fresh output file, new agent."""
    st = load_state(args.run_dir)
    pend = find_pending(st, args.turn)
    role = pend["role"]
    old_agent = st.setdefault("cast", {}).pop(role, None)
    # The replacement didn't write this role's earlier notes, so later sheets must
    # spell them out instead of pointing back to them.
    for key, who in list(st.setdefault("var_author", {}).items()):
        if who == role:
            st["var_author"][key] = role + " (earlier speaker)"
    st.setdefault("seen", {}).pop(role, None)
    k = pend.get("recasts", 0) + 1
    stem = re.sub(r"\.recast\d+$", "", pend["output_file"][:-4])
    pend["output_file"] = "%s.recast%d.txt" % (stem, k)
    pend["recasts"] = k
    pend["issued_at"] = now()
    pend["nudges"] = 0
    for key in ("nudged_at", "delta_file", "agent"):
        pend.pop(key, None)
    save_state(args.run_dir, st)
    emit({
        "oldAgent": old_agent,
        "issue": [describe(st, pend, st.get("mode", "cast"))],
        "note": ("Stop the old agent if you can (TaskStop). If it writes late, it writes to the old file, "
                 "which is ignored. Register the new speaker with advance --cast as usual."),
    })


def cmd_verify(args):
    try:
        emit({"bundle": os.path.abspath(args.bundle), "ok": True, "appImport": simulate_import(args.bundle)})
    except (ValueError, KeyError, zipfile.BadZipFile) as e:
        emit({"bundle": os.path.abspath(args.bundle), "ok": False, "error": str(e)})
        sys.exit(1)


# ---------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("templates")
    p.add_argument("--templates-dir")
    p.set_defaults(func=cmd_templates)

    p = sub.add_parser("init")
    p.add_argument("--template", help="file stem (debate), id, displayName, or a .json path")
    p.add_argument("--program", help="a program you direct turn by turn (JSON: title, form, setting, lead, "
                                     "roles [{name, description}], segments [{title, minutes, plan}])")
    p.add_argument("--source", required=True, help="PDF, .txt, or .md file (a prepared reading from sources.py is a .md)")
    p.add_argument("--original", action="append", help="an original document the reading came from (repeatable); "
                                                       "speakers may open it to check a figure or exact wording")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--title")
    p.add_argument("--author")
    p.add_argument("--user-input", help="fills {{userInput}} (see the template's userInputConfig)")
    p.add_argument("--language", help="like the app's outputLanguage setting")
    p.add_argument("--loop-scale", type=float, default=1.0,
                   help="multiply every loop's target minutes (0.3 turns a 10-minute loop into 3)")
    p.add_argument("--max-turns", type=int, default=300)
    p.add_argument("--model", help="speaker model for every role: fable, opus, sonnet, haiku, session "
                                   "(default), or a full ID such as claude-opus-4-8")
    p.add_argument("--models", help='per-role overrides, e.g. "Hillford Johns=haiku;*=opus"')
    p.add_argument("--no-director", action="store_true", help="don't add director's notes to turn sheets")
    p.add_argument("--floor", choices=["template", "natural"], default="template",
                   help="natural: the showrunner picks who speaks where the template allows several roles")
    p.add_argument("--form", choices=("auto",) + FORMS, default="auto",
                   help="what kind of program this is; sets performance notes and director checks (default: guessed)")
    p.add_argument("--mode", choices=["cast", "fresh", "single"], default="cast",
                   help="cast: one persistent speaker agent per role (default); fresh: a new speaker "
                        "agent every turn; single: the showrunner writes every turn")
    p.add_argument("--templates-dir")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("roles", help="show the characters as they'll be played")
    p.add_argument("run_dir")
    p.set_defaults(func=cmd_roles)

    p = sub.add_parser("role", help="edit a character before its first turn")
    p.add_argument("run_dir")
    p.add_argument("--role", required=True)
    p.add_argument("--description", help="replace the template's role description")
    p.add_argument("--description-file")
    p.add_argument("--sketch", help="add a character sketch to the speaker's role card")
    p.add_argument("--sketch-file")
    p.add_argument("--reset", action="store_true")
    p.set_defaults(func=cmd_role)

    p = sub.add_parser("meta")
    p.add_argument("run_dir")
    p.add_argument("--title")
    p.add_argument("--author")
    p.add_argument("--user-input")
    p.add_argument("--model")
    p.add_argument("--models")
    p.add_argument("--form", choices=FORMS)
    p.set_defaults(func=cmd_meta)

    for name, fn in (("next", cmd_next), ("advance", cmd_advance)):
        p = sub.add_parser(name)
        p.add_argument("run_dir")
        p.add_argument("--batch", action="store_true", help="also issue following hidden steps that can run in parallel")
        if name == "advance":
            p.add_argument("--cast", action="append", metavar="ROLE=AGENT_ID",
                           help="record the persistent speaker agent for a role (repeatable)")
        p.add_argument("--speaker", action="append",
                       help="who takes the next turn (--floor natural, or a directed program; repeat for "
                            "parallel private preparation)")
        p.add_argument("--say", help="directed programs: this turn's instruction, or - to read it from stdin")
        p.add_argument("--private", metavar="KEY", help="directed programs: preparation nobody hears, saved as {{KEY}}")
        p.add_argument("--segment", type=int, help="directed programs: this turn begins segment N")
        p.add_argument("--last", action="store_true", help="directed programs: this is the final spoken turn")
        p.add_argument("--delivery", help="directed programs: how this turn should sound, for voices that take "
                                          "direction (e.g. \"wry, conceding the point\")")
        p.add_argument("--cuts-in", action="store_true", help="directed programs: this turn interrupts the last one")
        p.set_defaults(func=fn)

    p = sub.add_parser("commit")
    p.add_argument("run_dir")
    p.set_defaults(func=cmd_commit)

    p = sub.add_parser("program", help="revise a directed program")
    p.add_argument("run_dir")
    p.add_argument("--file", required=True)
    p.set_defaults(func=cmd_program)

    for name, fn in (("status", cmd_status), ("finalize", cmd_finalize)):
        p = sub.add_parser(name)
        p.add_argument("run_dir")
        p.set_defaults(func=fn)

    p = sub.add_parser("chapters", help="save chapter points for the finished transcript")
    p.add_argument("run_dir")
    p.add_argument("--plan", required=True, help='"1=Opening statements;6=The jurisdiction question;14=Closing"')
    p.set_defaults(func=cmd_chapters)

    p = sub.add_parser("package")
    p.add_argument("run_dir")
    p.add_argument("--audio", help="the rendered .m4a")
    p.add_argument("--out", help="directory for the .enTalkenator file (default: the run directory)")
    p.add_argument("--display-name", help="default: the title")
    p.add_argument("--content-type", help="default: the template's displayName, as the app uses")
    p.add_argument("--keep-hyphens", action="store_true",
                   help="keep ASCII hyphens in the display name (the app then shows a stray content entry)")
    p.set_defaults(func=cmd_package)

    p = sub.add_parser("pin-agent", help="write a project speaker agent pinned to a full model ID")
    p.add_argument("--model", required=True)
    p.add_argument("--project-dir", default=ROOT)
    p.set_defaults(func=cmd_pin_agent)

    p = sub.add_parser("audit", help="check from the sheet files that every speaker saw the whole transcript")
    p.add_argument("run_dir")
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("watchdog", help="background stall detector; exits on a stall or when the run ends")
    p.add_argument("run_dir")
    p.add_argument("--min-seconds", type=float, default=180,
                   help="floor for a spoken turn (prep steps get 2x, a role's first turn another 1.25x)")
    p.add_argument("--factor", type=float, default=4,
                   help="stall after this many times the run's median write time")
    p.add_argument("--interval", type=float, default=3)
    p.add_argument("--max-minutes", type=float, default=180)
    p.set_defaults(func=cmd_watchdog)

    p = sub.add_parser("nudge", help="restart a stalled turn's clock; prints the reminder to send")
    p.add_argument("run_dir")
    p.add_argument("--turn", type=int, required=True)
    p.set_defaults(func=cmd_nudge)

    p = sub.add_parser("recast", help="re-issue a stalled turn to a fresh speaker")
    p.add_argument("run_dir")
    p.add_argument("--turn", type=int, required=True)
    p.set_defaults(func=cmd_recast)

    p = sub.add_parser("verify", help="replay the app's import logic on an .enTalkenator file")
    p.add_argument("bundle")
    p.set_defaults(func=cmd_verify)

    args = ap.parse_args()
    if hasattr(args, "run_dir") and args.run_dir and args.cmd != "init":
        args.run_dir = os.path.abspath(args.run_dir)
    args.func(args)


if __name__ == "__main__":
    main()
