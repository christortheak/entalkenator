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
them pick. Run `VOICE_OPTIONS machine` first: the Mac's chip and memory, which local voices
fit, and how fast any has already rendered here. Then `VOICE_OPTIONS list --words 3500` for
each option's cost, work, download, memory, licence, and readiness, with estimates for a
typical 20-minute program. Present prices as approximate ("about"): they were checked on
the catalogue's `asOf` date.

**Speak of work, not minutes, for local voices.** How long a local voice takes depends on
the Mac, so never quote another machine's times. Each option has a `compute` word (light,
heavy, very heavy) and a `work` factor relative to Kokoro. Once a voice has rendered here,
the estimate's `renderMinutes` is this Mac's own figure; before that, say how heavy it is
and that the first program will show how long it takes. Help them choose what will work:
- The memory a voice needs is a hard limit: leave out what doesn't fit.
- An older or base chip (an M1 or M2 without Pro, Max or Ultra), or a Mac with only the
  memory a voice needs, will find the heavy voices slow. Say so, recommend Kokoro for
  everyday use, and suggest a heavy voice for short programs or a cloud voice for long
  ones.
- For someone who wants the best sound on long programs but has a modest Mac, recommend a
  cloud voice: Gemini is about $0.20–$0.30 for 20 minutes and takes no compute here.

**What's worth trying here**, from `machine`:
- No Apple Silicon (an Intel Mac): the macOS voices, or a cloud voice.
- Apple Silicon with 8 GB: Kokoro. For more expressive voices, a cloud voice.
- 16 GB: Kokoro, plus Qwen3-TTS (designed voices, heavy) and the Qwen3 preset voices
  (directed, medium; two native English voices, both men).
- 24 GB or more: all of those, plus Breeze TTS 2, the most natural (very heavy).
- A base M1 or M2 (no Pro, Max or Ultra): the heavy voices will be slow, so suggest them
  for short programs and a cloud voice for long ones.
Kokoro is always the first install: the others build on it, and it's the everyday choice.
Suggest one richer local voice that fits, not all of them; they can add more later.

Explain the kinds in a sentence each:
- **Free, on this Mac.** Kokoro is natural-sounding and light. Qwen3-TTS is more
  expressive: each character's voice is designed from a description and kept the same
  throughout (heavy, 16 GB or more). The Qwen3 preset voices follow a delivery note on
  every line and are quicker (medium, 16 GB or more), but only two are native English
  speakers, both men; the other presets speak English with a Chinese, Japanese or Korean
  accent. Breeze TTS 2 is the most natural: designed voices that can be directed line by
  line and that laugh and sigh (very heavy, 24 GB or more; its licence allows personal,
  educational and research use only, and it speaks English and Chinese). The built-in
  macOS voices need no install but sound robotic.
- **Cloud, with their own key.** Better voices, paid per program to the provider, never
  to enTalkenator: Google Gemini (best value), OpenAI, and ElevenLabs (best quality,
  about 5× Gemini's price).
- Programs can use any installed option, and the choice can change any time.

Then ask with AskUserQuestion (header "Voices", multiSelect), leaving out what can't run
here (an option whose `needs` say `unsupported`) and saying what's already installed:
- "Kokoro: free, on this Mac (Recommended)": about 660 MB to download, light work
- "Expressive voices (Qwen3-TTS): free, on this Mac": 6.2 GB more, about 10 GB of memory,
  heavy work (about 7× Kokoro)
- "Directed preset voices (Qwen3): free, on this Mac": 3.1 GB more, about 6 GB of memory,
  medium work (about 3× Kokoro); two English voices, both men
- "Most natural voices (Breeze TTS 2): free for personal use": 7.6 GB more, about 13 GB of
  memory, very heavy work (about 12–20× Kokoro); non-commercial licence
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
- **Qwen3 preset voices:** after Kokoro, `VOICE_OPTIONS install qwen3-preset` in the
  background.
- **Breeze TTS 2:** after Kokoro, `VOICE_OPTIONS install breeze` in the background. Mention
  its licence in a sentence: free for personal, educational and research use, not for
  commercial use, and no cloning a real person's voice without their consent.
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

### Recorded voices

Once Qwen3-TTS or Breeze is installed, mention in a sentence that either can speak in a
voice made from a short recording: their own, say, as the host of their programs. Offer it;
don't push it. If they want one, or ask for it any time later ("can it sound like me?"):

1. **Whose voice?** Their own, or someone who has agreed to it. For someone else, ask how
   they agreed (in person, by email), and record that with `--consent`. Never a public
   figure, a clip from the internet, or anyone who hasn't agreed: say so plainly if asked.
2. **The script:** `VOICE_OPTIONS recorded script` gives about 15 seconds of conversational
   text. Show it to them as is.
3. **Recording:** open Voice Memos (`open -a "Voice Memos"`) or QuickTime Player
   (`open -a "QuickTime Player"`, then File › New Audio Recording). Ask them to:
   - find a quiet room, without music or a fan
   - sit about a hand's width from the Mac or a headset microphone
   - read the script once, naturally, as if talking to someone
   - aim for 10–15 seconds; a longer clip slows every line in Breeze
   Then they share or drag the recording to Downloads (or into this chat) and tell you
   where it is.
4. **Keep it:** `VOICE_OPTIONS recorded add "<Name>" --audio <file> --whose own` (or
   `--whose consented --consent "<how they agreed>"`). If they read something other than
   the script, pass its exact words as `--transcript`. Pass on any `advice` (too short or
   too long).
5. **Hear it:** render the opening of their first program, or the example, with that voice
   for one role: `RENDER ... --engines "<Role>=breeze" --voices "<Role>=@<Name>" --sample 60`,
   and `open` it. If it sounds off, the usual fix is a quieter room or a more natural read.

`VOICE_OPTIONS recorded list` shows their voices, and `recorded remove "<Name>"` deletes one
(the recording is kept only in `~/.config/entalkenator/recorded-voices/`, readable only by
them).

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
- **If more than one local voice is ready**, offer to hear the start of this program in
  each, so they can choose by ear: `RENDER --transcript <dir>/transcript.txt --out
  "<dir>/sample-<id>.m4a" --engine <id> --sample 90` per voice (with the same `--voices`,
  `--lines` and `--styles` you'd use for a full program), then `open` each. Each sample also
  times that voice on this Mac, so later estimates are this Mac's own. Then ask which to
  keep as the default.

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
