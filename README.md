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
    └── test-run/                     transcripts from the four test runs (+ .m4a files and bundle, not committed)
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
(`"Oral Argument"`). With no template, the skill lists them and asks.

**Settings menu.** Unless the arguments already settle everything, the skill opens with
one question: **Use the best (Recommended)** or **Customize**. "Best" means your
session's model for every speaker, voices cast automatically, full length, and one agent
per role. Customize asks four things at once:

| Question | Choices |
| :- | :- |
| Speaker model | Opus, Fable, Sonnet, Haiku. "Other" takes a past version as a full model ID (`claude-opus-4-8`) or a per-role mix (`Hillford Johns=haiku; *=opus`) |
| Length | Full, Short (~30%), Medium (~60%), or a custom loop scale |
| Voices | Cast them for me, or audition: each role's first line read in 3–4 candidate voices, then you pick |
| Writers | One agent per role, single writer, or a fresh agent every turn |

The same choices are available as flags: `--model`, `--models`, `--loop-scale`,
`--voices`, `--single` / `--fresh`.

How models reach the speakers:
- The four aliases go straight to the Agent tool's per-spawn `model`, and a continued
  speaker keeps it for the whole run.
- Claude Code only accepts older versions as a full ID in an agent's frontmatter, so
  `engine.py pin-agent --model <id>` writes
  `.claude/agents/entalkenator-speaker-<id>.md` in your project.
- Claude Code hot-loads that file only if `.claude/agents/` existed when the session
  started. Otherwise the skill says a restart is needed and offers the nearest alias.
- Fable may bill to usage credits rather than plan limits on some plans, and an
  organization's `availableModels` list can block any of these. Runs go to
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
4. **`engine.py audit`** (also run by `finalize`) checks the deltas from the outside.
   It re-reads the sheet files each speaker was actually given and rebuilds, turn by
   turn, what that agent had seen: the transcript in its first full sheet, plus every
   delta since, plus its own lines. That must equal the true transcript up to the turn,
   line for line and in order.
   - It passed on all 60 speaker turns in the four cast runs, including the re-cast.
   - On deliberately corrupted copies it caught a dropped line, a duplicated line, two
     swapped lines, and a line cut in half.
   - What it can't see is whether a speaker actually read its whole sheet. Speakers are
     told to read to the closing marker.

Speakers write their lines to a file, and the showrunner never reads or re-types them,
so its own context stays small. Consecutive private prep steps for different roles are
issued together (`--batch`) and run in parallel.

`--mode fresh` keeps the app's stateless behaviour: a new agent with a full sheet on
every turn. `--mode single` has the showrunner write every role itself.

### Characters and director's notes

**Character review.** The opening menu offers "Review the characters first" ("Review the author's voice first" for a one-role written piece). The
showrunner lists each role's description from the template, then offers three choices:
- **Use them as written.**
- **Flesh them out:** a 60–100-word sketch per role covering voice, background,
  temperament, what they want, what they're touchy about, and pronouns. You approve or
  edit the sketches.
- **Edit them yourself.**

Under the hood, `engine.py role --role R --sketch "..."` adds a sketch to that speaker's
role card. `--description "..."` replaces the template's description everywhere the
app would use it. Both lock once the character's first turn is issued. The sketch's
pronouns also steer voice casting.

**Director's notes.** Before each spoken turn, the engine checks the transcript. If the
speaker about to talk has picked up a habit, it appends a private one-line note to the
end of their sheet. It checks for:
- opening by quoting the other side back (2 of their last 3 turns)
- stock concessions ("fair hit", "credit where due"…)
- the "That's not X. It's Y." device three or more times
- repeating their own earlier line word for word
- a turn that ran long where the template asks for brevity

A moderator or host also gets the running time. Each note goes to a speaker once. No
model calls are involved. `--no-director` turns notes off.

Replayed against the four earlier test transcripts, every flag was a real instance:
- "A note on the door." opening by echoing the previous turn
- the Author repeating "did not decide what happens when the notice is handed over in
  person" word for word
- the single writer's "not X. It is Y." habit

A "refrain" check (phrases reused across turns) was tried and dropped. Word counts
can't tell a decorative image ("kitchen table") from a term the argument is about
("trial judge", "help desk"), and a director that tells debaters to stop saying "trial
judge" does harm. The speaker brief handles callbacks instead ("use a memorable image
once").

**Probe.** I rebuilt the Opus debate as it stood before the Author's 14th turn, when
two notes would have fired: she had just repeated an earlier line word for word, and run
414 words in a "Be brief!" stretch. A fresh Sonnet speaker, with the new brief and a
character sketch, wrote the turn from that point:
- **It followed both notes silently:** 173 words, no repeated line, and no mention of
  notes or a director.
- **It used its sketch:** "here's what I saw in legal aid", "I'll put it on your tab".
- **It still opened by echoing Janson** ("Take the credit, Janson."), just as the
  original Opus speaker did. The brief alone didn't stop that, and the echo note only
  fires after a speaker has done it twice.

