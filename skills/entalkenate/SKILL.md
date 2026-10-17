---
name: entalkenate
description: Turn a document (PDF, text, or pasted text) into an enTalkenator audio program, such as a debate, podcast, seminar, oral argument, or quiz show. It runs one of the enTalkenator app's templates inside Claude Code, voices each role with its own subagent, renders a multi-voice .m4a locally with Kokoro, and packages an .enTalkenator bundle the app can import. Use when asked to "entalkenate" something or to make a debate, podcast, or similar audio conversation from a paper.
argument-hint: <document.pdf|.txt> [template] [--single|--fresh] [--loop-scale 0.3] [--engine kokoro|gemini|say] [--voices "Role=voice;..."] [--user-input "..."] [--no-audio]
allowed-tools: Read Write Bash(python3 *)
---

# enTalkenate

You are the **showrunner**. You run an enTalkenator template step by step. A script
does the bookkeeping, and the performers write the lines. You don't write the
spoken lines yourself unless single-writer mode is on.

Arguments: `$ARGUMENTS`

Scripts (always quote the paths, which may contain spaces):

- `ENGINE` = `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/engine.py"`. This is the template
  state machine. Every subcommand prints JSON.
- `RENDER` = `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/render_audio.py"` turns the
  transcript into one .m4a.
- `SETUP` = `bash "${CLAUDE_PLUGIN_ROOT}/scripts/setup_tts.sh"` installs the local
  Kokoro voices, but only with the user's permission.

## 1. Set up the run

1. Read the arguments:
   - **Source**: a PDF, `.txt`, or `.md` path. If the user pasted text instead, save it to
     `<run dir>/pasted.txt` and use that.
   - **Template**: a file stem, id, or display name from the app's templates. If none is
     given, run `ENGINE templates` and ask the user to pick one.
   - **Flags**:
     - `--single`: single-writer mode. You write every turn.
     - `--fresh`: a new speaker agent every turn. This is the app's stateless behaviour, kept
       for comparison.
     - `--loop-scale X`: shortens or lengthens every loop. 0.3 turns the debate's
       10-minute loops into 3-minute loops.
     - `--engine` and `--voices`: passed to RENDER.
     - `--user-input`: fills `{{userInput}}`.
     - `--language`: output language.
     - `--no-audio`: stop after the transcript.
2. Choose a run directory in the current project: `entalkenator-runs/<template>-<YYYYmmdd-HHMMSS>`.
3. Run `ENGINE init --template <t> --source <path> --run-dir <dir> [--loop-scale X] [--user-input ...] [--language ...] [--mode single|fresh]`.
4. From `documentHead`, work out the document's title and author, as the app does.
   Use the title if one is evident, otherwise a concise title-case title. Use the author's
   name, or "Unknown Author". Save them with `ENGINE meta <dir> --title "..." --author "..."`.
5. If the template's `userInputConfig.isRequired` is true and no `--user-input` was given,
   ask the user for it (use its `label` and `description`) and save it with `meta --user-input`.
   An optional input stays empty unless the user supplied one.

## 2. Generate the transcript

Repeat until the JSON says `"done": true`:

```
ENGINE advance <dir> --batch [--cast "<role>=<agentId>" ...]
```

Each result lists the step(s) to write under `issue`. Each item has `role`, `turn`,
`visible`, `outputFile`, an `action`, and a `sheet` (the turn sheet path). It also lists
what was just `committed`, with a short `preview`, and shows `progress` (spoken minutes
so far, loop target).

**Cast mode (default): one persistent speaker per role.** Each role is played by one
`entalkenator:speaker` agent for the whole run. It is cast on its first turn and
continued on every later turn, so it keeps the document, its notes, and the
conversation in its own context:

- `action: "spawn"` means this role has no speaker yet. Call the Agent tool with
  `subagent_type: "entalkenator:speaker"`, `description: "<role> · turn <turn>"`, and
  this prompt:
  ```
  You are <role>, turn <turn>.
  Turn sheet: <sheet>
  Output file: <outputFile>
  ```
  The result gives the agent's ID. Pass it on your next `advance` as
  `--cast "<role>=<agentId>"` so the engine sends that role only deltas from then on.
- `action: "message"` means the role's speaker exists (`agent`). Send it the turn with
  SendMessage (`to: <agent>`; load the tool with ToolSearch first if it's deferred).
  Then wait for its one-line reply before calling `advance`. The reply arrives as a
  message, not as the tool result:
  ```
  Turn <turn> for <role>. Read your turn sheet, write your lines to the output file, and reply with one line.
  Turn sheet: <sheet>
  Output file: <outputFile>
  ```
  This sheet holds only what was said since that role last spoke, plus its new
  instruction. The role card, setting, document, and the role's own earlier notes
  aren't sent again.

When `issue` has several items (parallel private prep steps for different roles),
launch them all at once and wait for all of them. Don't read the turn sheets or
output files yourself. The speaker's one-line reply and the `preview` are enough to
follow the show. If a speaker reports a problem or its output file is missing, send
the same turn again. `advance` re-issues outstanding turns. If a speaker can't be
continued, spawn a new one with the full `<turn>.prompt.md` sheet for that turn (same
name, `.prompt.md` instead of `.delta.md`). Then register the new agent with `--cast`.

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
user which turns those were.

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
   role a distinct voice, and match it to how the transcript itself refers to that role:
   pronouns, or words like "a man" or "she". Never go by the name alone. If the
   transcript gives no cue, the default order is fine. Contrast neighbouring roles, for
   example by accent.
3. Run, with a 10-minute timeout (run long transcripts in the background):
   `RENDER --transcript <dir>/transcript.txt --out "<dir>/<Template displayName>.m4a" [--engine ...] --voices "Role=voice;Role=voice"`
   Clips are cached by text and voice, so a re-run after a failure, or with one role's
   voice changed, only renders what's missing.

## 4. Package for the app

`ENGINE package <dir> --audio "<dir>/<Template displayName>.m4a"` writes
`<dir>/<Title>.enTalkenator`. It's a zip laid out like the app's bundled examples:
`info.txt`, the source file, `<base>-transcript.txt`, and `<base>-<Template>.txt/.m4a`.
With `--no-audio`, run `package` without `--audio`.

## 5. Report

Tell the user:
- the bundle path, the .m4a path, and the audio duration
- which voice each role got
- the template and mode
- how long generation and rendering took
- anything that went wrong

To import, the user opens the `.enTalkenator` file with the enTalkenator app: double-click
it on macOS, or use AirDrop or the Files app on iOS.
