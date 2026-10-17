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

Subcommands (all print JSON on stdout):
  templates                       list the bundled templates
  init     --template --source --run-dir [...]
                                  extract the document, create the run
  meta     RUN [--title] [--author] [--user-input]
                                  fix title, author, or user input before the first step
  next     RUN [--batch]          issue the next step(s); writes turn sheets
  commit   RUN                    fold the written outputs of the pending step(s) in
  advance  RUN [--batch] [--cast ROLE=AGENT_ID ...]
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

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
    # Plugin inside the enTalkenator repo: <repo>/plugin/scripts -> <repo>/enTalkenator/Templates
    candidates.append(os.path.join(os.path.dirname(PLUGIN_ROOT), "enTalkenator", "Templates"))
    # Snapshot shipped with the plugin for installs outside the repo.
    candidates.append(os.path.join(PLUGIN_ROOT, "templates"))
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


def role_by_name(template, name):
    for r in template["roles"]:
        if r["name"] == name:
            return r
    return None


def pick_role(st, step):
    names = [n.strip() for n in step["roleName"].split(",") if n.strip()]
    if not names:
        fail("Step has no roleName: %r" % step)
    if len(names) == 1:
        return names[0]
    rng = random.Random("%s:%d" % (st["seed"], st["turn_counter"]))
    return rng.choice(names)


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
        })
    emit({"templatesDir": d, "templates": out})


