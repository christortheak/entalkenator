---
name: setup-library
description: Set up the user's own private enTalkenator library on Cloudflare's free plan. It's one Worker with R2 and D1 that serves a web player at /app and private podcast feeds for Apple Podcasts, Overcast, or Pocket Casts, so programs made with /entalkenator:entalkenate can be heard on any device without the app. Use when asked to set up, deploy, connect, check, or fix the enTalkenator library, cloud library, or podcast feed.
argument-hint: "[--cli | --deploy-button] [--passphrase | --access]"
allowed-tools: Read Bash(python3 *)
---

# Set up your enTalkenator library

Arguments: `$ARGUMENTS`

You're helping the user deploy **their own** library to **their own** Cloudflare account.
Nothing is shared with the plugin's author or anyone else. The library has four parts:

- `/app`: the web library. It has search, a player with speed control and resume, a transcript that
  follows the audio, and downloads. Phones can install it as an app.
- `/feed/<FEED_TOKEN>.xml`: a private podcast feed, plus one feed per tag.
- `/api`: uploads from `publish.py`, guarded by `UPLOAD_TOKEN`.
- Storage: R2 holds the files and D1 holds the index. It all fits Cloudflare's free plan.

Paths (quote them; they may contain spaces):

- `PUBLISH` = `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/publish.py"`
- `CONFIG` = `"${CLAUDE_PLUGIN_DATA}/library.json"`: where the plugin keeps the library address
  and upload token (mode 600)
- `CLOUD_SRC` = `"${CLAUDE_PLUGIN_ROOT}/cloud"`: the Worker's source

**Secrets stay out of the conversation.** Never print, echo, or ask the user to paste
`UPLOAD_TOKEN`, `FEED_TOKEN`, or `APP_PASSPHRASE` into the chat. `PUBLISH setup-secrets`
generates the tokens and hands them straight to Cloudflare and the config file. The user types
the passphrase into Wrangler's own prompt. Feed addresses contain `FEED_TOKEN`, so send the
user to the web app's **Podcast feeds** page for them rather than printing them.

## 0. Check what's there

Run `PUBLISH status --config CONFIG --url "${user_config.library_url}"`.
- If it reports `configured: true`, run `PUBLISH info --config CONFIG`. Tell the user the library
  is already connected: its URL, item count, storage used, and login mode. Ask whether to stop
  there, check that it works (step 4), or set up a new library.
- Otherwise continue.

## 1. Explain, then ask two questions

Say briefly what will happen, what it costs, and how private it is:
- It deploys one Worker on the user's Cloudflare account. The free plan is enough:
  - 100,000 requests a day
  - 10 GB of R2 storage, about 650 twenty-minute programs with bundles, or twice that without
  - no charge for listening (R2 has no egress fees)
- Everything is private by default: the web app needs a login, and feeds need a long secret
  address.

Then ask, in one AskUserQuestion call (skip a question the arguments already answer):
- **Deploy** (header "Deploy"):
  - "From this computer with Wrangler (Recommended)": you run the commands. It needs Node.js
    and a free Cloudflare account.
  - "Deploy to Cloudflare button": a web flow that copies the code into a new GitHub or GitLab
    repository of theirs and deploys it. It only works if the enTalkenator plugin's repository
    is public on GitHub. Say so.
- **Web app login** (header "Login"):
  - "Passphrase (Recommended)": the Worker shows a sign-in page. One passphrase, 180-day
    cookie.
  - "Cloudflare Access": sign in with email codes or Google/GitHub, through Cloudflare Zero
    Trust (free for up to 50 users). It's more setup.

## 2a. Deploy from this computer (Wrangler)

1. **Prerequisites.** Run `node --version` (Node 18 or newer). If Node is missing, ask the user
   to install it from nodejs.org, and stop until they have.
2. **Pick a folder the user owns** for the deployable copy. Default: `~/entalkenator-library`.
   The plugin folder is replaced on every plugin update, so never deploy from `CLOUD_SRC`
   itself. If the folder doesn't exist, copy the source without local state:
   `rsync -a --exclude node_modules --exclude .wrangler --exclude .dev.vars --exclude worker-configuration.d.ts CLOUD_SRC/ ~/entalkenator-library/`.
   If it already exists, it's an earlier deploy. Ask before updating it, then copy only
   `src/`, `public/`, `migrations/`, and `package.json`. Keep their `wrangler.jsonc` (it holds
   their name and settings).
