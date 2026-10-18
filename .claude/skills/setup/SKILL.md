---
name: setup
description: Guided first-time setup for enTalkenator. It checks what's on this Mac, installs the free local voices (with permission), sets up the person's private library and podcast feed on their own free Cloudflare account, and makes a short first program to try it all out. Use when someone is new, says "set me up", "get started", "install", or "how do I use this", or when the session-start status says it's their first visit.
allowed-tools: Read Bash(python3 scripts/*) Bash(bash scripts/*)
---

# Set up enTalkenator

You're walking someone through setup who may not be technical. Follow the "Who you're
helping" and "Rules that always apply" sections of CLAUDE.md throughout. Each part can be
skipped and done later; nothing here is required to make a first program.

Scripts run from the enTalkenator folder, `${CLAUDE_PROJECT_DIR}`. If an earlier command
changed directory, `cd` back there first. Quote other paths; they may contain spaces.

- `STATUS` = `python3 scripts/status.py`
- `VOICES` = `bash scripts/setup_tts.sh`

## 1. See what's there

Run `STATUS` and keep the result. Don't recite it; use it to skip finished parts.

## 2. Say what enTalkenator does, then ask what they want

In three or four short sentences:
- You give it a document, pick a format (a debate, a podcast conversation, a class, an oral
  argument, a quiz...), and Claude writes and performs it with a separate voice for each
  speaker.
- It runs on their Claude plan and their Mac, with nothing to pay enTalkenator.
- Setup has two optional parts: **better voices** (a free download) and **a private
  library** on their own free Cloudflare account, so programs play on their phone and in
  their podcast app.

Then ask with AskUserQuestion (header "Setup"), leaving out parts that are already done:
- "Everything (Recommended)": voices, then the library, then a short first program
- "Just the voices": programs stay on this Mac
- "Just make something": skip setup and go straight to a first program with the macOS
  voices
- "Just the library": if the voices are already installed, or they'll do that later

## 3. Voices

Skip this if `voices` is `kokoro`.
- **Not an Apple Silicon Mac** (`appleSilicon` false): Kokoro won't run there. Say the
  programs will use the built-in macOS voices, or Gemini's voices if they have a Gemini API
  key (paid, roughly $0.40 for 30 minutes). Move on.
- **Otherwise**, ask before installing: "Kokoro is a free voice model (Apache-2.0) that runs
  on your Mac. It's about 660 MB to download and 1.3 GB on disk, in
  `~/.cache/entalkenator`. It sounds much more natural than the macOS voices. The
  install also brings in espeak-ng (GPL-3.0) for pronunciation."
- It needs Python 3.10–3.12, or `uv`. `VOICES` says so if neither is there.
  - If Homebrew is installed (`command -v brew`), offer `brew install uv`, with
    permission.
  - Otherwise send them to https://docs.astral.sh/uv/getting-started/installation/ for the
    macOS installer, or to python.org for Python 3.12.
- If they agree, run `VOICES` in the background. It takes a few minutes; carry on with the
  next part while it runs, and check it finished (`VOICES --check` exits 0) before
  rendering.

## 4. The library

Follow the **setup-library** skill from its step 1. It covers the Cloudflare account,
Node.js, Wrangler, the deploy, the passphrase, and adding the feed to a podcast app.
If they'd rather not have a library, say programs stay in `programs/` on this Mac and can
be published later.

## 5. A first program

Offer a short demo:
- Use their own document if they have one handy. Otherwise use
  `examples/plain-language-opinions.txt`, a short essay arguing that judges should write
  opinions in plain language.
- A good first template is `explainer` at `--loop-scale 0.3`: two friends talk the document
  through, roughly 5 minutes of audio, ready in a few minutes. If they'd rather hear a
  debate, `debate` at the same scale gives about 15–20 minutes of audio and takes about 10
  minutes to make.

Run the **entalkenate** skill with those arguments and its "Use the best" settings. When
it's done:
- If they have a library, publish the program and suggest they open it on their phone.
  The web app's library page shows it, and the podcast app picks it up within a few
  minutes.
- Otherwise `open` the `.m4a` so it plays on the Mac.

## 6. Wrap up

- Run `STATUS --welcomed`.
- Tell them, briefly:
  - To make a program, open this folder in Claude and say something like "make a podcast
    conversation from ~/Downloads/paper.pdf". Or type `/entalkenate`.
  - Templates can be listed ("what formats are there?").
  - Programs are kept in `programs/`.
  - If they downloaded a newer enTalkenator, saying "update my library" redeploys it,
    and their programs and feed address are kept.
