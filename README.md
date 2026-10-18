# enTalkenator

Turn any document into a podcast you can listen to anywhere.

Give enTalkenator a PDF, an article, or your notes, and pick a format: a debate, a podcast
conversation, a class, an oral argument, a quiz show, and more. Claude writes it with a
separate performer for each speaker, your Mac voices it, and it shows up in your own private
podcast feed and web library.

There's no enTalkenator account and no subscription. It runs on:
- **your Claude plan**, for the writing
- **your Mac**, for the voices (a free model, or the voices built into macOS)
- **your own free Cloudflare account**, for the library and podcast feed. This part is
  optional.

## What you need

- **A Mac.** One with Apple Silicon (M1 or later) gets the best voices; any Mac can use the
  built-in macOS voices.
- **Claude Code** with a Claude Pro or Max plan. Either the Claude desktop app's **Code**
  tab or the `claude` command in Terminal works.
- **For the library (optional):** a free Cloudflare account. If you don't have one, Claude
  walks you through making one. Cloudflare may ask for a card before it switches on its
  file storage, but a personal library stays within the free allowance.

You don't need GitHub, a website, a domain name, or any coding.

## Get started

1. Unzip the download. Put the `enTalkenator` folder somewhere you'll keep it, such as
   Documents.
2. Open the folder in Claude:
   - **Claude desktop app:** open the **Code** tab, choose the `enTalkenator` folder, and
     say yes when asked whether you trust it.
   - **Terminal:** `cd` into the folder and run `claude`.
3. Say **"Set me up."**

Claude checks what's on your Mac and asks before installing anything. It then:
- installs the voices: free, about 660 MB
- helps you create the library on Cloudflare
- makes a short first program so you can hear it on your phone

The whole thing takes about 20 minutes, most of it waiting for downloads.

## Making programs

Open the folder in Claude and ask in your own words:

> Make a debate from ~/Downloads/the-paper.pdf

> Turn this article into a podcast conversation: (paste the text)

> What formats are there?

Claude shows you the speakers and lets you flesh them out, then writes the program one
speaker at a time. You can also type `/entalkenate`. Programs are saved in the
`programs` folder. If you set up the library, Claude offers to publish each one when it's
done.

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
- **Voices:** free (Kokoro or macOS). Google's Gemini voices are an option if you have a
  Gemini API key, at about $0.40 per 30 minutes.
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

- The voice model **Kokoro-82M** and its **misaki** pronunciation library are Apache-2.0.
- **mlx-audio** is MIT.
- The installer also fetches **espeak-ng** (GPL-3.0), used for pronunciation.
- Cloudflare's **Wrangler** is MIT/Apache-2.0.

The enTalkenator templates come from the enTalkenator app.

## More

- [docs/how-it-works.md](docs/how-it-works.md): how programs are made, test results, and
  design notes
- [cloud/README.md](cloud/README.md): how the library works
- [docs/developing.md](docs/developing.md): for changing enTalkenator itself