3. **Install Wrangler, with permission.** Ask before running `npm install` in that folder.
   It installs `wrangler` (Cloudflare's CLI, MIT/Apache-2.0), about 225 MB on disk, mostly
   the local Workers runtime. Nothing is installed globally.
4. **Log in.** `npx wrangler whoami`. If it isn't logged in, run `npx wrangler login`. It
   opens the browser for the user to approve, so tell them to look for it.
5. **Name and settings.** Ask what to call the library. The Worker `name` becomes part of the
   address: `https://<name>.<their-subdomain>.workers.dev`. The default is
   `entalkenator-library`. Edit the copy's `wrangler.jsonc`:
   - `name` (lowercase letters, digits, hyphens)
   - `vars.LIBRARY_TITLE` if they want a different display name
   - `vars.AUTH_MODE`: `"passphrase"` or `"access"`
6. **Deploy.** `npm run deploy` in the folder. That's `wrangler deploy` (which creates the R2
   bucket and D1 database on first run) followed by the D1 migration.
   - If R2 isn't enabled on the account, Wrangler says so. The user enables R2 once in the
     dashboard (R2 Object Storage). Cloudflare may ask for a payment method, but use within
     the free allowance costs nothing.
   - If the account has no workers.dev subdomain yet, they pick one in the dashboard
     (Workers & Pages), then deploy again.
   - Note the `https://….workers.dev` address it prints.
7. **Secrets.** Run
   `PUBLISH setup-secrets --cloud-dir ~/entalkenator-library --url <the workers.dev address> --config CONFIG`.
   This generates `UPLOAD_TOKEN` and `FEED_TOKEN` and sets them on the Worker with
   `wrangler secret bulk`. It also saves the address and upload token for the plugin. Its output
   names the secrets but not their values. On a later re-run, add `--keep-feed-token` unless the
   user wants to change their feed address: changing it breaks existing podcast subscriptions.