def cmd_init(args):
    tdir = find_templates_dir(args.templates_dir)
    tpath, template = resolve_template(args.template, tdir)
    run = os.path.abspath(args.run_dir)
    if os.path.exists(state_path(run)) and not args.force:
        fail("%s already holds a run. Use --force to start over, or pick another --run-dir." % run)
    os.makedirs(os.path.join(run, "turns"), exist_ok=True)
    os.makedirs(os.path.join(run, "source"), exist_ok=True)

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
    source_copy = os.path.join(run, "source", "original." + source_type)
    if source_type == "pdf":
        shutil.copyfile(src, source_copy)
    else:
        write_text(source_copy, text)
    write_text(os.path.join(run, "document.txt"), text)

    title = args.title or os.path.splitext(os.path.basename(src))[0]
    models = parse_models(args.model, args.models, template)
    st = {
        "version": 1,
        "run_dir": run,
        "template_path": tpath,
        "template_name": template["displayName"],
        "source_path": src,
        "source_type": source_type,
        "title": title,
        "author": args.author or "Unknown Author",
        "user_input": args.user_input or "",
        "language": args.language or "",
        "loop_scale": args.loop_scale,
        "max_turns": args.max_turns,
        "mode": args.mode,
        "models": models,   # role -> "" (session model), an alias, or a full model ID
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
        "documentHead": text[:800],
    })


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
    save_state(args.run_dir, st)
    emit({"title": st["title"], "author": st["author"], "userInput": st["user_input"],
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
        "Turn %d. You are still %s. %s\n\n%s\n\nYour instruction for this turn:\n\n%s\n\n%s\n"
    ) % (
        n, role_name,
        "Your words go into the spoken transcript." if visible
        else "This is a private preparation step: nobody hears it, but your notes are saved for later steps.",
        wrap_long_lines(said),
        wrap_long_lines(step_prompt_for_known_context(st, template, step, role_name, variables)),
        END_MARKER,
    )


def issue(st, template, index):
    step = template["steps"][index]
    role_name = pick_role(st, step)
    role = role_by_name(template, role_name)
    if role is None:
        fail("Step %d names role %r, which the template does not define." % (index, role_name))
    variables = variables_of(st, template)
    full_prompt, role_prompt = build_prompt(st, template, step, role, variables)
    st["turn_counter"] += 1
    n = st["turn_counter"]
    base = os.path.join(st["run_dir"], "turns", "%03d-%s" % (n, slug(role_name)))
    prompt_file = base + ".prompt.md"
    output_file = base + ".txt"
    visible = is_visible(step)
    sheet = (
        "ROLE CARD: you are %s.\n%s\n\n"
        "Turn %d. %s\n\n"
        "=== PROMPT (as the enTalkenator app would send it) ===\n\n%s\n\n%s\n"
    ) % (
        role_name,
        role["roleDescription"],
        n,
        "Your words go into the spoken transcript." if visible
        else "This is a private preparation step: nobody hears it, but your notes are saved for later steps.",
        wrap_long_lines(full_prompt),
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
    mode = st.get("mode", "cast")
    if mode == "cast" and st.get("cast", {}).get(role_name):
        # This role already has a persistent speaker: send only what it missed.
        delta_file = base + ".delta.md"
        write_text(delta_file, delta_sheet(st, template, step, role_name, variables, n, visible))
        pend["delta_file"] = delta_file
        pend["agent"] = st["cast"][role_name]
    elif mode == "single":
        pend["instruction"] = role["roleDescription"] + "\n\n" + step_prompt_for_known_context(
            st, template, step, role_name, variables, writer_knows_all=True)
    return pend, role_prompt


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
        return {"agentType": "entalkenator:speaker"}
    if model.lower() in MODEL_ALIASES:
        return {"agentType": "entalkenator:speaker", "model": model.lower()}
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
    steps = template["steps"]
    idx = st["current_index"]
    pend, _ = issue(st, template, idx)
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
    if st["current_index"] >= len(template["steps"]):
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
        committed.append({"turn": pend["turn"], "role": pend["role"], "visible": pend["visible"],
                          "words": word_count(text), "preview": text[:160].replace("\n", " ")})
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
        "changedAfterCommit": changed,
        "viewAudit": audit_views(st, template),
    })


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
    """Replay LibraryItem.importFromEnTalkenator and scanGeneratedContent on a bundle.

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
    source = [f for f in files if f.split(".")[0] == base]
    transcript = [f for f in files if f.endswith("-transcript.txt")]
    if not source or not transcript:
        raise ValueError("needs <baseFileName>.<ext> and a *-transcript.txt file")
    # LibraryItem.init: new base = display name with "/" -> "-"; other files keep their suffix.
    new_base = (info.get("displayName") or "Imported Item").replace("/", "-")
    old_prefix = os.path.splitext(source[0])[0]
    src_ext = os.path.splitext(source[0])[1].lstrip(".")
    imported = ["%s.%s" % (new_base, src_ext), "%s-transcript.txt" % new_base]
    if any(f.endswith("-transcript.m4a") for f in files):
        imported.append("%s-transcript.m4a" % new_base)
    for f in files:
        if f.split(".")[0] == base or f.endswith("-transcript.txt") or f.endswith("-transcript.m4a") or f.endswith("info.txt"):
            continue
        imported.append(f.replace(old_prefix, new_base))
    # scanGeneratedContent
    content = {}
    t_files = [f for f in imported if f.endswith("-transcript.txt")]
    if t_files:
        b = os.path.splitext(t_files[0])[0].replace("-transcript", "")
        if any(os.path.splitext(f)[0] == b for f in imported):
            content["original"] = {"txt": True}
    for f in imported:
        stem, ext = os.path.splitext(f)
        if "-" not in stem:
            continue
        name = [p for p in stem.split("-") if p][-1]
        if ext.lower() not in (".txt", ".m4a"):
            continue
        content.setdefault(name, {})[ext.lower().lstrip(".")] = True
    return {"folder": folder, "itemFiles": imported,
            "libraryEntries": {k: sorted(v) for k, v in sorted(content.items())}}


def cmd_pin_agent(args):
    """Write a project agent that runs the speaker on a full (older) model ID."""
    model = args.model.strip()
    if not model.startswith("claude-"):
        fail("pin-agent takes a full model ID such as claude-opus-4-8. Aliases (fable, opus, sonnet, "
             "haiku) need no pinned agent.")
    src = os.path.join(PLUGIN_ROOT, "agents", "speaker.md")
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
    p.add_argument("--template", required=True, help="file stem (debate), id, displayName, or a .json path")
    p.add_argument("--source", required=True, help="PDF, .txt, or .md file")
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
    p.add_argument("--mode", choices=["cast", "fresh", "single"], default="cast",
                   help="cast: one persistent speaker agent per role (default); fresh: a new speaker "
                        "agent every turn; single: the showrunner writes every turn")
    p.add_argument("--templates-dir")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("meta")
    p.add_argument("run_dir")
    p.add_argument("--title")
    p.add_argument("--author")
    p.add_argument("--user-input")
    p.add_argument("--model")
    p.add_argument("--models")
    p.set_defaults(func=cmd_meta)

    for name, fn in (("next", cmd_next), ("advance", cmd_advance)):
        p = sub.add_parser(name)
        p.add_argument("run_dir")
        p.add_argument("--batch", action="store_true", help="also issue following hidden steps that can run in parallel")
        if name == "advance":
            p.add_argument("--cast", action="append", metavar="ROLE=AGENT_ID",
                           help="record the persistent speaker agent for a role (repeatable)")
        p.set_defaults(func=fn)

    p = sub.add_parser("commit")
    p.add_argument("run_dir")
    p.set_defaults(func=cmd_commit)

    for name, fn in (("status", cmd_status), ("finalize", cmd_finalize)):
        p = sub.add_parser(name)
        p.add_argument("run_dir")
        p.set_defaults(func=fn)

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
    p.add_argument("--project-dir", default=".")
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
