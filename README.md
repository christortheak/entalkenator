# enTalkenator

Listen to what you read: papers, briefs, articles, chapters, anywhere.

- **Narration.** Claude reads the document itself to you, word for word, like an audiobook,
  but made for listening. Running heads, page numbers and footnote numbers are gone, and
  citations are dropped or kept as names. Figures and tables are described. The footnotes
  that matter are read in a second voice right after their paragraph.
- **Programs.** Turn one document, or a whole set, into a debate, a podcast conversation,
  a class, an oral argument, a quiz show, and more. Claude writes it with a separate
  performer for each speaker.

Your Mac voices it, and it shows up in your own private podcast feed and web library.

There's no enTalkenator account and no subscription. It runs on:
- **your Claude plan**, for the writing
- **your Mac**, for the voices (free models that run on it), or a cloud voice service you
  choose, with your own key
- **your own free Cloudflare account**, for the library and podcast feed. This part is
  optional.

## What you need

- **A Mac.** One with Apple Silicon (M1 or later) gets the best voices; any Mac can use the
  built-in macOS voices.
- **Claude Code** with a Claude Pro or Max plan. Either the Claude desktop app's **Code**
  tab or the `claude` command in Terminal works. Cowork and chats don't: Cowork runs its
  commands in a sandbox that can't reach your Mac's voices, Keychain, or PDF tools.
- **For the library (optional):** a free Cloudflare account. If you don't have one, Claude
  walks you through making one. Cloudflare may ask for a card before it switches on its
  file storage, but a personal library stays within the free allowance.

You don't need GitHub, a website, a domain name, or any coding.

## Get started

1. Unzip the download. (From GitHub the folder is called `entalkenator-main`; rename it if
   you like.) Put it somewhere you'll keep it that iCloud Drive doesn't sync, such as your
   home folder: programs are large audio files, and syncing them slows things down. If your
   Mac syncs Desktop and Documents to iCloud, avoid those two.
2. Open the folder in Claude:
   - **Claude desktop app:** open the **Code** tab, choose the `enTalkenator` folder, and
     say yes when asked whether you trust it.
   - **Terminal:** `cd` into the folder and run `claude`.
3. Say **"Set me up."**

