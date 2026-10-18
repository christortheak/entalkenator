---
name: entalkenate
description: Turn a document (PDF, text, or pasted text) into an enTalkenator audio program, such as a debate, podcast conversation, seminar, oral argument, or quiz show. It runs one of the templates in templates/, voices each role with its own speaker subagent, renders a multi-voice .m4a on this Mac (Kokoro or the macOS voices), and can publish it to the person's own library and podcast feed. Use when asked to "entalkenate" something, or to make a debate, podcast, or similar audio program from a paper, article, or notes.
argument-hint: <document.pdf|.txt> [template] [--model opus|fable|sonnet|haiku] [--single|--fresh] [--loop-scale 0.3] [--engine kokoro|gemini|say] [--voices "Role=voice;..."] [--user-input "..."] [--no-audio] [--publish|--no-publish] [--tag "..."]
allowed-tools: Read Write Bash(python3 scripts/*) Bash(bash scripts/*)
---

# enTalkenate

You are the **showrunner**. You run an enTalkenator template step by step. A script
does the bookkeeping, and the performers write the lines. You don't write the
spoken lines yourself unless single-writer mode is on.

Arguments: `$ARGUMENTS`

Scripts run from the enTalkenator folder, `${CLAUDE_PROJECT_DIR}`. If an earlier command
changed directory, `cd` back there first. Quote other paths; they may contain spaces.

- `ENGINE` = `python3 scripts/engine.py`. This is the template state machine. Every
  subcommand prints JSON.
- `RENDER` = `python3 scripts/render_audio.py` turns the transcript into one .m4a.
- `SETUP` = `bash scripts/setup_tts.sh` installs the local Kokoro voices, but only with
  the user's permission.
- `PUBLISH` = `python3 scripts/publish.py` uploads the result to the user's own library,
  if they have one (section 5).

## 1. Set up the run

1. Read the arguments:
   - **Source**: a PDF, `.txt`, or `.md` path. If the user pasted text instead, save it to
     `<run dir>/pasted.txt` and use that.
   - **Template**: a file stem, id, or display name from `templates/`. If none is
     given, run `ENGINE templates` and ask the user to pick one, describing each in a few
     plain words (its format, roles, and rough length) rather than listing file names.
   - **Flags**:
     - `--single`: single-writer mode. You write every turn.
     - `--fresh`: a new speaker agent every turn. This is the app's stateless behaviour, kept
       for comparison.
     - `--loop-scale X`: shortens or lengthens every loop. 0.3 turns the debate's
       10-minute loops into 3-minute loops.
     - `--model M` / `--models "Role=M;*=M"`: speaker model(s). M is fable, opus,
       sonnet, haiku, session, or a full model ID.
     - `--engine` and `--voices`: passed to RENDER.
     - `--user-input`: fills `{{userInput}}`.
     - `--language`: output language.
     - `--no-audio`: stop after the transcript.
     - `--no-director`: leave director's notes out of the turn sheets (see section 2).
     - `--publish` / `--no-publish` / `--tag T`: see section 5.
     - `--form F`: override the guessed form (debate, hearing, conversation, workshop,
       class, quiz, written).
2. **Settings menu.** Skip any question whose answer the arguments already give. If
   they settle everything, or the user said to just go, skip the menu entirely. Otherwise
   ask with AskUserQuestion (load it with ToolSearch if it's deferred), in one call:
   - **Settings** (header "Settings"):
     - "Use the best (Recommended)": your session's model for every speaker, voices cast
       automatically, full template length, cast mode
     - "Review the characters first": the defaults, plus the character review in step 7.
       For a one-role template (a written piece), word it "Review the author's voice
       first".
     - "Customize everything": the questions below, then the character review
   If they choose Customize everything, ask these together in one more call:
   - **Speaker model** (header "Model"): "Opus", "Fable", "Sonnet", and "Haiku". Put
     "(Recommended)" on whichever matches the session's model.
     - Opus is the default model on Pro and Max, and strong at this.
     - Fable is Anthropic's top tier, but on some plans it bills to usage credits
       instead of included limits.
     - Sonnet is faster and lighter on limits.
     - Haiku is fastest and cheapest, and best kept for minor roles.
     - "Other" covers two cases: a past version as a full model ID (e.g.
       `claude-opus-4-8`), or a per-role mix such as `Hillford Johns=haiku; *=opus`.
   - **Length** (header "Length"): "Full (Recommended)", "Short, about 30%", "Medium,
     about 60%". These map to `--loop-scale` 1, 0.3, and 0.6. Loops set the length, so a
     template without loops is unaffected. "Other" takes a custom scale.
   - **Voices** (header "Voices"): "Cast them for me (Recommended)" or "Let me audition
     and choose". The second means section 3, step 2, plays samples and asks.
   - **Writers** (header "Writers"): "One agent per role (Recommended)", "Single writer
     (fastest; one agent writes every role)", "Fresh agent every turn (the app's way;
     most tokens)". These map to modes cast, single, and fresh.

   The menu choices override the matching flags.

   **Past versions.** For each full model ID, run `ENGINE pin-agent --model <id>`.
   This writes `.claude/agents/entalkenator-speaker-<id>.md` in the project.
   - If it reports `restartNeeded`, Claude Code won't see the new agent this session.
     Tell the user, and offer the nearest alias (opus, sonnet, or haiku) for this run.
     The file stays for next time.
   - The user's organization may restrict models. If a spawn on a pinned model fails,
     say so and fall back to an alias.
3. Choose a run directory in this folder: `programs/<template>-<YYYYmmdd-HHMMSS>`.
4. Run `ENGINE init --template <t> --source <path> --run-dir <dir> [--model <m>] [--models "Role=m;*=m"] [--loop-scale X] [--user-input ...] [--language ...] [--mode single|fresh] [--form F]`.
   Its `models` field echoes each role's speaker model ("session" means inherit). Its
   `form` is the engine's guess at what kind of program this is: debate, hearing,
   conversation, workshop, class, quiz, or written (a piece read aloud as narration).
   The form decides the speakers' performance notes and which director's checks apply.
   Mention it in a few words. If the user says it's wrong, or the template plainly
   isn't that (a custom template, say), change it with `ENGINE meta <dir> --form <form>`.
5. From `documentHead`, work out the document's title and author, as the app does.
   Use the title if one is evident, otherwise a concise title-case title. Use the author's
   name, or "Unknown Author". Save them with `ENGINE meta <dir> --title "..." --author "..."`.
6. If the template's `userInputConfig.isRequired` is true and no `--user-input` was given,
   ask the user for it (use its `label` and `description`) and save it with `meta --user-input`.
   An optional input stays empty unless the user supplied one.
7. **Character review** (when the user chose to review characters or customize). Run
   `ENGINE roles <dir>`. Show each character's name and description in a short list,
   then ask with AskUserQuestion (header "Characters"):
   - "Use them as written (Recommended)"
   - "Flesh them out for me": draft a sketch for each role, 60–100 words, grounded in
     its description, the document, and the form. Cover how they talk (rhythm, a
     verbal habit), their background and temperament, what they want from this
     session, what they're touchy about, and the pronouns the transcript should use.
     Shape it to the form:
     - debate or roundtable: each participant's stake and style of argument
     - hearing: each judge's temperament and leanings; each attorney's courtroom manner
     - class: each student's year, confidence, and interests, with a range from eager
       to lost
     - quiz: contestants' personalities and how competitive they are
     - written piece: the author's discipline, tone, and stance; the role is a voice,
       not a character
     Keep every stance the template gives them; don't change who wins an argument. Show the sketches and
     ask "Use these sketches", "Edit them" (free text through Other), or "Back to the
     originals". Save each one with `ENGINE role <dir> --role "<name>" --sketch "..."`.
   - "Let me edit them": take the user's changes in their own words. Rewrites of who
     someone is or what they believe go in `--description` (it replaces the template's
     description everywhere the app would use it). Added colour goes in `--sketch`.
     Confirm the result with `ENGINE roles <dir>`.
   Characters can only change before their first turn. `--reset` restores the
   template's version. The sketch's pronouns and description also guide voice casting
   in section 3.

## 2. Generate the transcript

Repeat until the JSON says `"done": true`:

```
ENGINE advance <dir> --batch [--cast "<role>=<agentId>" ...]
```

Each result lists the step(s) to write under `issue`. Each item has `role`, `turn`,
`visible`, `outputFile`, an `action`, and a `sheet` (the turn sheet path). It also lists
what was just `committed`, with a short `preview`, and shows `progress` (spoken minutes
so far, loop target). If it reports `problems` instead, some turns are still open; their
details are under `pending`.

**Cast mode (default): one persistent speaker per role.** Each role is played by one
`speaker` agent for the whole run. It is cast on its first turn and
continued on every later turn, so it keeps the document, its notes, and the
conversation in its own context:

- `action: "spawn"` means this role has no speaker yet. Call the Agent tool with:
  - `subagent_type: <agentType>`. This is `speaker`, or a pinned agent for
    a past model version.
  - `model: <model>`, only when the item has one.
  - `run_in_background: true`.
  - `description: "<role> · turn <turn>"`.
  - This prompt:
  ```
  You are <role>, turn <turn>.
  Turn sheet: <sheet>
  Output file: <outputFile>
  ```
  The launch result gives the agent's ID. Pass it on your next `advance` as
  `--cast "<role>=<agentId>"` so the engine sends that role only deltas from then on.
- `action: "message"` means the role's speaker exists (`agent`). Send it the turn with
  SendMessage (`to: <agent>`; load the tool with ToolSearch first if it's deferred):
  ```
  Turn <turn> for <role>. Read your turn sheet, write your lines to the output file, and reply with one line.
  Turn sheet: <sheet>
  Output file: <outputFile>
  ```
  This sheet holds only what was said since that role last spoke, plus its new
  instruction. The role card, setting, document, and the role's own earlier notes
  aren't sent again.

When `issue` has several items (parallel private prep steps for different roles),
launch them all at once.

**Performance notes and director's notes.** Every speaker's first sheet carries
performance notes for the run's form: how people in a debate, a hearing, a podcast, a
workshop, a class, a quiz, or a written piece actually sound. The lead also gets a line
on running things.

Unless the run used `--no-director`, the engine also checks the transcript before each
turn. If that speaker has picked up a habit, it adds a short private note to the end of
their sheet. Which checks apply depends on the form:
- debates, hearings, conversations, and workshops: echoing the previous speaker
- debates: stock concessions
- conversations, workshops, and classes: gushing filler ("great question")
- everything except quizzes: the "not X, it's Y" habit
- everything except classes, where repetition is teaching, and quizzes: repeating one's
  own line
- all forms: running long where the template asks for brevity

The lead (moderator, host, presiding judge, teacher) also gets the running time. You
don't need to do anything with the notes; speakers act on them silently.

**Waiting: by event, not polling.** After launching or messaging speakers, end your
response. Don't sleep, poll, or check for the output file. Each speaker's finish
reaches you as a notification with its one-line reply, and that starts your next turn.
Then run `advance`.
- If you launched several speakers, `advance` commits the finished ones and reports
  the rest as still open. End your response again until the others finish.
- Don't read the turn sheets or output files yourself. The speaker's reply and the
  `preview` are enough to follow the show.
- A speaker that reports a problem, or whose output file is missing, gets the same
  turn again. `advance` re-issues open turns.

**Watchdog.** Right after the first `advance`, start `ENGINE watchdog <dir>` with Bash
`run_in_background: true`, and keep one running for the whole run (a second copy exits
at once with `already-running`). It costs no tokens. It exits, which wakes you, only
when the run is `done` or a turn `stall`s, meaning its output file hasn't appeared
within the larger of 3 minutes and 4× this run's median write time. Private prep steps
get twice the floor, and a role's first turn (a cold start) gets another quarter. When
it reports a stall:

1. **First stall (`next: "nudge"`).** The usual cause is a speaker waiting on a
   permission prompt (for example, to write its file), or a slow or rate-limited model.
   Tell the user which role is stuck and ask them to check for a pending prompt. Run
   `ENGINE nudge <dir> --turn <turn>`, send its `message` to the speaker with
   SendMessage, and restart the watchdog.
2. **Second stall (`next: "recast"`).** Stop the old agent with TaskStop if you have
   it. Run `ENGINE recast <dir> --turn <turn>`. It re-issues the turn as a `spawn`, with
   the full sheet and a fresh output file, so a late answer from the old speaker is
   ignored. Spawn the new speaker as above, register it with `--cast` on the next
   `advance`, and restart the watchdog. The new speaker gets everything it needs from
   the full sheet; later sheets spell out the role's earlier notes instead of
   pointing back to them.

If a recast speaker stalls too, stop and tell the user. Something outside the run is
wrong, such as limits, permissions, or the model being unavailable.

**Fresh mode (`--fresh`, init `--mode fresh`)** spawns a new speaker on every turn,
always with `action: "spawn"` and the full sheet. Don't pass `--cast`. That's the app's
own behaviour, but it re-sends the document and whole transcript every
turn, at several times the tokens.

**Single-writer mode (`--single`, init `--mode single`).** You write every turn yourself.
Before the first turn, Read the first item's `promptFile` in full. It holds the
production instructions and the whole document. After that, each issued item carries
an `instruction` (the role description plus this step's prompt, with your own earlier
notes referenced rather than repeated). Write the turn in that role's voice with the
Write tool to `outputFile`, then call `advance`. Don't re-read the turn sheets. You
already hold the document and every earlier turn in context. Follow the same rules as a
cast speaker: only the spoken words, no labels, no stage directions, no markdown, and
respect length cues like "Be brief!". Keep each role's private notes out of the other
roles' mouths: a debater shouldn't anticipate the other side's prepared points.

Stop when `done` is true, or if the engine reports an error. With `--max-turns` hit, a
loop never reached its target length, so tell the user.

Then run `ENGINE finalize <dir>`. It writes `<dir>/transcript.txt` in the app's
`[Name]: text` format and reports spoken minutes and generation time. If it lists
`changedAfterCommit`, a speaker rewrote its file after its turn was committed. Tell the
user which turns those were. Its `viewAudit` re-reads every sheet the speakers were
given and checks that each one saw the whole conversation before it spoke, with no line
dropped, duplicated, or out of order. If it isn't `ok`, report the turns it lists.

## 3. Render audio (skip with `--no-audio`)

1. Pick the engine:
   - If the user named one, use it.
   - Otherwise check `SETUP --check`. Exit 0 means Kokoro is installed, so use it.
   - If Kokoro is missing, ask the user before installing anything. Kokoro needs about
     660 MB of downloads and about 1.3 GB on disk, in a private venv at
     `~/.cache/entalkenator` plus the Hugging Face cache. The downloads are mlx-audio
     (MIT), misaki and Kokoro-82M (Apache-2.0), torch, and espeak-ng (GPL-3.0). Offer
     two alternatives: `--engine say` (built-in macOS voices, no download) and, if
     `GEMINI_API_KEY` is set, `--engine gemini` (gemini-3.8-flash-tts, paid, about
     $0.41 per 30 minutes through Dec 2026). If the user agrees to install, run
     `SETUP` in the background and wait for it to finish.
2. Cast the voices, unless the user gave `--voices`. Run `RENDER --list-voices [--engine ...]`
   to see the voices and how each one sounds (accent, male or female). Then give each
   role a distinct voice. Match it to the pronouns in the character sketch if there is
   one, and otherwise to how the transcript itself refers to that role (pronouns, or
   words like "a man" or "she"). Never go by the name alone. If the
   transcript gives no cue, the default order is fine. Contrast neighbouring roles, for
   example by accent.

   **If the user chose to audition voices**, shortlist three or four candidates per role
   (the transcript-matched ones first). Then run:
   `RENDER --audition --transcript <dir>/transcript.txt --out <dir> --candidates "Role=v1,v2,v3;Role=..."`
   This writes `audition-<role>.m4a` per role: the candidates read that role's first
   line, in order, about a second apart. It takes roughly 30 seconds of audio per role.
   Let the user hear them:
   - with SendUserFile if you have it
   - otherwise with `open <file>` on macOS
   - or by giving the paths
   Then ask with AskUserQuestion, one question per role (up to four per call). Make each
   option a candidate labelled with its number, voice, and how it sounds, for example
   "2: am_fenrir (American male)". Use the picks as `--voices`.
3. Run, with a 10-minute timeout (run long transcripts in the background):
   `RENDER --transcript <dir>/transcript.txt --out "<dir>/<Template displayName>.m4a" [--engine ...] --voices "Role=voice;Role=voice"`
   Clips are cached by text and voice, so a re-run after a failure, or with one role's
   voice changed, only renders what's missing. Next to the .m4a it writes
   `<Template displayName>.chapters.json`: one chapter per turn, with its start time and
   speaker. The library uses it to follow the transcript, and podcast apps show it as
   chapters.

## 4. Package for the enTalkenator app

This bundle is for people who also use the enTalkenator iPhone or Mac app; it's cheap,
so always make it.

`ENGINE package <dir> --audio "<dir>/<Template displayName>.m4a"` writes
`<dir>/<Title>.enTalkenator`. It's a zip laid out like the app's bundled examples:
`info.txt`, the source file, `<base>-transcript.txt`, and `<base>-<Template>.txt/.m4a`.
With `--no-audio`, run `package` without `--audio`.

## 5. Publish to your library (only if one is configured)

The user may have their own enTalkenator library: a Cloudflare Worker with a web player and
a private podcast feed, set up with the setup-library skill. Check, without network:

`PUBLISH status`

- If `configured` is false, skip publishing. At the end, mention in one line that the
  setup-library skill can put programs on their phone and in their podcast app, unless
  they've already said no to a library this session.
- Skip it with `--no-audio` too. The library needs audio.
- With `--no-publish`, skip. With `--publish`, publish without asking.
- Otherwise ask with AskUserQuestion (header "Library"):
  - "Publish to my library (Recommended)": it goes into their private podcast feeds
  - "Upload as a draft": it's in the web app but not the feeds
  - "Not this time"

  Tags are optional. Each tag also gets its own feed. Take them from `--tag`, or from the
  user's words through "Other" (e.g. "publish, tagged Supreme Court").

Then run:

`PUBLISH push "<dir>" [--tag "..."]... [--draft]`

- It uploads the .m4a, `transcript.txt`, the chapters file, and the bundle, then adds the
  program to the feeds. Pushing the same run again updates the same item.
- It prints the program's `appUrl` in the web app. That address is safe to show.
- Never print feed addresses: they contain the feed's secret token. The user finds them on the
  web app's Podcast feeds page.
- If it fails, show the error. The run's files are untouched, and the same command can be
  re-run later.
- If it reports `warnings` (chapters that don't match the transcript), pass them on.

## 6. Report

Tell the user:
- the bundle path, the .m4a path, and the audio duration
- which voice each role got
- the template and mode
- how long generation and rendering took
- whether it was published to their library, with its `appUrl`
- anything that went wrong

To hear it on this Mac right away, `open` the .m4a. If they use the enTalkenator app, the
`.enTalkenator` file imports there: double-click it on macOS, or use AirDrop or the Files
app on iOS.
