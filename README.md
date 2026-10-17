# enTalkenator for Claude Code (proof of concept)

This plugin runs enTalkenator's generation inside your own Claude Code session
instead of the Swift app's direct API calls. Your Claude subscription writes the
conversation, with each template role voiced by its own subagent. A local model
renders the audio for free. The output is an `.enTalkenator` bundle the existing app
imports, so the app stays the library and player.

```
/entalkenator:entalkenate paper.pdf debate --loop-scale 0.3
```

## What's here

```
plugin/
├── .claude-plugin/
│   ├── plugin.json          manifest (name: entalkenator)
│   └── marketplace.json     single-plugin local marketplace, for `claude plugin install`
├── skills/entalkenate/SKILL.md   the showrunner: arguments, generation loop, render, package
├── agents/speaker.md        one persistent agent per role (Read + Write tools only)
├── scripts/
│   ├── engine.py            template state machine (stdlib Python 3.9+)
│   ├── render_audio.py      transcript -> one .m4a, a voice per role (kokoro | gemini | say)
│   ├── setup_tts.sh         installs the Kokoro engine into a private venv (asks first)
│   └── requirements-kokoro.{in,txt}   pinned dependency set for that venv
├── templates/               snapshot of enTalkenator/Templates/*.json, used outside this repo
└── examples/
    ├── plain-language-opinions.txt   ~500-word sample used for the test run
    └── test-run/                     transcripts from the three modes (+ .m4a and bundle, not committed)
```

There's no top-level `bin/`, because Cowork and claude.ai refuse plugins that have one.
The skill calls scripts by path with `${CLAUDE_PLUGIN_ROOT}`.

## Install and run

Requirements: Claude Code, Python 3.9+ (the macOS system Python is fine), and macOS for
PDF text extraction (PDFKit via `osascript`) and `afconvert`. The Kokoro voices need an
Apple Silicon Mac. `ffmpeg` replaces `afconvert` elsewhere.

Load it for one session:

```bash
claude --plugin-dir ./plugin
```

Or install it from the bundled local marketplace:

```bash
claude plugin marketplace add ./plugin
```

```bash
claude plugin install entalkenator@entalkenator-local
```

Then run, from the project where you want the output:

```
/entalkenator:entalkenate path/to/paper.pdf debate
/entalkenator:entalkenate notes.txt podcast --single
/entalkenator:entalkenate brief.pdf oralargument --loop-scale 0.5 --engine say
```

The template name can be a file stem (`oralargument`), id, or display name
(`"Oral Argument"`). With no template, the skill lists them and asks. Runs go to
`./entalkenator-runs/<template>-<timestamp>/`, which holds the turn sheets, every turn's
text, `transcript.txt`, the `.m4a`, and the bundle.

**Permissions.** The skill pre-approves `Bash(python3 *)` for its own turn. Speakers
write one file per turn into the run directory, so use `acceptEdits` mode, or allow
`Write(entalkenator-runs/**)`. Otherwise you'll get a prompt for every turn.

**Voices.** On first render the skill checks for the Kokoro engine and, if it's missing,
asks before running `scripts/setup_tts.sh`. That install is about 660 MB of downloads and
about 1.3 GB on disk, in `~/.cache/entalkenator/venv` plus the Hugging Face cache. Delete
those two to uninstall. The alternatives are `--engine say` (built-in macOS voices, no
download) and `--engine gemini` (needs `GEMINI_API_KEY`). Pick voices with
`--voices "Hillford Johns=bm_george;The Author=af_heart"`.

## How it maps to the app

`engine.py` re-implements `Template.generateTranscript` / `processStep` /
`handleLoopForDuration` from `Models/Template.swift` and `Models/RawTemplates.swift`.
Claude writes every line.

