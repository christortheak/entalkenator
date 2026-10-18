# How enTalkenator works

enTalkenator runs the enTalkenator app's templates inside Claude Code. Claude is the
showrunner: a script keeps the template's state, each role is played by its own speaker
subagent, a local model renders the audio, and the program can be published to your own
library on Cloudflare. This began as a Claude Code plugin inside the app's repository
(the test runs below date from then); this folder is the standalone version. To get
started, see the [README](../README.md).

## What's here

```
enTalkenator/
├── CLAUDE.md                 what Claude needs to know to guide someone through it
├── README.md                 for people: what it is, what it needs, how to start
├── VERSION
├── .claude/
│   ├── settings.json         session-start status hook; permissions for the scripts and programs/
│   ├── skills/
│   │   ├── setup/            guided first-time setup: voices, library, a first program
│   │   ├── entalkenate/      the showrunner: settings, generation loop, render, package, publish
│   │   └── setup-library/    the library on Cloudflare, from no account to podcast app
│   └── agents/speaker.md     one persistent agent per role (Read + Write tools only)
├── scripts/
│   ├── engine.py             template state machine (stdlib Python 3.9+)
│   ├── render_audio.py       transcript -> one .m4a + per-turn chapters, a voice per role (kokoro | gemini | say)
│   ├── library.py            sets up, updates and removes the library through Wrangler (stdlib)
│   ├── publish.py            uploads a program to the library and manages its items (stdlib)
│   ├── status.py             what's set up (local checks); session_start.sh runs it as a hook
│   ├── setup_tts.sh          installs the Kokoro engine into a private venv (asks first)
│   └── requirements-kokoro.{in,txt}   pinned dependency set for that venv
├── cloud/                    the library Worker (TypeScript, R2 + D1); see cloud/README.md
├── templates/                snapshot of the app's enTalkenator/Templates/*.json
├── tests/                    checks that need no account (see developing.md)
├── tools/make_release.sh     builds the download zip
└── examples/
    ├── plain-language-opinions.txt   ~500-word sample used for the test runs
    └── test-run/                     transcripts from the test runs
```

## Running it

Requirements: Claude Code, Python 3.9+ (the macOS system Python is fine), and macOS for
PDF text extraction (PDFKit via `osascript`) and `afconvert`. The Kokoro voices need an
Apple Silicon Mac. `ffmpeg` replaces `afconvert` elsewhere.

Open this folder in Claude Code and ask in your own words, or use the skill directly:

