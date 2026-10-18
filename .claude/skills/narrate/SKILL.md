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

Scripts run from the enTalkenator folder, `${CLAUDE_PROJECT_DIR}`. If an earlier command
changed directory, `cd` back there first. Quote other paths; they may contain spaces.
- `SOURCES` = `python3 scripts/sources.py`
- `RENDER` = `python3 scripts/render_audio.py`
- `VOICE_OPTIONS` = `python3 scripts/voices.py`
- `PUBLISH` = `python3 scripts/publish.py`

## 1. Settings

Run `SOURCES prefs`. If it has saved choices and the arguments don't override them, say what
they are in one line ("Footnotes after their paragraph, citations dropped, your usual voices")
and go on; the person can say "change" to see the menu. Otherwise ask in one AskUserQuestion
call:
- **Footnotes** (header "Footnotes"):
  - "Read the important ones after their paragraph (Recommended)": substantive notes, in a
    second voice, right after the paragraph that cites them. Citation-only notes are never
    read.
  - "Read them at the end of each section"
  - "Skip footnotes"
- **Citations** (header "Citations"):
  - "Drop them (Recommended)": reporter citations, string cites, and page pins go; a case or
    author name stays when the sentence needs it
  - "Keep names only"
  - "Keep them all": for checking the text, hard listening
- **Voices** (header "Voices"): from `VOICE_OPTIONS list`, as in the entalkenate skill. Say that
  notes and figure descriptions get a second voice.
- **Readers** (header "Readers"), only for documents over 40 pages:
  - "Same model as this session (Recommended)"
  - "Sonnet (lighter on usage)"

Save the footnote and citation choices with
`SOURCES prefs --notes <after-paragraph|section-end|none> --citations <drop|names|keep>`.
Figures are always described unless the person says otherwise (`--figures skip`).

## 2. Plan the reading

Make a run directory: `programs/narration-<short name>-<YYYYmmdd-HHMMSS>`. Then run:

`SOURCES plan <dir> --source <file> [--source <file>...] [--pages A-B] --citations <choice>`

It copies each document and cuts each PDF into reader tasks of up to 8 pages, each with
its own small PDF, so nothing needs installing. In a line, tell the person:
- how many pages and reader tasks
- roughly how long the audio will be: about 1.5 minutes per page of body text for a typical
  article, before footnotes

If a document is `scanned` (no text layer), the readers still read it from the page images,
but the faithfulness check in step 4 can't run on it. Say so.

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

`SOURCES script <dir> --notes <choice> --figures describe` writes `<dir>/narration.txt`:
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
   `RENDER --transcript <dir>/narration.txt --out "<dir>/<Title>.m4a" --engine <id> --voices "Narrator=…;Notes=…" --gap 0.6`
   - Paid engines: state the estimated cost first (`VOICE_OPTIONS list --transcript <dir>/narration.txt`).
   - A long document takes a while. Kokoro does three hours of audio in about 12 minutes on a
     recent Mac; Qwen3-TTS takes about four times as long.
2. **Package** for the enTalkenator app (one document at a time):
   `SOURCES package <dir> --audio "<dir>/<Title>.m4a"`. The bundle holds:
   - the PDF
   - the reading as the paper's text
   - the narration as the paper's own audio
3. **Publish** if a library is configured (`PUBLISH status`; same choices as in the entalkenate
   skill):
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