This is one probe, not a measured improvement. The original Opus turn at that point was
short too.

**Forms.** Templates aren't all debates, so realism is defined per form. The engine
guesses each template's form; `--form` overrides it.

| Form | Templates | Performance notes, in short |
| :- | :- | :- |
| debate | debate | React before you argue; vary length; concede in your own words; neutral moderator |
| hearing | oralargument | Counsel answer the bench first and directly; judges ask short, pointed questions |
| conversation | podcast, explainer, roundtable | Build on each other as often as you push back; casual; no gushing filler |
| workshop | workshop, hotBench | One sharp question at a time; presenter answers without re-giving the talk |
| class | introClass, seminar | Teacher signposts and repeats key ideas on purpose; students ask real, sometimes unsure questions |
| quiz | quiz | Brisk host, one question at a time; short answers, guesses |
| written | criticalReply, replyPaper, firstDraft, compressor, summary, referencebuilder | Finished prose read aloud: signposting, no headings or lists, no conversational tics |

The lead role gets one more line on running the room: the moderator, host, presiding
judge, or teacher. The engine identifies it structurally: whoever opens the program
with a welcome, introduction, or announcement. All 16 templates are classified and
given a lead correctly; explainer, two friends, correctly has none.

Director's checks apply per form:
- echo openers: only where speakers answer each other
- stock concessions: debates only
- gushing filler: conversations, workshops, classes
- self-repetition: everywhere except classes, where repetition is teaching, and quizzes

`speaker.md` now holds only rules that apply to every form.

A short live run of explainer (Sonnet) produced a friendly two-person conversation:
- follow-up questions on the other's thread, an everyday analogy, casual asides
- no debate-style concessions or gushing filler
- a clean audit

### Waiting and stall recovery

The showrunner never polls. After it launches a speaker (in the background) or
messages one, it ends its response. The speaker's finish arrives as a notification,
and that starts the showrunner's next turn. A speaker between turns isn't running at
all. `SendMessage` resumes it, with its history intact, when its role is next up.

The things that don't produce an event are a speaker stuck on a permission prompt or a
hung API call. `engine.py watchdog` covers those:
- It runs as one background process per run. It reads `state.json` every few seconds
  and costs no tokens.
- It exits, waking the showrunner, only when the run ends or a turn's output file is
  overdue.
- "Overdue" scales with the run: the larger of 3 minutes and 4× its median write time.
  Prep notes get 2× the floor, and a role's first turn gets another 1.25×.

When a turn stalls, the showrunner escalates in steps:
1. **First stall:** it tells the user to check for a pending permission prompt, records
   the stall with `engine.py nudge`, sends the speaker a reminder, and restarts the
   watchdog.
2. **Second stall:** `engine.py recast` re-issues the turn to a fresh speaker. The new
   speaker gets the full sheet and a new output file, so a late answer from the old
   speaker is ignored. From then on, that role's delta sheets spell out its earlier
   prep notes, because the new agent didn't write them.
3. **A third stall** means something outside the run is wrong, so the showrunner stops
   and tells the user.

