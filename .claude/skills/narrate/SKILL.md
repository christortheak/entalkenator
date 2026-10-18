---
name: narrate
description: Turn a PDF (or several, or a Word, RTF, HTML or text file) into an audiobook-style narration of the document itself, read word for word. Running heads, page numbers and footnote numbers are removed, citations are handled, figures and tables are described, and the footnotes that matter are read in a second voice. Use when asked to narrate, read aloud, make an audiobook of, or "listen to" a paper, article, brief, opinion, report, or chapter, rather than turn it into a conversation.
argument-hint: <document> [more documents] [--pages 3-40] [--notes after-paragraph|section-end|none] [--citations drop|names|keep] [--engine ...] [--publish|--no-publish] [--tag "..."]
allowed-tools: Read Write Bash(python3 scripts/*)
---

# Narrate a document

The listener wants the document itself, as the author wrote it, minus what only works on a
printed page. Never summarize or rewrite it. Reader agents (`reader`) do the reading, a few pages
each. A script stitches their work, places the footnotes, and checks the result against the
PDF's own text.

Arguments: `$ARGUMENTS`

**No questions.** If the person said "no questions", "use your best judgment", or "I'm walking
away", or `PREFS show` has `questions=none`, ask nothing.
- Survey and write the brief yourself, from their preferences and your judgment.
- Use voices that are already ready, and publish if a library is set up, unless
  `publish=never`.
- Report at the end, including any choice you would have asked about, plus a push
  notification if you have that tool.

Scripts run from the enTalkenator folder, `${CLAUDE_PROJECT_DIR}`. If an earlier command
changed directory, `cd` back there first. Quote other paths; they may contain spaces.
- `SOURCES` = `python3 scripts/sources.py`
- `RENDER` = `python3 scripts/render_audio.py`
- `VOICE_OPTIONS` = `python3 scripts/voices.py`
- `PUBLISH` = `python3 scripts/publish.py`
- `PREFS` = `python3 scripts/prefs.py`: the person's preferences (CLAUDE.md says when to record
  them)

## 1. Plan the reading

Make a run directory: `programs/narration-<short name>-<YYYYmmdd-HHMMSS>`. Then run
`SOURCES plan <dir> --source <file> [--source <file>...] [--pages A-B]`. A source can be a
folder, or `file.pdf:15-24` for some pages. It copies each document and cuts each PDF into
reader tasks of up to 8 pages. Each task gets its own small PDF, because Claude Code reads a
small PDF whole with nothing installed.

## 2. Survey each document and write its brief

Every reader follows the same brief, so the document is narrated one way from start to
finish. Writing it is your job, and it's the step that matters most.

1. **Look at the document.**
   - `SOURCES map <dir>` sketches every page from the PDF's text layer: its size, opening
     and closing words, contents-page dots, and footnote-like lines.
   - Read the first task's small PDF (the `chunk` in the map) to see the layout and the
     opening.
   - If the map suggests something unusual, look at that part too: an appendix of tables,
     a long Methods section, columns, block quotes, or endnotes. Don't read the whole
     document; the readers will.
   - A document with `scanned: true` has no text layer. The readers can still read it from
     the page images, but the faithfulness check in step 4 can't run on it. Say so.
2. **Decide how it should sound**, for someone listening on a walk:
   - what it is
   - what to read and what to leave out (front and back matter, contents pages, reference
     lists, appendices)
   - inline citations
   - footnotes or endnotes: which kind to read, and where (after their paragraph, or at the
     section's end)
   - figures and tables
   - anything odd: columns, equations, foreign-language passages, long quotations

   `PREFS show` holds the person's usual choices (`narration.notes`, `narration.citations`,
   `narration.figures`, `chapters.narration`, and notes in their own words). The arguments
   (`--notes`, `--citations`, `--figures`) settle those choices for this run. Follow both
   unless the document gives a good reason not to, and then say why.
   - **Chapters:** podcast apps list one chapter per heading, down to the depth in
     `chapters.narration`:
     - `all-headings` is every level, the default
     - `main-parts` is the top two levels
     - `top-level` is the top level only

     If there's no preference and the outline is deep (say, over 30 headings), that's a
     fair question: "Chapters for every subsection (60), or the main parts only (9)?".

3. **Ask only about real choices.** Use AskUserQuestion, recommended option first, at most
   two or three questions. Ask when a choice is genuinely the person's and the document
   makes it matter. For example:
   - "The appendix is 12 payoff tables. Describe them briefly, or skip them?"
   - "About 300 footnotes, a tenth substantive. Read those after their paragraph?"
   - "Methods runs 9 pages. Read it, or stop at the Discussion?"

   Don't ask what the brief can settle sensibly by itself. When you ask, offer to remember
   the answer. If the person states a general preference ("never read footnotes"), record
   it: `PREFS set narration.notes=none`, or `PREFS note "..."`.
4. **Write the brief** to the `brief` path the plan printed (`sources/<doc>/brief.md`). Use
   plain sentences, under about 250 words, for readers who will see only their own pages.
   The same note placement goes to `SOURCES script` in step 5 (`--notes after-paragraph`,
   `section-end`, or `none`).
5. **Tell the person** what you decided, in two or three lines. Include how long the
   narration will be: roughly 1.5 minutes per page of body text, plus the notes and figure
   descriptions.

## 3. Read

For each task, spawn a reader with the Agent tool:
- `subagent_type: "reader"`
- `model` only if the person chose Sonnet
- `run_in_background: true`
- `description: "Reader · <doc> pages <a>-<b>"`
- prompt: `Task sheet: <sheet>`

Run up to 8 at a time. When one finishes, start the next. Wait for their notifications;
don't poll. Each replies with one line.
- A reader that reports an error, or whose output file is missing, gets one more try.
  `SOURCES status <dir>` lists what's missing.
- If a second try fails, stop and tell the person which pages couldn't be read.

## 4. Stitch and check

1. `SOURCES stitch <dir>`. It joins the pages, joins paragraphs split across pages, and
   matches footnotes to where they're cited. It reports:
   - `problems`: missing tasks (read them again), lines it couldn't parse (usually harmless;
     look at them), and substantive notes that were never cited (they're placed at their page)
   - per document: words, headings, figures, substantive and citation notes, and what the
     readers skipped (tables of contents, reference lists)
2. `SOURCES audit <dir>`. For each PDF, it compares the reading with the PDF's own text
   layer:
   - `faithfulness` is the share of the reading's words that match the author's text in order.
     Expect 0.97 or more; spoken forms ("section" for §) and rejoined words account for the
     rest.
   - Each `possiblyDropped` passage is author's text the reading doesn't have. Check each one:
     open that task's small PDF (the `chunk` in `sources/manifest.json`) and the reader's
     output.
     - If it's really body text, delete that task's output file, and spawn its reader again
       with this added to the prompt: "A check found this passage missing from an earlier
       reading; include it: <excerpt>". Then stitch and audit again.
     - If it's a caption, a table, a heading block, or something the reader rightly skipped,
       let it go.
   - Each `addedOrReworded` passage is words in the reading that aren't the author's. A few
     short ones are normal (spoken forms, a name kept from a citation). Long ones mean a
     reader paraphrased; re-read that task, as above.

## 5. Script and voices

`SOURCES script <dir> --notes <as in the brief> --figures <describe|skip>` writes `<dir>/narration.txt`:
- `[Narrator]:` turns for the text; each section starts with its heading, so the chapters
  are named after the sections
- `[Notes]:` turns for the footnotes ("Footnote 12. …") and the figure descriptions

Cast two clearly different voices from the chosen engine (`RENDER --list-voices --engine
<id>`):
- **Narrator:** a clear, warm, steady voice. For example, Kokoro `af_heart` or `bm_george`;
  for Qwen3-TTS, a description such as "A clear, warm audiobook narrator, a woman in her
  forties, unhurried and engaged".
- **Notes:** a contrasting one (another gender or accent), a little lighter.

For Gemini and gpt-4o-mini-tts, also pass `--styles`:
`"Narrator=steady, engaged audiobook narration;Notes=a lighter aside, like reading a footnote"`.

## 6. Render, package, publish

1. **Render**, in the background for anything over a few minutes of audio:
   `RENDER --transcript <dir>/narration.txt --out "<dir>/<Title>.m4a" --engine <id> --voices "Narrator=…;Notes=…" --gap 0.6 [--chapter-depth 1|2]`
   - `--chapter-depth` follows `chapters.narration`: 2 for `main-parts`, 1 for `top-level`,
     nothing for all headings. The chapters are embedded in the .m4a for Overcast and Apple
     Podcasts, and listed in the feed for other apps.
   - Paid engines: state the estimated cost first (`VOICE_OPTIONS list --transcript <dir>/narration.txt`).
   - A long document takes a while. Kokoro does three hours of audio in about 12 minutes on a
     recent Mac; Qwen3-TTS takes about four times as long.
2. **Package** for the enTalkenator app (one document at a time):
   `SOURCES package <dir> --audio "<dir>/<Title>.m4a"`. The bundle holds:
   - the PDF
   - the reading as the paper's text
   - the narration as the paper's own audio
3. **Publish** if a library is configured (`PUBLISH status`). Follow the entalkenate skill:
   `publish=always`, `--publish`, or no-questions mode publish without asking; otherwise
   ask.
   `PUBLISH push "<dir>" --audio "<dir>/<Title>.m4a" --transcript "<dir>/narration.txt" --title "<Title>" --author "<Author>" --template Narration [--tag ...]`
   Its chapters file sits next to the .m4a, so the web app follows along by section.

## 7. Report

Tell the person:
- the audio's length
- the faithfulness result, and anything you re-read or let go
- how many footnotes were read and how many figures described
- what the readers skipped (tables of contents, reference lists)
- the voices used, and the cost for a paid engine
- where the files are, and whether it was published

Offer to turn the same reading into a conversation: the entalkenate skill can use
`<dir>/sources/combined.md` without reading the document again.