| App behaviour | Here |
| :- | :- |
| Static context: `initialContext` values joined with blank lines, placeholders filled, optional language line | Same. Values keep JSON order (Swift's dictionary order is random) |
| `{{transcript}}` is the document, with "The title of the text is …" and "The author of the text is …" prepended | Same. The showrunner infers title and author from the opening lines, as the app's extract prompts do |
| History block: `outputTranscriptPrompt` split on `{{output}}`, `[Name]: text` entries, plus the app's `outputTranscriptPromptAddition`; "There has been no conversation yet." when empty | Same text, in the first turn sheet each speaker gets |
| Step prompt is `roleDescription` + blank line + `promptTemplate`, placeholders filled | Same in full sheets. Delta sheets drop the role description and reference the speaker's own notes (see below) |
| `outputKey` stores the output; `includeInTranscript: "false"` hides it | Same |
| `roleName` "A, B" picks one at random | Same (seeded per run) |
| `loop: N` starts a loop with an N-minute target; `loop: "end"` jumps back while accumulated words / 175 < N | Same, including the app's quirk that the counter resets only on first entry. `--loop-scale` multiplies N. The JSON `loopDuration` field is unused by the app, so it's ignored here too |
| Transcript file `[Name]: text\n\n` per visible turn | Same (`transcript.txt`, which becomes `<base>-<Template>.txt` in the bundle) |

Deliberate differences:

- An empty `{{userInput}}` becomes "". The app leaves the literal `{{userInput}}` in the
  prompt.
- Word counts split on any whitespace. The app splits on spaces only, so it undercounts
  across line breaks and runs loops slightly longer.
- Placeholders expand repeatedly, so the result doesn't depend on dictionary order.

### Cast mode: one persistent speaker per role

The app re-sends the static context, the whole document, and the whole transcript on
every call. Cast mode doesn't:

1. On a role's first turn, the showrunner spawns an `entalkenator:speaker` agent with
   the **full sheet**: role card, the app's full prompt (setting, document, history so
   far), and the instruction.
2. On every later turn, it continues that same agent with `SendMessage` and a **delta
   sheet**. The delta holds only the lines said since that role last spoke, plus the
   new instruction. Role descriptions, setting, and document aren't sent again.
   Placeholders that point at the role's own earlier notes (`{{outlineJanson}}`) become
   "[your outlineJanson, which you wrote earlier in this conversation]". In all 16
   bundled templates, every `outputKey` placeholder is read only by the role that wrote
   it, so notes are never re-sent.
3. The engine tracks which history each role has seen (`seen`), who wrote which
   variable (`var_author`), and each role's agent ID (`cast`, registered with
   `advance --cast "Role=agentId"`), so a compacted showrunner can recover.

Speakers write their lines to a file, and the showrunner never reads or re-types them,
so its own context stays small. Consecutive private prep steps for different roles are
issued together (`--batch`) and run in parallel.

`--mode fresh` keeps the app's stateless behaviour: a new agent with a full sheet on
every turn. `--mode single` has the showrunner write every role itself.

## Bundle format

`engine.py package` writes a zip laid out like `enTalkenator/Default Items/default.enTalkenator`:

```
<Display Name>/info.txt                    {"displayName","author","baseFileName","sourceFileType"}
<Display Name>/<UUID>-original.txt|.pdf    source (copied as is for PDFs)
<Display Name>/<UUID>-original-transcript.txt   the extracted source text (the app's "transcript" type)
<Display Name>/<UUID>-original-Debate.txt  the generated conversation
<Display Name>/<UUID>-original-Debate.m4a  mono 24 kHz AAC, like the app's own files
```