**Tested end to end.** This was a short debate (`--loop-scale 0.1`, Sonnet speakers,
13 steps, 2,234 words), run with event waiting only and the watchdog's floor cut to
45 s for the test. I injected a stall by never sending turn 5. The watchdog fired at
95 s (limit 94 s, scaled from the run's own write times). The second window fired
`recast`, and the replacement Author speaker wrote turn 5 in 25 s and carried the role
to the end. Its later sheets included the original speaker's prep notes in full, and
the original agent's file path was never used. The run took 707 s, including about 3
minutes lost to the injected stall.

The test also caught a false positive. With the shortened floor, Janson's first prep
step (1,750 words of notes, 92 s on Sonnet) was flagged at 70 s, which led to the
longer allowance for prep steps and first turns. At the default 3-minute floor it
wouldn't have fired. Per turn, the showrunner's round trip (wake, `advance`, send)
was 11–16 s, about the same as with polling. Waiting on events removes the polling
calls and the stale-draft race, but doesn't shorten each cycle.

### Why speakers don't hear every line live

It's tempting to broadcast each new line to every speaker as it's said, so that idle
cast members "listen". I tried the idea on paper and left it out:
- **Nothing to gain.** The delta sheet already gives the next speaker exactly the lines
  it missed, in order, just before it speaks. A speaker that had been hearing every
  line live would end up with the same lines in the same order, so its next turn would
  be no better informed.
- **More calls.** Each broadcast wakes an idle speaker for a full model call that
  re-reads its whole context, just to reply "noted". Each reply also wakes the
  showrunner, whose context is the largest in the run. With 3 roles that's roughly
  one extra speaker call and one extra showrunner wake-up per line; with 5 roles,
  three of each.
- **More risk.** Every broadcast is another chance for a cast member, especially on
  Sonnet or Haiku, to write lines or touch files out of turn.

The template's hidden prep steps are the right place for speakers to think ahead, and
they already run in parallel.

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
There were no audience questions. The hardware was an M5 Pro with 64 GB. The speakers
ran on Opus 5.5 (inherited from the session), except in the Sonnet column, where every
speaker was spawned with `model: sonnet`. The showrunner was Opus throughout. The
outputs are in `examples/test-run/`: the transcripts are committed, and the `.m4a` files
and `.enTalkenator` stay on disk only, via `.gitignore`.

| | Cast, persistent, Opus (default) | Cast, persistent, **Sonnet** | Cast, fresh agent each turn (Opus) | Single writer (Opus) |
| :- | :- | :- | :- | :- |
| Spoken turns + private prep | 15 + 2 | 15 + 2 | 11 + 2 | 13 + 2 |
| Spoken words / audio | 3,662 / 21.0 min | 2,837 / 16.3 min | 3,712 / 20.8 min | 3,438 / ~19.6 min (not rendered) |
| Average spoken turn | 244 words (157–414) | 189 words (145–290) | 337 words (244–478) | 264 words (176–360) |
| Speaker time, sum of all turns | 660 s | **326 s** | not measured | n/a |
| Typical continued turn (median) | 20.5 s | **15.3 s** | (no continuations) | n/a |
| Generation wall time | 9.5 min (568 s) | 9.9 min (593 s)¹ | 10.6 min (635 s) | **4.3 min** (255 s) |
| Speaker agents | 3 (cast once, then 14 `SendMessage` turns) | 3 (same pattern) | 13 | 1 |
| Turn-sheet words the speakers had to read | **10,802** (3 full sheets + 14 deltas) | 9,377 | 40,833 | one full sheet + short per-step instructions |

¹ Wall time isn't a fair comparison for the Sonnet run: the showrunner (me, on Opus) was
writing the settings menu between turns, so the speakers often sat finished while it
caught up. Use speaker time instead: Sonnet's speakers took half as long in total. That's
partly because four Opus turns made extra tool calls after writing (70–143 s each); on
ordinary turns Sonnet was about 25% faster.

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

**Sonnet speakers** produced a tight, disciplined debate that's clearly usable. Concessions
are clean ("Fair on the tenant… I overreached"), and lines carry across turns ("good
goal, wrong tool", the lawyer at the counter). The moderator turned the two sides'
positions into a concrete closing question. Compared with Opus it's leaner. Sonnet takes
"Be brief!" literally (189 words a turn against 244), so the same template ran about 23%
shorter. It also has fewer concrete details: no elected-judge angle, no mailing-versus-
receipt hypothetical, no Pascal. There's less humour and scene-setting, and more
"I'll take your three in order" structure. My read: Opus for showcase pieces and Sonnet
for everyday use. A per-role mix (`--models "Hillford Johns=sonnet;*=opus"`) keeps
the debaters sharp and saves on the moderator. I haven't tested Haiku, Fable, or past
versions.

**Fresh-agent cast** turns were good one at a time but long. Speakers that get a fresh
full sheet ignored "Be brief!" (average 337 words, max 478), so each loop finished after
one exchange. Each turn also starts cold.

**Single writer vs persistent cast (both Opus), judged blind.** Two independent judges
saw the transcripts anonymized as A and B, in opposite orders. Both rated the
persistent cast "clearly better" and said they'd rather listen to it. They agreed on
why:
- **Movement.** In the cast version the positions move under pressure: Dana rewrites
  her clause and accepts the forms fix, Janson concedes the syllabus, and Janson
  catches Dana shifting who holds the pen. In the single writer, Dana says "No
  concession tonight", so the arguments sharpen but barely move.
- **Voices.** The single writer's three speakers all use no contractions, and its two
  opponents share one rhetorical habit, "That is not X. It is Y.". Counted: Janson
  6, the Author 5, against 1 and 0 for the cast. Both judges said the voices blur,
  and that it will sound like prose read aloud.
- **Choreography rather than anticipation.** Neither judge found a true pre-emption,
  where a speaker answers a point before it's made, so the single writer's
  self-reported problem didn't show up as literal foreknowledge. It shows up as
  choreography: Janson explains a rule in three sentences and then says it can't be
  done in 300 words; the moderator "pounces" and Dana repeats him. Elsewhere there's a
  too-neat center/edge ladder and frictionless instant answers. Its moderator also
  announces agreement halfway through and leans Dana's way.
- **Where the single writer won.** It had the sharpest moderator question ("are you
  defending the courts' legitimacy, or their privacy?") and the best single exchange
  ("why are they safe enough for her?").
- **Where the cast fell short.** Some callbacks get over-tidy ("kitchen table" four
  times, Pascal three). Turns tend to open by quoting the other side's last line. And
  the moderator once makes Dana's rebuttal for her ("it cuts both ways"), which she
  repeats.

Judge scores, cast vs single: responsiveness 9/8 and 8/7; development 9/7 and 8/7;
distinct voices 8/6 and 7/6; listenability 8/7 and 8/6. One debate and two
Claude-family judges is a small sample, but the two agreed closely.

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
