---
name: setup-library
description: Set up the person's own private enTalkenator library on their free Cloudflare account, step by step, starting from no account at all. The library is a web app that plays their programs on any device, plus a private podcast feed for Apple Podcasts, Overcast, or Pocket Casts. Also use it to check, update (after downloading a newer enTalkenator), fix, change the passphrase of, or remove the library.
argument-hint: "[update | check | passphrase | remove]"
allowed-tools: Read Bash(python3 scripts/*)
---

# Your enTalkenator library

Arguments: `$ARGUMENTS`

The library runs on the person's **own** Cloudflare account; nobody else can see it. It's one
Cloudflare Worker (the code in `cloud/`) with:
- `/app`: a web app with search, a player that remembers its place, a transcript that
  follows along, and downloads. It can be installed on a phone's home screen.
- `/feed/...`: a private podcast feed with a secret address, plus one feed per tag.
- R2 storage for the files and a D1 database for the index. It all fits Cloudflare's free
  plan.

Follow CLAUDE.md's "Who you're helping" and "Rules that always apply" throughout. Above
all: never show a token, a feed address, or the passphrase, and never sign up, sign in, or
enter payment details for the person.

Scripts run from the enTalkenator folder, `${CLAUDE_PROJECT_DIR}`. If an earlier command
changed directory, `cd` back there first. Quote other paths; they may contain spaces.

- `LIB` = `python3 scripts/library.py`: setup and deploy. Every command prints JSON. When
  something has to happen first, it exits with `"ok": false` and a `needs` list. Each need
  has a `what`, a `tell` (what to explain), and sometimes a `url`. Handle each need as
  described below, then run the same command again.
- `PUBLISH` = `python3 scripts/publish.py`: talks to the library.

## 0. Is there one already?

Run `LIB status`.
- If `library.configured` is true, run `PUBLISH info`. Say the library is set up: its
  address, how many programs, storage used, and the login mode. Then do what the arguments
  or the person ask for (see "Looking after the library" below). If they want a second,
  separate library, that's a new `--name` (and a new `--config` file).
- Otherwise, start at step 1.

## 1. Explain, and choose how the web app is protected

Tell them, briefly:
- It costs nothing on Cloudflare's free plan, which includes 100,000 requests a day and
  10 GB of storage (about 650 twenty-minute programs). Listening has no data charges.
- It's private: the web app needs a passphrase, and the podcast feed's address contains a
  long secret.
- It takes about 15 minutes. They'll need to sign up for Cloudflare (if they haven't),
  click Allow once in the browser, and choose a passphrase.

Ask with AskUserQuestion (header "Login"):
- "A passphrase (Recommended)": one passphrase, typed once per device, remembered for 180
  days. Simplest for one person.
- "Cloudflare Access": each person signs in with an email code or Google, and access can be
  removed per person. Better for sharing with family or a class, but more setup (section
  "Cloudflare Access" below).

## 2. A Cloudflare account

Ask (header "Cloudflare"): "Do you have a Cloudflare account?"
- "No, help me make one": run `open https://dash.cloudflare.com/sign-up`. Tell them:
  - Sign up with an email and password. Cloudflare emails a link to verify the address;
    click it.
  - They don't need a website or domain. If Cloudflare asks to add a domain or pick a
    plan for one, skip it.
  - Say when it's done.
- "Yes": go on. If they have several accounts, step 5 asks which to use.

## 3. Node.js

Wrangler, Cloudflare's setup tool, runs on Node.js. Check `nodeOk` from `LIB status`.
If it's false:
- If Homebrew is installed (`command -v brew` succeeds), offer `brew install node`, with
  permission. Node.js is MIT-licensed, about 100 MB.
- Otherwise run `open https://nodejs.org/en/download` and tell them to choose the **macOS
  Installer (.pkg)** for the LTS version, open it, and click through. It asks for their Mac
  password; that goes into macOS's own prompt.
- Then run `LIB status` again to confirm.

## 4. Wrangler

Ask first: "Wrangler is Cloudflare's tool for setting up a site on your account
(MIT/Apache-2.0). It's about 210 MB, installed in `~/.cache/entalkenator/wrangler`, with
nothing system-wide." If they agree, run `LIB install-wrangler` with a 10-minute timeout.

## 5. Let Wrangler use the account

Tell them what's coming: a Cloudflare page opens in their browser. They sign in if
asked, check that it's the right account, and click **Allow**. The page then says they can
close it.

Run `LIB login` with a 10-minute timeout. It returns when they've clicked Allow, or
reports that the login didn't finish. If so, offer to try again.

Then run `LIB preflight` and handle its `needs`:
- `account`: the login can use several accounts. Ask which one, by name (AskUserQuestion),
  and pass `--account-id <id>` to every later `LIB` command.
- `r2`: R2 storage isn't switched on. Run `open <url>` and explain its `tell`:
  - In R2, they follow the steps to turn it on. Cloudflare may ask for a card or PayPal
    first; they enter it themselves. The free allowance means no charge for a library
    this size.
  - Say when it's done, then run `preflight` again.
- `name-taken`: there's already a Worker called that on the account. Ask for another name,
  for example `<their-name>-library`, and pass `--name`. Use `--adopt` only if they say
  it's their earlier enTalkenator library.

## 6. Deploy

Say what will be created and ask for a go-ahead: "This puts your library on your Cloudflare
account: a Worker called `<name>` (its address will be `https://<name>.<your
subdomain>.workers.dev`), a storage bucket, and a small database, all on the free plan.
OK?" Use the default name `entalkenator-library` unless they want another. They can also
give the library a title for podcast apps (`--title`, default "enTalkenator Library").

Run `LIB deploy [--name N] [--title T] [--account-id A]` with a 10-minute timeout.
- It's safe to re-run. It reuses whatever already exists.
- `needs` with `subdomain`: the account has no workers.dev address yet. Run `open <url>`.
  In Workers & Pages, the first visit asks them to choose a subdomain. Any free name works;
  it becomes part of the library's address. Then deploy again.
- `needs` with `r2` or `name-taken`: as in step 5.
- On success, note `url`. If `next` includes `passphrase`, go to step 7. Otherwise go to
  step 8.

## 7. The passphrase

Tell them: "A small enTalkenator window is about to appear on your Mac. Type a passphrase
of at least 10 characters (a few words is easiest to type on a phone), then type it again.
It goes straight to Cloudflare; I won't see it."

Run `LIB passphrase` in the background and wait for it to finish; it waits for them.
- If it reports `cancelled`, ask whether to try again.
- If it reports `needs` with `terminal` (not a Mac), give them its `command` to run in
  their own terminal.

To change the passphrase later, run the same command.

## 8. Check it

Run `PUBLISH check`. Expect `"ok": true`. Otherwise its `problems` say what's wrong:
- the passphrase isn't set: step 7
- a feed that redirects to Cloudflare Access: see "Cloudflare Access"
- a wrong upload token (HTTP 401): `LIB deploy` again; it replaces the token

Then run `LIB open`. The web app opens in their browser. They sign in with the passphrase
and should see an empty library. Safari and other browsers offer to save the passphrase.

## 9. Their phone and podcast app

- **Phone:** open the same address (`<url>/app/`) on the phone and sign in. On an iPhone,
  Share → **Add to Home Screen** makes it an app icon.
- **Podcast app:** the feed address is on the web app's **Podcast feeds** page
  (`LIB open '#/feeds'` on the Mac, or the menu in the web app). It has Copy buttons and an
  "Open in Overcast" link, plus one feed per tag. Don't show the address in the chat.
  - **Apple Podcasts:** on iPhone or iPad, Library → **⋯** → **Follow a Show by URL**. On a
    Mac, **File → Follow a Show by URL**. A show followed by URL stays private to them.
  - **Overcast:** **+** → **Add URL**.
  - **Pocket Casts:** go to pocketcasts.com/submit, paste the address, choose **Private**,
    then search for the short link it gives in the app. Without "Private", Pocket Casts may
    list the show for others.

New programs reach the feed when they're published (the entalkenate skill offers this at the
end of each run). Podcast apps check for new episodes every so often, usually within an
hour.

## 10. Privacy and copyright, in plain words

Tell them, briefly:
- **Programs are made from other people's work.** The library is for their own listening.
  Don't post feed links, submit the feed to podcast directories, or share the passphrase
  beyond people they'd hand the documents to anyway. Downloads of the original document
  are only in the signed-in web app, not the feeds.
- **A feed address works like a password.** Anyone with it can listen. Overcast and Pocket
  Casts fetch feeds from their own servers, so those services see it. If an address
  leaks, `LIB deploy --new-feed-address` changes it. Every old address stops working, so
  they re-add the feed in their apps.
- **It's kept out of search.** The feeds are marked private (`itunes:block`), every page
  says `noindex`, and the storage has no public access; the Worker is the only way in.
- **Generated speech isn't the source.** Speakers paraphrase and can be wrong.

## Looking after the library

- **Update** (after downloading a newer enTalkenator, or when the session status says the
  library runs an older version): `LIB deploy`. It uses the saved settings and keeps the
  programs, the passphrase, and the feed address. Ask before running it; it changes their
  Cloudflare account.
- **Check:** `PUBLISH check`.
- **Change the passphrase:** step 7.
- **New feed address:** `LIB deploy --new-feed-address`. Warn first that every podcast
  subscription has to be re-added.
- **Log in again** (Wrangler's login expired, or they want another account): `LIB login
  --again`.
- **Remove it:** first ask with AskUserQuestion. Say plainly that it permanently deletes
  every program in the library, the feeds, and the web app, and that copies in
  `programs/` on this Mac are kept. Make "Keep it" the first option. Only on an explicit
  yes, run `LIB remove --yes`, and report what it deleted. If a step failed, its error says
  what to delete by hand in the dashboard.

## Cloudflare Access (only if chosen)

Podcast apps can't sign in, so Access must cover **only `/app`**. `/feed`, `/media`, and
`/api` must stay reachable without it; the Worker's own tokens protect them.

1. Deploy first (step 6) with the passphrase default, so the address exists. The Worker
   won't open the web app until Access is set up.
2. Walk them through the Cloudflare dashboard (`open https://one.dash.cloudflare.com/`):
   - **Zero Trust.** The first time, it asks for a team name and the Free plan (up to 50
     users). The team domain is `https://<team>.cloudflareaccess.com`.
   - **Protect `/app`.** Access → Applications → Add an application → Self-hosted:
     - Domain: the library's hostname (`<name>.<subdomain>.workers.dev`), with path `app`
     - Policy: Allow, with Include → Emails → their own address (and anyone else they
       choose)
     - Login method: the one-time PIN (email code) works with no other setup
   - **Don't use the Worker's own "Access" switch** (Worker → Settings → Domains &
     Routes). It protects the whole address, and podcast apps would be locked out of the
     feeds. If it's already on, add a second self-hosted application for the same
     hostname with paths `feed`, `media`, and `api`, and a **Bypass** policy for Everyone.
3. They copy the application's **Application Audience (AUD) Tag** (in its settings) and
   tell you the team domain. The AUD tag isn't a secret.
4. Run `LIB deploy --auth access --access-team-domain https://<team>.cloudflareaccess.com --access-aud <tag>`.
5. Run `PUBLISH check`. It confirms that Access is in front of `/app` and not in front of
   the feed.

The Worker checks the Access token's signature, audience, issuer, and expiry on every `/app`
request. A misconfigured setup shows a "Not signed in through Cloudflare Access" page; it
never falls open.