The content type is the template's `displayName`, as the app uses. `engine.py verify
<bundle>` replays `LibraryItem.importFromEnTalkenator` and `scanGeneratedContent` in
Python and lists the library entries the app would show. On the bundled examples it
reproduces what the app shows.

## Voice engines and licences

| Engine | What runs | Cost | Licence notes |
| :- | :- | :- | :- |
| `kokoro` (default) | [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) via [mlx-audio](https://github.com/Blaizzy/mlx-audio) 0.5.8, weights `mlx-community/Kokoro-82M-bf16` | free, local | Kokoro weights **Apache-2.0** (commercial use allowed). mlx-audio and MLX are MIT. The misaki G2P is Apache-2.0. The out-of-vocabulary fallback is espeak-ng via `espeakng-loader` and `phonemizer-fork`, both **GPL-3.0**: fine for local use, but check before redistributing a bundled app. num2words is LGPL. spaCy and `en_core_web_sm` are MIT. torch is BSD-style |
| `gemini` | `gemini-3.8-flash-tts` through the Interactions API, one request per turn (so the 2-speakers-per-request limit never applies), delivery notes in `speech_metadata.style` because 3.8 reads inline cues aloud | about $0.41 per 30 minutes until Dec 31 2026, about double from Jan 1 2027 | Google API terms. **Untested here**: no `GEMINI_API_KEY` on this machine, so the request follows the docs |
| `say` | macOS built-in voices | free, no install | Apple system voices; robotic but instant |

The Kokoro worker gets one voice per role from `am_michael, af_heart, bm_george,
af_bella, am_fenrir, bf_emma, …`, alternating register and accent. The `a`/`b` prefix in
a voice name means American or British, and `f`/`m` means female or male;
`render_audio.py --list-voices` prints that. The skill tells the showrunner to cast
voices from how the transcript refers to each role, not from names.

## Test run (2026-10-17)

This was the `debate` template on `examples/plain-language-opinions.txt`, a ~500-word
op-ed, with `--loop-scale 0.3`. That makes two 3-minute loops instead of 10-minute ones.
There were no audience questions. The run used Opus 5.5 (speakers inherit the session
model) on an M5 Pro with 64 GB. The outputs are in `examples/test-run/`: the three
transcripts are committed, and the `.m4a` and `.enTalkenator` stay on disk only, via
`.gitignore`.

| | Cast, persistent (default) | Cast, fresh agent each turn | Single writer |
| :- | :- | :- | :- |
| Spoken turns + private prep | 15 + 2 | 11 + 2 | 13 + 2 |
| Spoken words / audio | 3,662 / 21.0 min | 3,712 / 20.8 min | 3,438 / ~19.6 min (not rendered) |
| Average spoken turn | 244 words (157–414) | 337 words (244–478) | 264 words (176–360) |
| Generation wall time | **9.5 min** (568 s) | 10.6 min (635 s) | **4.3 min** (255 s) |
| Speaker agents | 3 (cast once, then 14 `SendMessage` turns) | 13 | 1 |
| Turn-sheet words the speakers had to read | **10,802** (3 full sheets + 14 deltas) | 40,833 | one full sheet + short per-step instructions |

The same 17 turns sent as full sheets would have been 56,673 words, so the deltas cut
speaker input by 5.2×. Full sheets grow with the square of the conversation length and
deltas grow linearly, so the gap widens on the 10-minute loops.

Other timings:
- **Kokoro install** (`setup_tts.sh`): 140 s, making a 1.1 GB venv plus 339 MB of
  weights. The first Kokoro call after install took 18 s to set up the G2P pipeline;
  later model loads take under 1 s.
- **Render**: 21.0 minutes of audio in 41 s (about 30× realtime). The fresh-cast
  transcript rendered in 46 s.
- **Package and verify**: under 1 s. The bundle is 7.7 MB.

End to end, a ~20-minute debate took about 10 minutes of generation plus under a minute
of audio, with no API spend.

### Observations

**Transcript quality: persistent cast was the best of the three.** Positions moved
over the evening. Dana conceded points and rewrote her own proposal on stage ("what it
means for you" became "what changed and what didn't"). Janson took credit for that, and
the moderator kept count of the concessions. Images came back across turns: the "note
on the door", Pascal, the parent at the kitchen table. That only works when each speaker
remembers what it said and hears what the others say. Turns were short and lively, so
the second loop ran a second pass, giving 15 spoken turns against 11.

**Fresh-agent cast** turns were good one at a time but long. Speakers that get a fresh
full sheet ignored "Be brief!" (average 337 words, max 478), so each loop finished after
one exchange. Each turn also starts cold.

**Single writer** was the fastest and cheapest, and the prose is polished. But the agent
reported, unprompted, that because it wrote both prep outlines it knew each side's plan,
so the debaters anticipated each other's attacks too neatly, and it had to hold rebuttals
back on purpose. The register drifted formal ("I will", "do not") for every role.

**In all three runs**, no speaker labels, stage directions, or markdown leaked into the
spoken text. With no audience questions, the moderator put a question of their own, as
the template intends. One template quirk: the fresh-cast moderator said "before we turn to the
audience", and the template never gives the audience a turn. Speakers cited real things:
*United States v. Detroit Timber & Lumber Co.* (1906) behind the syllabus disclaimer,
FRAP 32.1 on citing unpublished opinions, and the UK Supreme Court's press summaries. I
spot-checked these and found nothing wrong beyond rounding 32.1's 2007 effective date to
2006. They're still generated claims, not sourced ones.

**Local voice quality.** I couldn't listen to the audio, so these checks are objective
only. Every clip runs at 162–193 words per minute, so nothing was skipped or truncated.
Loudness is evened out per clip, and the espeak-ng fallback loaded, so unknown names
like "Skullbottom" are phonemized rather than dropped. Kokoro's own grades put `af_heart`
at A, `bm_george` at C, and `am_michael` at C+. **Listen before judging.** The first
render exposed a real casting problem: auto-assignment gave Janson a female voice, but
the transcript calls him "a man" and "his". The fix is the voice-casting step in the
skill. The final `.m4a` uses `am_michael`, `bm_george`, and `af_heart`.

### What didn't work, and workarounds

- **No headless plugin run.** The Claude Code CLI bundled with the desktop app isn't
  logged in, so `claude --plugin-dir ./plugin -p "/entalkenator:entalkenate …"` couldn't
  run. The test drove the skill's exact loop from a desktop session. Speakers were
  general-purpose subagents briefed with `agents/speaker.md` verbatim. So
  `claude plugin validate --strict` passes for the manifest, marketplace, and agents, but
  three things haven't run for real: the `/entalkenator:entalkenate` command, the
  `entalkenator:speaker` agent type, and the skill's `allowed-tools`. Those
  general-purpose stand-ins also carried about 60k tokens of system prompt and tool
  definitions per call. The real speaker has only Read and Write, so per-turn overhead
  should be far lower.
- **`SendMessage` replies are asynchronous.** A continued speaker's answer arrives as a
  message, not a tool result. The showrunner has to wait for it, or poll for the output
  file, before `advance`. Committing as soon as the file appears could catch a speaker
  mid-revision. That never happened here (all 15 committed spoken turns matched the files
  on disk), but `finalize` now reports any turn file that changed after commit.
- **MLX crashed silently once** after rendering all 15 clips and before encoding: no
  output, no traceback. Kokoro now renders in a worker process that hard-exits. The parent
  never loads MLX, retries the worker once, and stitches the clips. Clips are cached by
  text and voice, so re-runs and voice changes only render what's missing.
- **`say -v ?` changed format mid-session** ("Daniel" became "Daniel (English (UK))"),
  which broke voice discovery. The parser now handles both.
- **Hyphens in titles.** On import the app makes the display name the file prefix, and
  `LibraryItem.scanGeneratedContent` names content after the last `-`. So
  "Plain-Language" would show a bogus "Language Summary" entry. The bundled
  `default2.enTalkenator` ("Last-Mile …") has the same problem. `package` swaps ASCII
  hyphens in the display name for U+2011, which looks identical. `--keep-hyphens` turns
  that off.
- **Cowork is untested.** There's no `bin/`, but Kokoro (MLX), PDF extraction (PDFKit
  via `osascript`), and `afconvert` all need macOS on the host. In a Linux sandbox only
  `--engine gemini` plus `ffmpeg` would work.

### App issues noticed (not fixed: the Swift app was out of scope)

- `scanGeneratedContent` treats any `-` in the display name as a content-type separator
  (see above).
- A blank user input leaves the literal `{{userInput}}` in the prompt
  (`Template.generateTranscript` only sets the variable when non-empty).
- `RawStep.loopDuration` is decoded but never used. The loop minutes come from `loop: N`.
- Loop word counts use `split(separator: " ")`, so words across line breaks aren't
  counted and loops run a little long.
- `importFromEnTalkenator` finds the source by `components(separatedBy: ".")[0] ==
  baseFileName`, so a `baseFileName` containing a dot can never import. The app's own
  `<UUID>-original` names are safe.