8. **Login secret.**
   - Passphrase mode: ask the user to run this in their own terminal and type a passphrase of
     10+ characters (a few words is best; they'll type it on their phone):
     `cd ~/entalkenator-library && npx wrangler secret put APP_PASSPHRASE`.
     Don't ask them to tell you what it is.
   - Access mode: do section 3.

## 2b. Deploy to Cloudflare button

1. The button link is
   `https://deploy.workers.cloudflare.com/?url=<public GitHub URL of the repo>/tree/main/plugin/cloud`.
   If you don't know the repository's public URL, ask the user. If the link shows an error, the
   repository isn't public; switch to 2a.
2. Before they click, the user makes two long random values for the form, in their own terminal
   or password manager (never in this chat):
   `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`, run twice.
   One value is `UPLOAD_TOKEN`, which they keep for step 4. The other is `FEED_TOKEN`.
3. The form asks for:
   - a Git account and repository name
   - the R2 bucket and D1 database (the defaults are fine)
   - the three secrets: `UPLOAD_TOKEN`, `FEED_TOKEN`, and `APP_PASSPHRASE`. In Access mode,
     `APP_PASSPHRASE` can be anything; it isn't used.

   Then it builds and deploys, and shows the `….workers.dev` address.
4. **Connect the plugin.** The user enters the address and `UPLOAD_TOKEN` in the plugin's
   settings, **library_url** and **library_token**. Claude Code asks for them when the plugin is
   enabled, and the plugin manager (`/plugin`) can change them later. The token is kept in the
   system keychain. A session-start hook copies both into `CONFIG` for `publish.py`, so they take
   effect in the **next** session.
   - To use the library right away instead, the user copies the token to the clipboard and runs
     this in their own terminal (give them the command with the paths filled in):
     `pbpaste | python3 "<plugin root>/scripts/publish.py" configure --url <address> --token-stdin --config "<CONFIG path>"`
5. For Access mode, change `AUTH_MODE`, `ACCESS_TEAM_DOMAIN`, and `ACCESS_AUD` in
   `wrangler.jsonc` in their new repository. Each push redeploys.

## 3. Cloudflare Access (only if chosen)

Podcast apps can't sign in, so Access must cover **only `/app`**. `/feed`, `/media`, and `/api`
must stay reachable without it; the Worker's own tokens protect them. Walk the user through it
in the Cloudflare dashboard:

1. **Zero Trust.** Open Zero Trust. The first time, it asks them to choose a team name and the
   Free plan. The team domain is `https://<team>.cloudflareaccess.com`.
2. **Protect `/app`.** Go to Access → Applications → Add an application → Self-hosted:
   - Domain: the Worker's hostname (`<name>.<subdomain>.workers.dev`), with path `app`
   - Policy: Allow, with Include → Emails → the user's own address (and anyone else they choose)
   - Identity: the one-time PIN (email code) works with no other setup
3. **Don't use the Worker's "All traffic" Access switch** (Worker → Settings → Domains &
   Routes, or the Worker's Access tab). It protects the whole hostname, and podcast apps would
   be locked out of the feeds. If the hostname is already protected that way, add a second
   self-hosted application for the same hostname with paths `feed`, `media`, and `api`, and a
   **Bypass** policy for Everyone. More specific paths win.
4. **Tell the Worker.** Copy the application's **Application Audience (AUD) Tag** (in the
   application's settings) and the team domain. Put them in `wrangler.jsonc` as
   `ACCESS_AUD` and `ACCESS_TEAM_DOMAIN`, set `AUTH_MODE` to `"access"`, and redeploy
   (`npm run deploy`).

The Worker checks the Access token's signature, audience, issuer, and expiry on every `/app`
request. A misconfigured setup shows a "Not signed in through Cloudflare Access" page; it never
falls open.

## 4. Check it works

- Run `PUBLISH check --config CONFIG`. It uses the upload token to read the library's details,
  then fetches the feed and `/app/` with no credentials, the way a podcast app or a new
  browser would. It prints no tokens or feed addresses. Expect `"ok": true`. Otherwise its
  `problems` say what to fix:
  - a secret that isn't set
  - a feed that redirects to Cloudflare Access (fix step 3.3)
  - Access mode with no Access application in front of `/app`
  - a wrong upload token (HTTP 401)
- Ask the user to open `<address>/app/` and sign in. They should see an empty library.

## 5. Add the feed to a podcast app

The user copies the address from the web app's **Podcast feeds** page. It has Copy buttons and
an "Open in Overcast" link, plus one feed per tag.
- **Apple Podcasts**:
  - iPhone or iPad: Library → **⋯** → **Follow a Show by URL**
  - Mac: **File → Follow a Show by URL**
  - Followed by URL, the show is private to them.
- **Overcast:** **+** → **Add URL**. Overcast keeps feeds marked `itunes:block` out of its
  search.
- **Pocket Casts:** go to pocketcasts.com/submit, paste the address, and choose **Private**.
  Then search for the short link it returns in the app. Without "Private", Pocket Casts may
  list the show for others.

To get programs into the library, `/entalkenator:entalkenate` offers to publish at the end of
each run. To publish an earlier run, use `PUBLISH push <run dir> --config CONFIG`. Tags
(`--tag "Supreme Court"`) give it a feed of its own.

## 6. Privacy and copyright, in plain words

Tell the user this, briefly:
- **Your programs are made from other people's work.** The library is for your own listening.
  Don't post feed links, submit them to podcast directories, or share the web app's passphrase
  beyond people you'd hand the documents to anyway. Bundles include the original document, so
  only the signed-in web app offers them, not the feeds.
- **A feed address works like a password.**
  - Anyone who has it can listen.
  - Overcast and Pocket Casts fetch feeds from their own servers, so those services see it.
  - If an address leaks, run `npx wrangler secret put FEED_TOKEN` in the deploy folder with a
    new value (or re-run `setup-secrets`). Then re-add the feed in your apps; every old address
    stops working.
- **Kept out of search.**
  - The feeds carry `itunes:block`, `podcast:block`, and `podcast:locked`.
  - Every response is `noindex` and `no-referrer`, and `robots.txt` disallows everything.
  - The R2 bucket has no public access; the Worker is the only way in.
- **Generated speech isn't the source.** Speakers paraphrase and can get things wrong; check
  the document before relying on a claim.