```
/entalkenate path/to/paper.pdf debate
/entalkenate notes.txt podcast --single
/entalkenate brief.pdf oralargument --loop-scale 0.5 --engine say
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
  `.claude/agents/entalkenator-speaker-<id>.md` in this folder. Claude Code picks it up
  within seconds, since `.claude/agents/` already exists.
- Fable may bill to usage credits rather than plan limits on some plans, and an
  organization's `availableModels` list can block any of these.

Programs go to `programs/<template>-<timestamp>/`, which holds the turn sheets, every
turn's text, `transcript.txt`, the `.m4a`, its chapters, and the bundle.

**Permissions.** `.claude/settings.json` allows writes under `programs/` and the
project's own scripts (`python3 scripts/engine.py ...` and so on), so a run doesn't prompt
on every turn. Deleting a program and removing the library still ask.

**Voices.** The person chooses among free local voices and cloud voices with their own key ([voices.md](voices.md)); `scripts/voices.py` keeps the catalogue and their default. On first render the skill checks for the Kokoro engine and, if it's missing,
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

### Directed programs: no fixed steps

The app needed templates because nothing was in charge of the show: every call was a fresh
model following a fixed step. Here the showrunner is, so a program is composed from the
request and directed as it goes. `init --program program.json` takes:
- the setting, which carries `{{transcript}}`
- the roles, as second-person descriptions
- the lead
- the form, which picks the performance notes and director's checks
- segments, each with a title, target minutes, and a plan

The engine turns that into a template with no steps yet. Each turn the showrunner directs
(`advance --speaker NAME --say -`) is appended as a step: its instruction is the step
prompt, and `--private KEY` makes it a hidden step with an `outputKey`. So everything a
template run has carries over unchanged: full and delta sheets, director's notes, the view
audit, `finalize`, and the bundle.

- **Time** is counted per segment at the app's 175 words a minute; `progress.program`
  reports the current segment's minutes against its target.
- **Directing from what was said.** `advance` returns each committed turn's full text, plus
  how much and how recently each role has spoken and whether the last speaker named them.
- **Revising.** `program RUN --file` changes segments and adds roles mid-run. A role that has
  spoken keeps its card, since its speaker already holds it.
- **Ending.** The run ends only after the turn marked `--last`.

Template runs get part of this: with `--floor natural`, a step that lists several roles
("Hillford Johns, Janson Skullbottom") returns `choose`, and the showrunner gives the floor
to whoever would naturally come in. The candidates are widened to every role listed on a
visible step with the same prompt, the template's interchangeable seats, and speakers are
told they may address one another by name.

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

## Reading documents: narrations and sets

The original enTalkenator app's first job was narration, so you could listen to papers on the
go. Its "AI Clean-Up" sent each page to a model with instructions to remove headers, footers
and footnotes, describe figures, and keep the body text exactly. Here, Claude does that
reading itself, and the footnotes are judged rather than deleted. Conversations skip all this and use the PDF's own
text (below).

- **Readers.** `sources.py plan` splits each PDF into tasks of up to 8 pages. It cuts each
  task's pages into a small PDF of their own with PDFKit. Claude Code's Read tool reads a small
  PDF whole, text and page images, with nothing installed. Reading selected pages of a long
  PDF needs poppler's `pdftoppm`, which most Macs don't have. Without the cut, two test
  readers loaded the whole 67-page brief, about 206,000 tokens each for 8 pages of work; with
  it, about 76,000.
- **The survey and the brief.** Before any reader starts, the showrunner surveys each
  document:
  - `sources.py map` sketches every page from the text layer: its size, opening and closing
    words, contents-page dots, and footnote-like lines
  - it looks at the first pages, and wherever the map suggests something unusual

  It then writes `sources/<doc>/brief.md`: what the document is, what to read and skip, and
  how to handle citations, notes, figures, columns, numbers and units. It asks the person
  only about real choices. Every reader follows the same brief, so a document split eleven
  ways is still narrated one way.

  This replaced a regex "kind detector" and fixed rule sets. The detector misfiled both a
  Supreme Court brief and a Nature article as "other", and no rule set anticipates an
  appendix of payoff tables. The reader instructions shrank from about 1,300 words to 700:
  the goal, the word-for-word rule, and the output format the stitcher needs.
- **What a reader writes** (`.claude/agents/reader.md`): the body text word for word, in a
  small line format. Page furniture is gone, citations follow the chosen rule, and § becomes
  "section". Headings are marked by level. Figures and tables get a description to be read
  aloud. Every footnote is sorted: `@note 14 citation` (not copied) or
  `@note 15 substantive | text`. `@continues` and `@incomplete` mark paragraphs cut by a task
  boundary, and `@skipped` records tables of contents and reference lists that were left out.
- **Stitching** joins the tasks, rejoins split paragraphs (and words hyphenated across the
  cut), and matches each note to the paragraph that cites it. `reading.md` carries
  substantive notes and figure descriptions inline. `combined.md` labels each document in a
  set the way the app's Topics do ("What follows is the text entitled …"), and it becomes the
  `{{transcript}}` for any template.
- **The faithfulness check** (`sources.py audit`) aligns each task's reading with the PDF's own
  text layer for those pages, word by word (difflib, after normalizing case, punctuation, and
  spoken forms). It reports the share of the reading that matches the author's text in order.
  It also lists stretches of 12 or more of the author's words that are missing, and of 6 or
  more read words that aren't the author's. Missing footnote text, citations, and the title
  page are recognized and not flagged. A scan has no text layer, so it can't be checked.
- **Chapters follow structure, not turns.**
  - A narration gets a chapter per heading, down to the depth the person prefers
    (`chapters.narration`), with no footnotes, figures or "continued" entries. A heading
    followed straight away by a subheading becomes one chapter.
  - A conversation gets the chapters the showrunner chose after reading the finished
    transcript (`engine.py chapters`): the opening, each main question or phase, and the
    closing.
  - Every turn's timing is kept separately (`entalkenator.turnTimes`), and the library uses
    it to make the transcript follow the audio.
- **Chapters in two places.** Pocket Casts and the library read the feed's Podcasting 2.0
  chapters file. Overcast and Apple Podcasts read only chapters embedded in the audio file.
  So `render_audio.py` also writes a QuickTime chapter track into every .m4a, using
  `scripts/mp4chapters.py`: standard library only, ffmpeg's layout, verified by reading the
  file back with AVFoundation. The feed adds `?v=<upload time>` to its media links, so a
  replaced file gets a new address and podcast apps fetch it again.
- **Narration script** (`sources.py script`): `[Narrator]` turns open with the section
  heading, so chapters are named after sections. `[Notes]` turns carry substantive footnotes,
  after their paragraph or at the section's end, and figure descriptions. The two voices make
  it clear what is the author's main text.
- **Conversations use the PDF's own text.** Speakers discuss a document rather than read it
  aloud, so they don't need a clean reading. `sources.py plan --text-layer` takes each PDF's
  text layer (PDFKit) as it is, marked `[PDF page N]`, and cuts the PDF into parts of up to
  10 pages. Readers run only for a scan (too few words in the text layer), or for a document
  whose text layer the showrunner finds garbled (`sources.py read RUN d2`). Two test
  articles, 98 pages, were ready in 3 seconds with no reader tasks; read by Claude, the same
  pages took a dozen readers and several minutes.
- **Summaries for large sets.** Each speaker holds the whole of `combined.md`, so a set's
  cost is its size times the number of speakers. Above about 100,000 words the showrunner
  offers summaries. `sources.py summarize` sets up one `summarizer` agent per document, and
  `stitch --summaries` puts the summaries in place of those documents' text. Summaries are
  kept under `~/.cache/entalkenator/summaries`, keyed by the file's contents, and reused.
  They're written for speakers rather than listeners: the claim, the structure, the
  evidence, passages worth quoting with their page marks, and what a careful reader would
  push on.
- **Sets for conversations.** Persistent speakers get `combined.md` once, in their first
  sheet, and only what's new after that. The sheet lists each original by its file name, with
  its parts by page, so a speaker can open the part that holds a figure, a table, or the exact
  wording. (Reading selected pages of a long PDF would need poppler.)

Test (St. Mary v. Roy, petitioners' brief, pages 1–24, three readers):
- Each reader took 32–121 seconds.
- The tables of contents and authorities were skipped and the case caption became the
  spoken opening.
- Faithfulness was 0.987. The only flags were two citation-only sentences a reader had
  reduced to "reported in the Federal Reporter and reproduced in the petition appendix",
  which it had also reported.
- Kokoro voiced the 18.6 minutes in 36 seconds, with chapters by section and both
  substantive footnotes in the second voice.

## Your own library on Cloudflare

To listen on a phone or in a podcast app, each program can be published to a private
library that **you** host. It's one Cloudflare Worker on your own account, on the free
plan, built in [`cloud/`](../cloud/README.md).
- **Feeds.** It serves a private RSS feed for Apple Podcasts, Overcast, or Pocket Casts, plus
  one feed per tag. Feeds include chapters and synced transcripts.
- **Web app.** `/app` is a web library: search, a player with speed and resume, a transcript
  that highlights who's speaking, and bundle downloads. Phones can install it as an app.

The **setup-library** skill walks someone through it, starting from no Cloudflare account,
and needs no GitHub. It drives `scripts/library.py`:
- **Only Wrangler touches the account.** `library.py` runs Cloudflare's own CLI (installed
  with permission into `~/.cache/entalkenator/wrangler`, pinned to one version) and parses
  its JSON and error codes. It never reads Wrangler's login token. An early version called
  Cloudflare's API directly with that token; Claude Code's safety check rightly blocked it
  as credential handling, and a new user's session would have hit the same wall.
- **Steps stop for the person.** `preflight` and `deploy` return `needs` (log in, choose an
  account, switch on R2, pick a workers.dev subdomain, a name that's taken) with the
  dashboard link to open. The skill explains and re-runs.
- **Idempotent.** `deploy` creates or reuses the R2 bucket and D1 database, writes a
  per-person Wrangler config from `cloud/wrangler.jsonc` (into `~/.cache/entalkenator/deploy/`
  with absolute paths, since a new download replaces this folder), deploys, applies the
  migration, and sets `UPLOAD_TOKEN` and `FEED_TOKEN` only if they're missing. Running it
  again is how a newer enTalkenator updates the library; the feed address doesn't change.
- **The passphrase never passes through Claude.** `library.py passphrase` asks for it in a
  macOS dialog (`osascript`, hidden answer, typed twice) and hands it to `wrangler secret
  bulk` on stdin.
- **Settings** go to `~/.config/entalkenator/library.json` (mode 600), which `publish.py`
  reads.

After that, `/entalkenate` offers to publish at the end of each run. To publish an
earlier run:

```bash
python3 scripts/publish.py push programs/<run> --tag "Supreme Court"
```

- **`render_audio.py`** also writes `<name>.chapters.json`, one Podcasting 2.0 chapter per
  turn. It has the start time, end time, and speaker, computed from the clip lengths it
  already has. The library uses it to follow the transcript, and podcast apps show it as
  chapters.
- **`publish.py`** (stdlib only) has `push`, `info`, `check`, `list`, `edit`, `delete`,
  `status`, and `configure`. `push` streams each file in its own request and uses an id
  derived from the run, so re-pushing updates the same item.

Privacy: the feeds opt out of every directory (`itunes:block`, `podcast:block`,
`podcast:locked`), and every response is `noindex` and `no-referrer`. Feed addresses carry a
long secret token, and the source document (inside bundles) is only offered in the signed-in
web app. These are personal copies of other people's work: don't share feed addresses or
submit them anywhere. [`cloud/README.md`](../cloud/README.md) covers limits, privacy, the
tests, and the live deploy check.

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
Python, as fixed on main in `9ccccaa`, and lists the library entries the app would show.

## Voice engines and licences

The full set of options (Kokoro, Qwen3-TTS, macOS voices, Gemini, OpenAI, ElevenLabs), how they compare, and how each is wired are in [voices.md](voices.md). The notes below are from the first three engines' test runs.

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

### Oral argument on a pending Supreme Court case (Sonnet)

- **Input:** both merits briefs in *St. Mary Catholic Parish v. Roy*, No. 25-581, set for
  argument Nov. 3, 2026. They were downloaded from supremecourt.gov and extracted with
  PDFKit: 31,069 words.
- **Template:** `oralargument`, as written, with `--loop-scale 0.4`, so each side got
  about 4 minutes of questions. All five speakers ran on Sonnet, with the director on.
- **Result:** 21 spoken turns plus 2 private prep notes, 2,710 words. Audio: 17.1 minutes,
  rendered in 37 s. Generation took 13.5 minutes.
- **Audit:** clean, with no stale drafts.
- **Long first sheets:** each speaker's first sheet carried both briefs, about 42k tokens,
  and every speaker paged through it to the end marker.

Observations:
- **It sounds like a real bench.**
  - Judges ask 70–100-word questions that build on each other: Patterson presses for a
    limiting principle, and Maralla extends Patterson's co-religionist hypothetical.
  - Counsel answer first and directly ("No, Your Honor.", "I accept that a mistaken guess
    alone would not be enough") and concede where they have to.
  - Holland, the originalist, presses both sides on how far *Carson* reaches.
- **Facts are grounded.**
  - Every name used (Rassbach, Norton, Odean, Cooke) and every specific claim checked
    against the briefs: the page cites, forty faith-based providers, six Catholic Charities
    preschools, the January 14, 2023 instruction, and no complaints on record.
  - The one loose paraphrase: "approved seventeen requests, and none departed from it",
    where the brief has 17 requests for adjustments under one provision.
- **The template stages a three-judge federal appeals panel, not the Supreme Court.**
  The speakers mostly adapted ("this court could not [overrule Smith] if you did"), but
  there are seams: the presiding judge says the case was "affirmed by the Tenth Circuit",
  and the respondents' lawyer cites "this Court's" church-autonomy cases. A Supreme Court
  variant of the template (nine justices, petitioner/respondent) would remove that.
- **The respondents' opening ran 672 words**, against 374 for the petitioners. The
  template puts no brevity cue on openings, so no director's note applied.
- **App bug, real-world case (since fixed).** Case names contain periods ("St.", "v.").
  The bundle imported fine, because its file names are UUID-based. But once imported, the
  app made the display name the item's file prefix, and `importFromEnTalkenator` matched
  files by `components(separatedBy: ".")[0]`, so exporting the item and importing it again
  would fail. Fixed on main in `9ccccaa`.

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
  `LibraryItem.scanGeneratedContent` named content after the last `-`. So
  "Plain-Language" showed a bogus "Language Summary" entry, as did the bundled
  `default2.enTalkenator` ("Last-Mile …"). The app is fixed on main in `9ccccaa`, but
  builds released before that still have the bug. Until the fix ships, `package` swaps
  ASCII hyphens in the display name for U+2011, which looks identical. `--keep-hyphens`
  turns that off.
- **Not Cowork.** Opened in Cowork, Claude found the setup skill but stopped. Cowork runs
  commands in a Linux VM, so Kokoro (MLX), PDFKit via `osascript`, `say`, `afconvert` and
  the Keychain are out of reach, and anything installed there never reaches the Mac.
  Anthropic's docs say a plugin's local MCP server runs on the Mac even in Cowork, so a
  plugin with a Mac helper should work. But uploading one shows a warning that it can
  access everything on the Mac, which is too much to ask of the people this is for. That
  route wasn't tested live; the test plugin is in commit `ded4b75`. So enTalkenator runs in
  Claude Code only. Off a Mac, `status.py` reports `"mac": false` (with `"where":
  "cowork"` for a Cowork mount), the session hook says to stop, and setup points the
  person to the Code tab.

### App issues noticed

Fixed on main in `9ccccaa`:
- `scanGeneratedContent` treated any `-` in the display name as a content-type separator
  (see above).
- `importFromEnTalkenator` found the source by `components(separatedBy: ".")[0] ==
  baseFileName`, so an item whose title contained a dot couldn't be imported again after
  export.

Still open:
- A blank user input leaves the literal `{{userInput}}` in the prompt
  (`Template.generateTranscript` only sets the variable when non-empty).
- `RawStep.loopDuration` is decoded but never used. The loop minutes come from `loop: N`.
- Loop word counts use `split(separator: " ")`, so words across line breaks aren't
  counted and loops run a little long.
