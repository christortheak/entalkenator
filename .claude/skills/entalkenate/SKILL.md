---
name: entalkenate
description: Turn a document or a set of documents into an enTalkenator audio program, such as a faculty workshop, a lecture or class, a debate, a podcast conversation, an oral argument, a quiz show, or whatever format the person describes. Claude composes the program from the request (format, cast, segments, length) and directs it turn by turn, each role voiced by its own speaker subagent, or runs one of the bundled templates as written. It renders a multi-voice .m4a on this Mac and can publish it to the person's own library and podcast feed. Use when asked to "entalkenate" something, or to make a workshop, class, debate, podcast, or similar audio program from a paper, article, or notes.
argument-hint: <document.pdf|.txt|folder> [what to make, in your own words] [--template T] [--model opus|fable|sonnet|haiku] [--single|--fresh] [--loop-scale 0.3] [--engine kokoro|gemini|say] [--voices "Role=voice;..."] [--user-input "..."] [--no-audio] [--publish|--no-publish] [--tag "..."]
allowed-tools: Read Write Bash(python3 scripts/*)
---

# enTalkenate

You are the **showrunner**. You compose the program the person asks for and direct it turn
by turn: who speaks, when, and what each moment calls for. A script does the bookkeeping, and
the performers write the lines. You don't write the spoken lines yourself unless
single-writer mode is on.

The bundled templates come from the enTalkenator app, which needed fixed steps because no
model was in charge of the show. You are, so treat them as starting points. A program fits
the request, follows where the conversation goes, and changes its plan when it should. Run a
template exactly as written only when the person asks for that template, or passes
`--template`.

Arguments: `$ARGUMENTS`

Off a Mac (`uname -s` isn't `Darwin`, as in Cowork), make nothing: follow "Only in Claude
Code, on their Mac" in CLAUDE.md.

**No questions.** If the person said "no questions", "use your best judgment", or "I'm walking
away", or `PREFS show` has `questions=none`, ask nothing from start to finish. Skip:
- the settings menu
- the plan and character reviews
- voice auditions
- the library question

Use their preferences, then the recommended options, then your own judgment. Publish if a
library is set up, unless `publish=never`. This mode is how someone drops in a PDF, says
"make a workshop hot bench from this", and finds it in their podcast app later, so make it
work. Never stop to ask, never install anything, and finish with a short report (and a
push notification, if you have that tool).

Scripts run from the enTalkenator folder, `${CLAUDE_PROJECT_DIR}`. If an earlier command
changed directory, `cd` back there first. Quote other paths; they may contain spaces.

- `ENGINE` = `python3 scripts/engine.py`. This is the template state machine. Every
  subcommand prints JSON.
- `RENDER` = `python3 scripts/render_audio.py` turns the transcript into one .m4a.
- `PUBLISH` = `python3 scripts/publish.py` uploads the result to the user's own library,
  if they have one (section 5).
- `SOURCES` = `python3 scripts/sources.py` prepares the documents (step 3).
- `PREFS` = `python3 scripts/prefs.py` holds the person's preferences. Read it at the start
  (`PREFS show`); CLAUDE.md says when to record new ones.
- `VOICE_OPTIONS` = `python3 scripts/voices.py` lists the voice options, their costs and
  readiness, and the person's default (section 3).

## 1. Set up the run

1. Read the arguments:
   - **Sources**: one document or a whole set: PDFs, Word, RTF, HTML, text, or Markdown
     files, or a folder of them. Pasted text goes into `<run dir>/pasted.txt`. A web page you
     fetch goes into a `.md` file in the run directory. A set is discussed as a set: a
     seminar on three articles, a debate between two briefs, a podcast on a week of
     reading. A finished narration's `sources/combined.md` can be reused as is.
   - **What to make**: the format in their own words, with any length, proportions,
     audience, or style: "an hour-long faculty workshop, 20-minute talk, the rest
     questions"; "an intro lecture for college students, half lecture, half back and
     forth"; "a Socratic law school class on this article". If they name only a format
     ("a debate"), give it that format's usual shape. If they don't say what to make, ask
     in one short question, offering three or four formats that suit the material; with no
     questions, choose one yourself. `--template T` (a stem, id, or display name from
     `ENGINE templates`) runs that template as written instead.
   - **Flags**:
     - `--single`: single-writer mode. You write every turn.
     - `--fresh`: a new speaker agent every turn. This is the app's stateless behaviour, kept
       for comparison.
     - `--loop-scale X`: shortens or lengthens every loop. 0.3 turns the debate's
       10-minute loops into 3-minute loops.
     - `--model M` / `--models "Role=M;*=M"`: speaker model(s). M is fable, opus,
       sonnet, haiku, session, or a full model ID.
     - `--engine`, `--voices`, `--styles`: passed to RENDER (section 3). `--engine` takes
       kokoro, qwen3, say, gemini, openai, elevenlabs, or an option id such as
       `openai:tts-1`.
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
     - "Use the best (Recommended)": your session's model for every speaker, their chosen
       voices cast automatically, full template length, cast mode, and their preferences
     - "Review the plan first": the defaults, plus the plan review in step 4 (for a
       template run, the character review in step 7). For a one-role piece, word it
       "Review the author's voice first".
     - "Customize everything": the questions below, then the plan or character review
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
     template without loops is unaffected. For a program, scale its segments' minutes
     instead. "Other" takes a custom scale. Skip this question when they gave a length.
   - **Voices** (header "Voices"): run `VOICE_OPTIONS list` first (and `VOICE_OPTIONS
     recorded list`: if they have a recorded voice, include "Use my recorded voice for
     <a role>"). Offer up to three
     ready options, their default first, marked "(Recommended)". Label each with its cost
     ("Kokoro: free, on this Mac", "Gemini Flash: about $0.40 per 30 min"). Add "Show me all
     the voice options", which lists every option with its trade-offs, including ones
     that need an install or a key, and sets up whichever they pick (the setup skill's
     voices step says how). Section 3 then asks whether they'd like to audition the cast.
   - **Writers** (header "Writers"): "One agent per role (Recommended)", "Single writer
     (fastest; one agent writes every role)", "Fresh agent every turn (the app's way;
     most tokens)". These map to modes cast, single, and fresh.
   - **Turns** (header "Turns"), only for a template run whose `floorSteps` (in `ENGINE
     templates`) is above 0: "Natural: whoever would speak up next (Recommended)" and "As
     the template has it (a random pick)". These map to init `--floor natural` and
     `--floor template`. Their `turns` preference answers it; without one, use natural.

   The menu choices override the matching flags.

   **Past versions.** For each full model ID, run `ENGINE pin-agent --model <id>`.
   This writes `.claude/agents/entalkenator-speaker-<id>.md` in the project.
   - If it reports `restartNeeded`, Claude Code won't see the new agent this session.
     Tell the user, and offer the nearest alias (opus, sonnet, or haiku) for this run.
     The file stays for next time.
   - The user's organization may restrict models. If a spawn on a pinned model fails,
     say so and fall back to an alias.
3. Choose a run directory in this folder: `programs/<format>-<YYYYmmdd-HHMMSS>` (such as `programs/workshop-20261018-1430`). Then
   **prepare the documents**. Every speaker gets their text once, in their first sheet
   (later sheets carry only what's new). Speakers discuss a document rather than read it
   aloud, so they don't need a clean reading: the PDF's own text does, running heads and
   all.
   - Run `SOURCES plan <dir> --text-layer --source <file> [--source ...]`. Each PDF's text
     comes from its text layer, marked `[PDF page N]`, and the PDF is cut into parts of up
     to 10 pages that speakers can open to check a figure, a table, or exact wording.
   - Glance at each document's `opening` in the result. If one looks garbled (stray
     symbols, words run together, columns interleaved), check with `SOURCES map <dir>`.
     If it is, `SOURCES read <dir> <doc>` sends that document to readers instead.
   - **Readers only where needed.** A document without a usable text layer (`scanned`: a
     scan or an image-only PDF), or one sent with `read`, gets reader tasks. For each,
     survey it and write its brief as in step 2 of the narrate skill, but aimed at speakers
     who will discuss it:
     - keep case and author names, so they can cite authorities
     - keep substantive footnotes, and describe figures and tables
     - leave out what no discussion needs, such as contents pages and reference lists

     Then run its reader tasks as in step 3 of the narrate skill.
   - Then `SOURCES stitch <dir>` (and `SOURCES audit <dir>` if any readers ran).
   - A finished narration's `sources/combined.md` (same document) can be used directly.
   - Text, Markdown, Word, RTF and HTML files need no reader. `plan` and `stitch` convert
     them along with the PDFs.
   - With several documents, `combined.md` labels each one ("What follows is the text
     entitled …") as the app's Topics do. A document taken from its text layer is labelled
     by its file name until you name it: `SOURCES meta <dir> d2 --title "..." --author "..."`
     from its opening, then stitch again.
   - **Full text or summaries.** Every speaker holds the whole of `combined.md`, so what a
     run costs grows with its size times the number of speakers. Up to about 100,000 words
     (roughly 200 pages), give them the full text without asking. Above that, ask once
     (header "Sources"): "Summaries of each document (Recommended)", "The full text", and,
     if one or two documents are plainly the focus, "Full text of <them>, summaries of the
     rest". With no questions, use summaries, keeping the full text of any document the
     request centres on. The person can ask for summaries at any size.
     - Run `SOURCES summarize <dir> [d2 d3 ...]` (every document if none are named). Spawn
       one `summarizer` agent per task, all at once with `run_in_background: true`, with the
       prompt `Summary task: <sheet>`. A summary made before for the same file comes back
       under `reused`, with no task.
     - When they've finished, run `SOURCES stitch <dir> --summaries all` (or `d2,d3`). The
       speakers still get every original's parts, so they can check the full text.
4. **Compose the program** (skip for a template run). Write `<dir>/program.json`:
   ```json
   {
     "title": "Faculty Workshop",
     "form": "workshop",
     "setting": "Where this is, who is there and why, and that everyone has read the material:\n\n{{transcript}}\n\n-----",
     "lead": "Gene Alford",
     "roles": [
       {"name": "Gene Alford", "description": "You are ... (second person: who, what you know, where you stand, temperament, pronouns)"}
     ],
     "segments": [
       {"title": "The talk", "minutes": 20, "plan": "Janson presents the paper in three parts: ..."},
       {"title": "Questions", "minutes": 40, "plan": "Colleagues press on ...; Gene keeps it moving and closes."}
     ]
   }
   ```
   - **Start from the closest template.** `ENGINE templates` lists them. Read the closest
     one's JSON in `templates/` for its setting, its roles, and its beats: private
     preparation, the opening, the back-and-forth, the close. Keep what fits the request
     and change what it changes. The templates' recurring characters can return.
   - **Roles**: usually two to six speakers. Write each description in the second person,
     as the templates do: who they are, what they know, where they stand on the material,
     their temperament, and the pronouns the transcript should use. Give positions an edge
     where the format wants one; a workshop with no skeptic is dull.
   - **Segments**: the program's phases in order, with target minutes that add up to the
     requested length. With no length given, use what the format usually runs: about 30
     minutes for a podcast, 45 for a debate, 50 for a class, 60 for a workshop. Each `plan`
     says what happens, who leads it, and what it should achieve. Segments are the natural
     chapter breaks.
   - **Time**: the engine counts 175 spoken words a minute (people speak 160–200), so a
     20-minute talk is about 3,500 words.
   - `form` is the closest of debate, hearing, conversation, workshop, class, quiz, or
     written; it sets the speakers' performance notes and the director's checks. `lead` is
     whoever runs things: the moderator, host, presiding judge, or teacher.
   - With "Review the plan first", show the plan before going on: the format, each segment
     with its minutes, and each character in a line. Take their changes into the file.

   Then run `ENGINE init (--program <dir>/program.json | --template <t> [--floor natural|template]) --source <dir>/sources/combined.md --original <dir>/sources/<doc>/original.pdf [--original ...] --run-dir <dir> [--model <m>] [--models "Role=m;*=m"] [--loop-scale X] [--user-input ...] [--language ...] [--mode single|fresh] [--form F]`.
   `--original` lists the copies of the documents inside the run directory (not the
   person's own files elsewhere, which could need a permission prompt mid-run). The speakers'
   sheets list each one with its parts by page, so a speaker can open the part that holds a
   figure. A single original PDF goes into the app bundle with the text as its transcript.
   Its `models` field echoes each role's speaker model ("session" means inherit). Its
   `form` is the engine's guess at what kind of program this is: debate, hearing,
   conversation, workshop, class, quiz, or written (a piece read aloud as narration).
   The form decides the speakers' performance notes and which director's checks apply.
   Mention it in a few words. If the user says it's wrong, or the template plainly
   isn't that (a custom template, say), change it with `ENGINE meta <dir> --form <form>`.
5. From `documentHead` (or the stitch report's titles), work out the title and author, as the
   app does. For a set, the title names the topic ("Two Briefs in St. Mary v. Roy"), and the
   author is the authors' names or "Various authors".
   Use the title if one is evident, otherwise a concise title-case title. Use the author's
   name, or "Unknown Author". Save them with `ENGINE meta <dir> --title "..." --author "..."`.
6. For a template run: if the template's `userInputConfig.isRequired` is true and no `--user-input` was given,
   ask the user for it (use its `label` and `description`) and save it with `meta --user-input`.
   An optional input stays empty unless the user supplied one.
7. **Character review** (a template run, when the user chose to review characters or
   customize; a program's characters were settled in step 4, though `ENGINE role --sketch`
   still adds colour before a character's first turn). Run
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

### Directing a program

You decide every turn. Issue one with:

```
ENGINE advance <dir> [--cast "<role>=<agentId>" ...] --speaker "<name>" [--segment N] [--last] --say - <<'EOF'
<the instruction for this turn>
EOF
```

Its `issue` item goes to the speaker as described under cast mode below (spawn or
message). When the speaker finishes, run `ENGINE advance <dir> [--cast ...]` with no
speaker. It commits the turn and returns:
- `committed`, with the **full text** of what was just said
- `direct`: each role's turns and words so far, how many turns since they last spoke, and
  whether the last speaker named them
- `progress.program`: the current segment's minutes against its target, and the total

Read what was said, decide, and direct the next turn. That's two `advance` calls a turn.

**Writing the instruction.** Tell the speaker the moment and the job, not the words:
- where things stand: "The talk is over and questions have begun", or "Abney has just
  challenged your reading of the second experiment"
- what's wanted: "answer her directly, concede what's fair, then press your main point",
  or "call on someone who hasn't spoken"
- how long: "two or three sentences", or "about 900 words"

Leave their opinions and phrasing to them; their character and the material supply those.
Don't put words in their mouths, and don't tell them who's right.

**Shape and pace.**
- A long stretch by one speaker, such as a talk or a lecture, goes in turns of 600–1,000
  words (four to six minutes), each picking up where the last stopped. Have them outline it
  privately first (`--private outline`: "Outline your 20-minute talk in three parts"), and
  refer to it in later instructions as `{{outline}}`.
- Preparation nobody hears can run in parallel: `--speaker A --speaker B --private
  questions` gives each the same instruction at once. Each speaker remembers their own
  notes.
- Give the floor to whoever would naturally come in next: someone just addressed or
  challenged, someone whose expertise the point touches, the lead when things drift or
  time is up, or someone who has been quiet. Let people speak to each other by name. Let a
  good exchange run two or three rounds, and end one that circles. Avoid both a fixed
  rotation and ping-pong between the same two people.
- Vary the rhythm: short interjections ("one or two sentences") between longer answers,
  and an aside or a joke where these people would make one.
- **How it should sound.** On a spoken turn, `--delivery "wry, conceding the point"` adds a
  few words for the voices: mood, pace, intensity. `--cuts-in` marks a turn that interrupts
  the last one; in the audio it starts a moment early, overlapping slightly. The speaker
  never sees these. Voices that take direction follow the note; the pauses between turns
  follow the conversation in every engine. Leave the note off when the moment is plain.
- Mark each segment's first turn with `--segment N`. Move on when its minutes are up or it
  has done its job, through the lead and within the story ("We have time for one more
  question"). Land the whole program within about 10% of its target length.

**Changing the plan.** Follow the conversation where it's good. When the plan should
change, edit `program.json` and run `ENGINE program <dir> --file <dir>/program.json`. A
segment might run long because it's the heart of the thing, an argument might need a new
voice, or a class might need another example. You can add roles and change or add segments;
characters who have spoken keep their cards. Tell the speakers what changed in their next
instruction.

**Ending.** The lead closes the program. Mark that turn `--last`, then run `finalize`.

In single-writer mode you direct the same way and write each turn yourself (see below).

### Running a template, and the speakers in both

For a template run, repeat until the JSON says `"done": true`:

```
ENGINE advance <dir> --batch [--cast "<role>=<agentId>" ...]
```

Each result lists the step(s) to write under `issue`. Each item has `role`, `turn`,
`visible`, `outputFile`, an `action`, and a `sheet` (the turn sheet path). It also lists
what was just `committed`, with a short `preview`, and shows `progress` (spoken minutes
so far, loop target). If it reports `problems` instead, some turns are still open; their
details are under `pending`.

With `--floor natural`, where the template lets several people take a turn, `advance`
returns `choose` instead of `issue`. It lists the candidates, how much and how recently
each has spoken, whether the last speaker addressed them, and the last speaker's closing
words (`lastWords`). Give the floor to whoever would most naturally come in, as when
directing a program, then run `ENGINE advance <dir> --speaker "<name>"`.

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
- Don't read the turn sheets or output files yourself. In a template run, the speaker's
  reply and the `preview` are enough to follow the show; in a program, `committed` already
  carries the text you direct from.
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

**Chapters.** Podcast apps list chapters for skipping around, so choose them the way a
podcast producer would. Read `<dir>/transcript.txt` once, and mark where the program moves
on:
- the opening
- each main question or topic
- a change of phase, such as opening statements, rebuttal, audience questions, or a new
  speaker's argument
- the closing

In a program, `finalize` lists where each segment began; those are chapters, with topic
chapters inside the long ones. That's usually one chapter every 2–5 minutes, and never one per turn. Follow their
preferences: `chapters.conversation=few` means just the big parts, and notes may say more. Titles are 2–7 words
and say what's discussed ("Is silence consent?"). Name a speaker only for a set piece ("Ms.
Norton's argument"). Then run
`ENGINE chapters <dir> --plan "1=Opening statements;6=Is silence consent?;14=Closing"`.
The numbers are spoken turns in `transcript.txt`, counting from 1. It echoes each choice
with its turn's opening words, so check they land where you meant. In section 3, pass
`--chapter-plan <dir>/chapters-plan.json` to RENDER.

## 3. Render audio (skip with `--no-audio`)

1. **Pick the voices.** In order: what the user named for this run, then their menu choice,
   then their saved default (`VOICE_OPTIONS default`, used when `--engine auto` and it's
   ready), then Kokoro, then the macOS voices. `RENDER` never chooses a paid engine by
   itself.
   - If nothing is installed and no choice was ever made, say so in a line and offer the
     options (the setup skill's voices step), or go ahead with the macOS voices.
   - **Paid engines:** run `VOICE_OPTIONS list --transcript <dir>/transcript.txt` and say
     the estimate in one line, e.g. "This will cost about $0.29 on your Google account".
     If they didn't pick this engine for this run and the estimate is over $1, ask before
     rendering.
   - **Heavier local engines (qwen3, qwen3-preset, breeze):** the same listing gives `renderMinutes` from this
     Mac's own earlier renders, or a rough guess from Kokoro's speed here. Say it in a line
     ("Breeze will take about 40 minutes on this Mac"). When it's over about an hour, or
     longer than the person seems to expect, offer the choice once: go ahead, a lighter
     voice, or a cloud voice with its cost. With no questions, go ahead with their choice
     and say how long it took in the report. With no figure yet, say this is the first
     render with that voice here and the time will be known after it.
     For breeze the listing can also give `fastRenderMinutes`, for Breeze's fast mode
     (`--breeze-fast`). It takes about a quarter of the time, but in a blind test it sounded
     slightly less natural. When a Breeze render would be long, include it in the choice, e.g.
     "about 70 minutes, or about 20 in the fast mode, which sounds a little less natural". With no
     questions, keep the default unless they've said speed matters more.
2. **Cast the voices**, unless the user gave `--voices`. Never cast a real person's voice
   or ask for one ("sounds like" a named person), except a recorded voice its owner made
   (see below).
   - **What you can offer** (once, briefly, in "Customize everything" or when they care
     about the voices; only what this Mac has):
     - a different engine per role (`--engines`)
     - a voice they recorded (`VOICE_OPTIONS recorded list`)
     - voices designed from descriptions
     - delivery notes per turn
     Pick the one that answers what they asked: "make it sound like me" is a recorded
     voice; "the judge should sound older" is a description; "livelier" is lines in
     character and delivery notes; "it's taking too long" is a lighter engine for the
     minor roles.
   - **Mixing engines:** `--engines "Role=breeze;Role=qwen3-preset;*=kokoro"` gives each role
     its own engine ("*" is everyone else, default `--engine`), and each role's `--voices`
     value is in its engine's terms: a description for qwen3 or breeze, `ryan` for the
     presets, a Kokoro voice. Local engines run one after another, so the time is each
     engine's share added up; heavy voices for the leads and Kokoro for minor roles keeps a
     Breeze program quick. Breeze and Qwen3 share an audio codec and sit together naturally;
     Kokoro or a cloud voice next to them can sound like a different room, which suits a
     caller or a guest on the line.
   - **Recorded voices:** `--voices "Role=@Name"` speaks in a voice kept with
     `VOICE_OPTIONS recorded add` (the setup skill's "Recorded voices" says how to make
     one). It needs qwen3 or breeze for that role, so pair it with `--engines "Role=breeze"`
     (or qwen3). A natural use: the person themselves as host or presenter.
   - **Engines with preset voices** (kokoro, say, gemini, openai, elevenlabs): run
     `RENDER --list-voices --engine <option id>`. It lists each voice with how it sounds;
     for ElevenLabs these are the voices on the person's account. Give each role a distinct
     voice. Match it to the pronouns in the character sketch if there is one, and otherwise
     to how the transcript itself refers to that role (pronouns, or words like "a man" or
     "she"). Never go by the name alone. If the transcript gives no cue, the default order
     is fine. Contrast neighbouring roles, for example by accent.
   - **Qwen3-TTS and Breeze** (qwen3, breeze): voices are descriptions. Write one or two
     sentences per role
     from its sketch or description and the transcript's cues:
     - gender and age, accent, voice quality, temperament, and pace
     - for example, "A calm, measured British man in his sixties with a deep voice;
       deliberate and dry"
     - don't use `;` or `=`, which separate the roles
     The same description gives the same voice in every program, because designed voices
     are kept, so a recurring character keeps their voice.

     Also write each role a line in character, one to three sentences that person would
     say in their own manner, and pass them as `--lines "Role=line;Role=line"` (no `;` or
     `=` inside). Each designed voice reads its line once, and every turn copies how that
     line was delivered. A line in character gives a livelier voice than the default, which
     is neutral and careful. For a recurring character, reuse the same description and line.
   - **Qwen3 preset voices** (qwen3-preset): only `ryan` and `aiden` are native English
     speakers, both men. Never give a role one of the other presets just to fill the cast:
     they speak English with a Chinese, Japanese or Korean accent, which can sound like a
     caricature. Use one only when the person asks for it by name. For a woman's voice, or
     more than two speakers, choose another engine. Each line follows the role's `--styles`
     note and the turn's delivery note. A render that has to share a voice says so under
     `warnings`.
   - **Kokoro**: give roles a slightly different pace with `--speeds "Role=0.94;Role=1.06"`
     (a measured professor, a quick student), and blend two voices for more variety than
     the eight presets, e.g. `af_bella,am_puck`.
   - **Delivery notes** (gemini and gpt-4o-mini-tts follow them; the others ignore them):
     write a short note per role from its sketch and the form, under 12 words, such as
     "dry, unhurried, faintly amused". Pass them as `--styles "Role=note;Role=note"`.

   **Audition** if the user asked to choose voices, or picked Customize and says yes to
   "Hear the cast first?". Shortlist three or four candidates per role (the
   transcript-matched ones first). Then run:
   `RENDER --audition --transcript <dir>/transcript.txt --out <dir> --engine <id> --candidates "Role=v1,v2,v3;Role=..."`
   - For qwen3, separate the candidate descriptions with `|`, not commas.
   - It writes `audition-<role>.m4a` per role: the candidates read that role's first line, in
     order, about a second apart. That's roughly 30 seconds of audio per role (paid engines
     charge for it, which is cents).
   - Let the user hear them: with SendUserFile if you have it, otherwise with `open <file>`
     on macOS, or by giving the paths.
   - Then ask with AskUserQuestion, one question per role (up to four per call). Make each
     option a candidate labelled with its number, voice, and how it sounds, for example
     "2: am_fenrir (American male)". Use the picks as `--voices`.
3. **Run**, in the background for anything longer than a few minutes:
   `RENDER --transcript <dir>/transcript.txt --out "<dir>/<Title>.m4a" --engine <id> --voices "Role=voice;Role=voice" --chapter-plan <dir>/chapters-plan.json [--styles "..."]`
   - How long it takes depends on the Mac (see step 1); cloud engines take a few minutes,
     four turns at a time. A described voice is designed once, on first use, and kept.
   - **Breeze** follows the role's `--styles` note and each turn's delivery note, and
     performs `(laughs)`, `(sighs)`, `(coughs)` and `(clears throat)` where the speakers wrote
     them. A note on every turn nearly doubles its work, so keep them for turns that need one.
     `--breeze-fast` voices several turns at once: much quicker, slightly less natural.
   - The pauses between turns follow the conversation: a quick reply comes fast, a question
     gets a beat, and a turn that cuts in overlaps. `--gap N` makes them all the same.
   - Clips are cached by text, voice, model, and delivery note, so a re-run after a failure,
     or with one role's voice changed, only renders what's missing.
   - Next to the .m4a it writes `<Title>.chapters.json`: your chapters, plus
     every turn's timing, which the library uses to follow the transcript. The chapters are
     also embedded in the .m4a, because Overcast and Apple Podcasts read only those.
   - **`checkTurns`** lists turns spoken much faster or slower than that speaker's usual
     pace. Much faster usually means the voice dropped words, a known Qwen3 quirk; much
     slower, a stall or a repeat. Render those turns again once, with the same command plus
     `--redo 5,23`: a local voice gives a new take, and only those turns are redone. If a
     turn is still listed, tell the person which turn and roughly where it falls, so they
     can listen to it.

## 4. Package for the enTalkenator app

This bundle is for people who also use the enTalkenator iPhone or Mac app; it's cheap,
so always make it.

`ENGINE package <dir> --audio "<dir>/<Title>.m4a"` writes
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
- With `--no-publish`, or `publish=never`, skip. With `--publish`, `publish=always`, or in
  no-questions mode, publish without asking (adding their `tags` preference, if any).
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
- which voices were used (engine and model) and which voice each role got, plus the cost
  for a paid engine
- the format (the program's segments with their minutes, or the template) and the mode
- how long generation and rendering took
- whether it was published to their library, with its `appUrl`
- anything that went wrong

To hear it on this Mac right away, `open` the .m4a. If they use the enTalkenator app, the
`.enTalkenator` file imports there: double-click it on macOS, or use AirDrop or the Files
app on iOS.
