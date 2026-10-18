---
name: setup
description: Guided first-time setup for enTalkenator. It checks what's on this Mac, lets the person choose their voices (free ones on this Mac, or cloud voices with their own key), sets up the person's private library and podcast feed on their own free Cloudflare account, and makes a short first program to try it all out. Use when someone is new, says "set me up", "get started", "install", or "how do I use this", or when the session-start status says it's their first visit.
allowed-tools: Read Bash(python3 scripts/*) Bash(bash scripts/*)
---

# Set up enTalkenator

You're walking someone through setup who may not be technical. Follow the "Who you're
helping" and "Rules that always apply" sections of CLAUDE.md throughout. Each part can be
skipped and done later; nothing here is required to make a first program.

Scripts run from the enTalkenator folder, `${CLAUDE_PROJECT_DIR}`. If an earlier command
changed directory, `cd` back there first. Quote other paths; they may contain spaces.

- `STATUS` = `python3 scripts/status.py`
- `VOICE_OPTIONS` = `python3 scripts/voices.py`

## 1. See what's there

Run `STATUS` and keep the result. Don't recite it; use it to skip finished parts.

If it says `"mac": false`, stop here: you're not on their Mac (in Cowork, for example), and
nothing installed here would reach it. Don't offer setup or installs. Tell them where to run
it, from `startHere`, in plain words, and why in one sentence: the voices, their keys, and
their documents are on the Mac.

## 2. Say what enTalkenator does, then ask what they want

In three or four short sentences:
- You give it a document, pick a format (a debate, a podcast conversation, a class, an oral
  argument, a quiz...), and Claude writes and performs it with a separate voice for each
  speaker.
- It runs on their Claude plan and their Mac, with nothing to pay enTalkenator.
- Setup has two optional parts: **voices** (free ones that run on this Mac, or cloud voices
  with their own key) and **a private library** on their own free Cloudflare account, so
  programs play on their phone and in their podcast app.

Then ask with AskUserQuestion (header "Setup"), leaving out parts that are already done:
- "Everything (Recommended)": voices, then the library, then a short first program
- "Just the voices": choose and install voices; programs stay on this Mac
- "Just make something": skip setup and go straight to a first program with the macOS
  voices
- "Just the library": if the voices are already installed, or they'll do that later

## 3. Voices

Every voice option is the person's choice: present them all, with their trade-offs, and let
them pick. Run `VOICE_OPTIONS list --words 3500`, which gives each option's cost, speed,
download, memory, licence, and readiness on this Mac, with estimates for a typical
20-minute program. Present prices as approximate ("about"): they were checked on the
catalogue's `asOf` date.

Explain the three kinds in a sentence each:
- **Free, on this Mac.** Kokoro is natural-sounding and fast. Qwen3-TTS is more expressive:
  each character's voice is designed from a description and kept the same throughout. It
  needs Apple Silicon with 16 GB or more, and is 4–5× slower. The built-in macOS voices
  need no install but sound robotic.
- **Cloud, with their own key.** Better voices, paid per program to the provider, never
  to enTalkenator: Google Gemini (best value), OpenAI, and ElevenLabs (best quality,
  about 5× Gemini's price).
- Programs can use any installed option, and the choice can change any time.

Then ask with AskUserQuestion (header "Voices", multiSelect), leaving out what can't run
here (an option whose `needs` say `unsupported`) and saying what's already installed:
- "Kokoro: free, on this Mac (Recommended)": about 660 MB to download, a 20-minute program
  in about 1–2 minutes
- "Expressive voices (Qwen3-TTS): free, on this Mac": 6.2 GB more, about 10 GB of memory, a
  20-minute program in about 10 minutes on a fast Mac
- "Cloud voices with my own key": Gemini, OpenAI or ElevenLabs, about $0.20–$1.75 for a
  20-minute program
- "Just the macOS voices": nothing to install

Picking an option whose size is stated is the go-ahead to install it. Then:
- **Kokoro:** `VOICE_OPTIONS install kokoro` in the background (a few minutes). It needs
  Python 3.10–3.12 or `uv`; if it says neither is there, offer `brew install uv` when
  Homebrew is installed (`command -v brew`), with permission, or point them to the uv
  installer (https://docs.astral.sh/uv/getting-started/installation/). Carry on with the
  next step while it runs.
- **Qwen3-TTS:** after Kokoro, `VOICE_OPTIONS install qwen3` in the background.
- **Cloud:** ask which providers (header "Cloud", multiSelect), each with its
  `summary`, cost for 20 minutes, and terms:
  - "Google Gemini (Recommended)": the Flash and Flash-Lite models; the key is free to
    create at Google AI Studio. Its free tier may use prompts to improve Google's
    products; paid use doesn't.
  - "OpenAI": gpt-4o-mini-tts, tts-1 or tts-1-hd; needs prepaid credit. OpenAI requires
    telling listeners the voices are AI-generated, and the library does that on every
    program.
  - "ElevenLabs": the best-sounding, at about 5× Gemini's price.
  For each, run `open <key page>` (the option's `needs` has the `url`). Tell them how to
  create the key there, then that a small enTalkenator window will ask for it. They
  create the account and add any card themselves. Run
  `VOICE_OPTIONS key set <gemini|openai|elevenlabs>`. It checks the key with a free
  request and saves it in their Mac's Keychain (they can see it in Keychain Access under
  "enTalkenator"). Never ask them to
  paste a key into the chat. If they paste one anyway, don't repeat it, and suggest they
  revoke it and make a new one.

Finally, if more than one option is ready, ask which to use by default (header "Default"),
recommending the best-sounding one they set up. Save it with
`VOICE_OPTIONS default set <id>`; with one option, set that. They can still pick other
voices for any program.

## 4. The library

Follow the **setup-library** skill from its step 1. It covers the Cloudflare account,
Node.js, Wrangler, the deploy, the passphrase, and adding the feed to a podcast app.
If they'd rather not have a library, say programs stay in `programs/` on this Mac and can
be published later.

## 5. A first program

Offer a short demo, either kind:
- **A narration** of a paper they have handy, or of a few pages of a long one (the
  **narrate** skill, `--pages`). It's the quickest way to hear what enTalkenator does with a
  PDF: about 1½ minutes of audio per page, ready in a few minutes.
- **A program:** use their own document if they have one handy. Otherwise use
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
  - To hear a paper itself, open this folder in Claude and say "read me ~/Downloads/paper.pdf"
    (or type `/narrate`).
  - To make a program, say something like "make a podcast conversation from
    ~/Downloads/paper.pdf" or "a seminar on the articles in this folder", or type
    `/entalkenate`.
  - Templates can be listed ("what formats are there?").
  - Programs are kept in `programs/`.
  - If they downloaded a newer enTalkenator, saying "update my library" redeploys it,
    and their programs and feed address are kept.