Claude checks what's on your Mac and asks before installing anything. It then:
- shows you the voice options and installs the ones you choose (see [Voices](#voices))
- helps you create the library on Cloudflare
- makes a short first program so you can hear it on your phone

The whole thing takes about 20 minutes, most of it waiting for downloads.

## Narrations

> Read me ~/Downloads/the-article.pdf

> Narrate pages 12–40 of this book chapter, skip the footnotes

Claude looks over each document first: what it is, how it's laid out, what to read and what
to skip. It asks you only when there's a real choice to make ("The appendix is 12 payoff
tables. Describe them, or skip them?"), and it remembers your usual preferences. Each
narration comes with a faithfulness check, a comparison with the PDF's own text, which
confirms Claude read the document rather than summarizing it. Chapters follow the
document's sections.

## Programs

Open the folder in Claude and ask in your own words:

> Make a debate from ~/Downloads/the-paper.pdf

> A seminar on the three articles in ~/Documents/Week 6

> Make a workshop hot bench from this PDF. No questions, use your best judgment.

> An hour-long faculty workshop on this paper: a 20-minute talk, the rest questions

> A Socratic law school class on this article

Claude plans the program from what you ask (who's there, what happens, and for how long)
and then directs it as it goes, giving the floor to whoever would naturally speak next and
changing the plan when the conversation earns it. The formats from the enTalkenator app are
its starting points.

With "no questions", Claude makes every choice itself, publishes to your library, and tells
you when it's done, so you can walk away. Keep the Mac awake and Claude open until it
finishes. Otherwise Claude asks only when a choice is really yours, and offers to remember
the answer. You can also just tell it what you like ("always publish", "chapters for the
main parts only", "never read footnotes"), and it remembers.

> Turn this article into a podcast conversation: (paste the text)

> What formats are there?

Claude shows you the speakers and lets you flesh them out, then writes the program one
speaker at a time. The speakers work from each PDF's own text and can open the PDF itself to
check a figure or a table; only a scanned PDF needs Claude to read it first. For a large set
of reading, Claude offers to summarize each document for the speakers instead, and keeps
the summaries for next time. You can also type `/entalkenate`. Programs are saved in the
`programs` folder. If you set up the library, Claude offers to publish each one when it's
done.

## Voices

You choose, and can change your mind for any program. The local voices run on your Mac's
own chip, so how long they take depends on the Mac. The table gives the work each one
needs relative to Kokoro instead of times:

| | Where | Cost for a 20-minute program | Work on your Mac | Notes |
|---|---|---|---|---|
| **Kokoro** | your Mac | free | light | Natural and even. Any Apple Silicon Mac; 660 MB download. |
| **Qwen3-TTS** | your Mac | free | heavy: about 7× Kokoro | More expressive: each character's voice is designed from a description. 16 GB of memory or more; 6.2 GB download. |
| **Qwen3 preset voices** | your Mac | free | medium: about 3× Kokoro | Directed line by line ("amused, pressing"). Only two native English voices, both men; the other seven speak English with an accent. 16 GB of memory or more; 3.1 GB download. |
| **Breeze TTS 2** | your Mac | free | very heavy: about 12–20× Kokoro (a fast mode is several times quicker, a little less natural) | The most natural local voices: designed from descriptions, directed line by line, and they laugh and sigh. 24 GB of memory or more; 7.6 GB download. Personal, educational and research use only (BreezeBlue's licence). English and Chinese. |
| **macOS voices** | your Mac | free | light | Built in, nothing to install, robotic. |
| **Google Gemini** | cloud | about $0.19–$0.28 | none | Best value; excellent quality. Needs a Google AI Studio key. |
| **OpenAI** | cloud | about $0.30–$0.65 | none | gpt-4o-mini-tts, tts-1 and tts-1-hd. Needs an OpenAI key with credit. |
| **ElevenLabs** | cloud | about $0.85–$1.75 | none | The best-sounding voices. Needs an ElevenLabs key. |

None of these is bundled: the download is just enTalkenator's code, and each voice is
downloaded only when you choose it, with its size stated first. Claude checks what your Mac
can run before offering anything, and suggests what's worth trying: Kokoro first, then one
richer voice that fits your memory. After your first program it can play you its opening in
each voice you've installed, so you can choose by ear. Once a voice has rendered on your
Mac, Claude knows how fast it is there, and tells you how long a program will take before
starting. If a heavy voice would take too long for a long program, or your
Mac doesn't have the memory, it suggests a lighter voice or a cloud one, with what that
would cost.

**Mix and match.** Each speaker can have a different voice engine: the lead in Breeze, the
others in Kokoro, a caller on a cloud voice. Heavy voices then go only where you'll notice
them, which also makes the program quicker.

**Your own voice.** With Qwen3-TTS or Breeze installed, you can read a 15-second script into
Voice Memos, and your programs can have you as host or presenter. The same works for a
colleague who agrees to it, never for someone who hasn't. Ask Claude "can it sound like me?"
and it walks you through it. The recording stays on your Mac.

Prices were checked in October 2026 and change. Cloud voices are billed by the provider to
your own account; enTalkenator never sees your key: you type it into a small Mac window,
and it's kept in your Mac's Keychain (look for "enTalkenator" in Keychain Access to see or
delete it). Every program in your library says its voices are AI-generated.

## Your library

The library is a small website on **your** Cloudflare account. It has:
- a web app with search, a player that remembers where you were, and a transcript that
  follows along. You can add it to your phone's home screen.
- a private podcast feed for Apple Podcasts, Overcast, or Pocket Casts, plus one feed per
  tag

It's private: the web app needs your passphrase, and the feed has a secret address. Keep
that address to yourself.

## Costs

- **enTalkenator:** free.
- **Claude:** programs use your plan's usage. A 20-minute debate takes about 10 minutes to
  make.
- **Voices:** free on your Mac, or paid cloud voices with your own key (see [Voices](#voices)).
- **Cloudflare:** free for a personal library. The free plan includes 10 GB of storage,
  about 650 twenty-minute programs.

## Updating

Download the new version and replace your old `enTalkenator` folder with it. Your programs
are in `programs` inside the old folder, so move that folder across first. Then open it in
Claude and say **"Update my library."** Your programs, passphrase, and feed address stay the
same.

## Privacy and fair use

- The documents you use stay on your Mac and in your Claude conversation, plus your own
  library if you publish there.
- Programs are made from other people's writing. Keep your library for your own listening,
  and don't submit the feed to podcast directories.
- The speakers paraphrase and can get things wrong. Check the original before relying on
  something you heard.

## Licences

enTalkenator itself is MIT-licensed (see `LICENSE`). What it downloads, with your OK, has
its own terms:
- The voice models **Kokoro-82M** and **Qwen3-TTS**, and Kokoro's **misaki** pronunciation
  library, are Apache-2.0.
- **Breeze TTS 2** is under BreezeBlue's Research and Non-Commercial License: personal,
  educational and research use only, and no cloning a voice without its owner's consent.
- **mlx-audio** is MIT.
- The installer also fetches **espeak-ng** (GPL-3.0), used for pronunciation.
- Cloudflare's **Wrangler** is MIT/Apache-2.0.

The enTalkenator templates come from the enTalkenator app.

## More

- [docs/how-it-works.md](docs/how-it-works.md): how programs are made, test results, and
  design notes
- [docs/voices.md](docs/voices.md): the voice options compared, with sources
- [cloud/README.md](cloud/README.md): how the library works
- [docs/developing.md](docs/developing.md): for changing enTalkenator itself
