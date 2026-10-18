#!/usr/bin/env python3
"""Set up, update, and remove your enTalkenator library on Cloudflare.

The library is one Cloudflare Worker (the code in cloud/) with an R2 bucket for the
audio and a D1 database for the index. It serves a web app at /app and private podcast
feeds. Everything here goes through Cloudflare's own command-line tool, Wrangler; this
script never handles your Cloudflare login itself.

Commands (each prints JSON; "needs" lists what the person has to do before going on):
  status             what's in place on this computer (no network)
  install-wrangler   install Wrangler into ~/.cache/entalkenator/wrangler (about 210 MB)
  login              open the browser so the person can let Wrangler use their account
  preflight          check the account: logged in, which account, R2 switched on, and
                     whether the library's Worker, bucket and database already exist
  deploy             create or reuse the bucket and database, upload the Worker, set up
                     the database tables and the Worker's tokens, and save the library's
                     address and upload token on this computer. Safe to run again: that's
                     how a newer version of enTalkenator updates the library.
  passphrase         ask for the web app's passphrase in a macOS dialog and set it on the
                     Worker. The passphrase is never printed.
  open               open the web app in the browser
  remove --yes       delete the library: every program in it, the Worker, the bucket and
                     the database. This can't be undone.

Settings are kept in ~/.config/entalkenator/library.json (mode 600), shared with
publish.py. Python 3.9+, standard library only.
"""

import argparse
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import dialog  # noqa: E402
import publish  # noqa: E402  (shares the config file and the library API client)

CLOUD = os.path.join(ROOT, "cloud")
WRANGLER_VERSION = "4.145.0"
NODE_MAJOR = 22  # Wrangler 4.145 needs Node.js 22 or newer
ENT_HOME = os.environ.get("ENTALKENATOR_HOME", os.path.expanduser("~/.cache/entalkenator"))
WRANGLER_HOME = os.path.join(ENT_HOME, "wrangler")
# The per-person Wrangler config, regenerated from cloud/wrangler.jsonc on every deploy.
DEPLOY_CONFIG = os.path.join(ENT_HOME, "deploy", "wrangler.json")
DEFAULT_NAME = "entalkenator-library"
DEFAULT_TITLE = "enTalkenator Library"
NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])$")
DASH = "https://dash.cloudflare.com/"


def emit(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def done(obj, code=0):
    emit(obj)
    sys.exit(code)


def need(stage, *needs, **extra):
    """Stop: the person has to do something first."""
    done(dict({"ok": False, "stage": stage, "needs": list(needs)}, **extra), 2)


def config_path(args):
    return args.config or publish.default_config_path()


# ---------------------------------------------------------------------------
# Node and Wrangler


def node_version():
    node = shutil.which("node")
    if not node:
        return None
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.match(r"v?(\d+)\.(\d+)", out)
    return (int(m.group(1)), int(m.group(2)), out) if m else None


def wrangler_cmd():
    """How to run Wrangler, or None if it isn't installed. Never falls back to npx,
    which would download it without asking."""
    if os.environ.get("ENTALKENATOR_WRANGLER"):
        return shlex.split(os.environ["ENTALKENATOR_WRANGLER"])
    local = os.path.join(WRANGLER_HOME, "node_modules", ".bin", "wrangler")
    return [local] if os.path.exists(local) else None


def wrangler_env(account_id=None):
    env = dict(os.environ)
    env.update({"CI": "1", "NO_COLOR": "1", "FORCE_COLOR": "0", "WRANGLER_SEND_METRICS": "false"})
    if account_id:
        env["CLOUDFLARE_ACCOUNT_ID"] = account_id
    return env


ANSI = re.compile(r"\x1b\[[0-9;]*m")


def wrangler(args, account_id=None, input=None, timeout=300, cwd=None):
    """(exit code, stdout, stderr) with colours stripped. Runs in cloud/ by default."""
    cmd = wrangler_cmd()
    if not cmd:
        need("wrangler", wrangler_need())
    try:
        p = subprocess.run(cmd + args, cwd=cwd or CLOUD, env=wrangler_env(account_id), input=input,
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "", "wrangler %s timed out after %d s" % (" ".join(args[:3]), timeout)
    return p.returncode, ANSI.sub("", p.stdout or ""), ANSI.sub("", p.stderr or "")


def wrangler_json(args, account_id=None):
    code, out, err = wrangler(args, account_id)
    if code != 0:
        return None, out + err
    start = min([i for i in (out.find("{"), out.find("[")) if i >= 0] or [-1])
    if start < 0:
        return None, out + err
    try:
        return json.loads(out[start:]), ""
    except ValueError:
        return None, out + err


def error_text(text, limit=600):
    """Wrangler's error, without its banner, log-file line, or 'open an issue' footer."""
    keep = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith(("⛅️", "───", "🪵", "Cloudflare collects")) or "open an issue" in s:
            continue
        keep.append(s)
    return " ".join(keep)[-limit:]


def code_in(text):
    return [int(c) for c in re.findall(r"\[code: (\d+)\]", text)]


def node_need():
    return {"what": "node",
            "tell": "Wrangler, Cloudflare's setup tool, runs on Node.js %d or newer, which isn't on this "
                    "Mac yet. The easiest way is the macOS installer (the LTS version) from nodejs.org." % NODE_MAJOR,
            "url": "https://nodejs.org/en/download"}


def wrangler_need():
    return {"what": "wrangler",
            "tell": "Wrangler, Cloudflare's setup tool, isn't installed yet. `library.py install-wrangler` "
                    "installs version %s (about 210 MB) into %s; nothing is installed system-wide."
                    % (WRANGLER_VERSION, WRANGLER_HOME)}


def login_need():
    return {"what": "login",
            "tell": "Wrangler isn't logged in to Cloudflare. `library.py login` opens the browser; the "
                    "person signs in to Cloudflare there and clicks Allow."}


# ---------------------------------------------------------------------------
# Commands


def cmd_status(args):
    cfg = publish.read_config(config_path(args))
    nv = node_version()
    wv = None
    cmd = wrangler_cmd()
    if cmd:
        try:
            pkg = os.path.join(WRANGLER_HOME, "node_modules", "wrangler", "package.json")
            with open(pkg, encoding="utf-8") as f:
                wv = json.load(f).get("version")
        except (OSError, ValueError):
            wv = "installed"
    emit({
        "library": {"configured": bool(cfg.get("url") and cfg.get("token")), "url": cfg.get("url"),
                    "worker": cfg.get("worker"), "authMode": cfg.get("authMode"),
                    "passphraseSet": cfg.get("passphraseSet"), "deployedAt": cfg.get("deployedAt"),
                    "deployedVersion": cfg.get("deployedVersion")},
        "node": nv[2] if nv else None,
        "nodeOk": bool(nv and nv[0] >= NODE_MAJOR),
        "wrangler": wv,
        "configFile": config_path(args),
        "version": read_version(),
    })


def read_version():
    try:
        with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def cmd_install_wrangler(args):
    nv = node_version()
    if not nv or nv[0] < NODE_MAJOR:
        need("install-wrangler", dict(node_need(), found=nv[2] if nv else None))
    npm = shutil.which("npm")
    if not npm:
        need("install-wrangler", dict(node_need(), tell="npm (part of Node.js) isn't on the PATH. Reinstall Node.js from nodejs.org."))
    os.makedirs(WRANGLER_HOME, exist_ok=True)
    pkg = os.path.join(WRANGLER_HOME, "package.json")
    if not os.path.exists(pkg):
        with open(pkg, "w", encoding="utf-8") as f:
            json.dump({"name": "entalkenator-wrangler", "private": True,
                       "description": "Cloudflare Wrangler, used to deploy the enTalkenator library"}, f, indent=2)
    log("Installing wrangler@%s into %s ..." % (WRANGLER_VERSION, WRANGLER_HOME))
    p = subprocess.run([npm, "install", "--no-fund", "--no-audit", "--save-exact", "wrangler@" + WRANGLER_VERSION],
                       cwd=WRANGLER_HOME, capture_output=True, text=True)
    if p.returncode != 0:
        done({"ok": False, "stage": "install-wrangler", "error": error_text(p.stdout + p.stderr, 1500)}, 1)
    code, out, err = wrangler(["--version"], cwd=WRANGLER_HOME)
    emit({"ok": code == 0, "wrangler": out.strip().splitlines()[-1] if out.strip() else None, "location": WRANGLER_HOME})


def whoami():
    data, err = wrangler_json(["whoami", "--json"])
    if not data or not data.get("loggedIn"):
        return None
    return data


def cmd_login(args):
    if not wrangler_cmd():
        need("login", wrangler_need())
    who = whoami()
    if who and not args.again:
        done({"ok": True, "alreadyLoggedIn": True, "accounts": accounts_of(who)})
    log("Opening the browser. Sign in to Cloudflare there and click Allow; this waits until you do.")
    cmd = wrangler_cmd() + ["login"]
    # Not captured: Wrangler prints the sign-in link in case the browser doesn't open.
    p = subprocess.run(cmd, cwd=CLOUD, env=wrangler_env(), stdout=sys.stderr, stderr=sys.stderr, timeout=args.timeout)
    who = whoami()
    if not who:
        done({"ok": False, "stage": "login", "exit": p.returncode,
              "tell": "The login didn't finish. Try again, and click Allow on the Cloudflare page."}, 1)
    emit({"ok": True, "accounts": accounts_of(who)})


def accounts_of(who):
    return [{"id": a.get("id"), "name": a.get("name")} for a in (who.get("accounts") or [])]


def pick_account(args, cfg, who):
    accounts = accounts_of(who)
    wanted = args.account_id or cfg.get("accountId")
    if wanted:
        for a in accounts:
            if a["id"] == wanted:
                return a
        need("preflight", {"what": "account", "tell": "Account %s isn't one this login can use." % wanted,
                           "accounts": accounts})
    if len(accounts) == 1:
        return accounts[0]
    if not accounts:
        need("preflight", {"what": "account", "tell": "This Cloudflare login has no accounts."})
    need("preflight", {"what": "account",
                       "tell": "This login can use several Cloudflare accounts. Ask which one, then re-run with --account-id.",
                       "accounts": accounts})


def names(args, cfg):
    name = (args.name or cfg.get("worker") or DEFAULT_NAME).strip().lower()
    if not NAME_RE.match(name):
        done({"ok": False, "error": "The name %r won't work. Use 3 to 63 lowercase letters, digits and hyphens, "
                                    "starting and ending with a letter or digit." % name}, 1)
    return name, cfg.get("bucket") or name, cfg.get("database") or name


def r2_state(bucket, account_id):
    """'exists', 'missing', or ('off', message) when R2 isn't switched on for the account."""
    code, out, err = wrangler(["r2", "bucket", "info", bucket, "--json"], account_id, timeout=120)
    text = out + err
    if code == 0:
        return "exists", None
    codes = code_in(text)
    if 10006 in codes:
        return "missing", None
    if 10042 in codes or re.search(r"enable R2|R2 is not enabled|purchase R2", text, re.I):
        return "off", error_text(text)
    return "error", error_text(text)


def d1_find(database, account_id):
    data, err = wrangler_json(["d1", "list", "--json"], account_id)
    if data is None:
        return "error", error_text(err)
    for db in data:
        if db.get("name") == database:
            return "exists", db.get("uuid")
    return "missing", None


def worker_exists(name, account_id):
    code, out, err = wrangler(["deployments", "list", "--name", name, "--json"], account_id, timeout=120)
    if code == 0:
        return True
    if 10007 in code_in(out + err):
        return False
    return None


def r2_need(account_id):
    return {"what": "r2",
            "tell": "R2, Cloudflare's file storage, isn't switched on for this account yet. In the dashboard, "
                    "open R2 Object Storage and follow the steps to turn it on (Cloudflare calls it "
                    "purchasing the R2 plan). The free allowance covers 10 GB a month, about 650 twenty-minute "
                    "programs. Cloudflare may ask for a card or PayPal before switching it on; the person "
                    "enters that themselves. Using R2 within the free allowance costs nothing.",
            "url": DASH + account_id + "/r2/overview"}


def cmd_preflight(args):
    nv = node_version()
    if not nv or nv[0] < NODE_MAJOR:
        need("preflight", node_need())
    if not wrangler_cmd():
        need("preflight", wrangler_need())
    who = whoami()
    if not who:
        need("preflight", login_need())
    cfg = publish.read_config(config_path(args))
    account = pick_account(args, cfg, who)
    name, bucket, database = names(args, cfg)
    r2, r2_err = r2_state(bucket, account["id"])
    d1, d1_id = d1_find(database, account["id"])
    exists = worker_exists(name, account["id"])
    ours = cfg.get("worker") == name and cfg.get("accountId") == account["id"]
    needs = []
    if r2 == "off":
        needs.append(r2_need(account["id"]))
    elif r2 == "error":
        needs.append({"what": "r2-error", "tell": "Couldn't check R2: " + (r2_err or "unknown error")})
    if exists and not ours:
        needs.append({"what": "name-taken",
                      "tell": "This account already has a Worker called %s that this computer didn't set up. If "
                              "it's an enTalkenator library from an earlier setup, deploying over it keeps its "
                              "programs; pass --adopt. Otherwise choose another name with --name." % name})
    emit({"ok": not needs, "account": account, "loggedInAs": who.get("email"),
          "name": name, "bucket": {"name": bucket, "state": r2},
          "database": {"name": database, "state": d1, "id": d1_id},
          "worker": {"exists": exists, "setUpHere": ours},
          "needs": needs})


# ---------------------------------------------------------------------------
# Deploy


def load_jsonc(text):
    """JSON with // and /* */ comments and trailing commas (wrangler.jsonc)."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
        else:
            out.append(c)
            i += 1
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", "".join(out)))


def write_deploy_config(name, bucket, database, database_id, account_id, settings):
    with open(os.path.join(CLOUD, "wrangler.jsonc"), encoding="utf-8") as f:
        base = load_jsonc(f.read())
    base.pop("$schema", None)
    # Absolute paths, so the config can live outside this folder (which a new download replaces).
    base["main"] = os.path.join(CLOUD, base["main"])
    base["assets"] = dict(base["assets"], directory=os.path.normpath(os.path.join(CLOUD, base["assets"]["directory"])))
    base["name"] = name
    base["account_id"] = account_id
    base["r2_buckets"] = [dict(base["r2_buckets"][0], bucket_name=bucket)]
    base["d1_databases"] = [dict(base["d1_databases"][0], database_name=database, database_id=database_id,
                                 migrations_dir=os.path.join(CLOUD, base["d1_databases"][0]["migrations_dir"]))]
    base["vars"] = dict(base.get("vars") or {}, **settings)
    path = DEPLOY_CONFIG
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("// Written by scripts/library.py deploy from wrangler.jsonc. Don't edit; it's rewritten on every deploy.\n")
        json.dump(base, f, indent=2)
        f.write("\n")
    return path


def worker_url(text, name):
    m = re.search(r"https://%s\.[a-z0-9-]+\.workers\.dev" % re.escape(name), text)
    return m.group(0) if m else None


def wait_until_up(url, seconds=None):
    """New workers.dev addresses take a few seconds to start answering."""
    if seconds is None:
        seconds = float(os.environ.get("ENTALKENATOR_DEPLOY_WAIT", "90"))
    deadline = time.time() + seconds
    while True:
        status, _, _ = publish.fetch_plain(url + "/robots.txt")
        if status == 200 or time.time() > deadline:
            return status == 200
        time.sleep(3)


def secret_names(name, account_id):
    data, err = wrangler_json(["secret", "list", "--name", name, "--format", "json"], account_id)
    if data is None:
        return None
    return {s.get("name") for s in data if isinstance(s, dict)}


def put_secrets(name, values, account_id):
    code, out, err = wrangler(["secret", "bulk", "--name", name], account_id, input=json.dumps(values))
    if code != 0:
        # Wrangler's own messages; the values only went to its stdin.
        done({"ok": False, "stage": "secrets", "error": error_text(out + err)}, 1)


def cmd_deploy(args):
    path = config_path(args)
    cfg = publish.read_config(path)
    nv = node_version()
    if not nv or nv[0] < NODE_MAJOR:
        need("deploy", node_need())
    if not wrangler_cmd():
        need("deploy", wrangler_need())
    who = whoami()
    if not who:
        need("deploy", login_need())
    account = pick_account(args, cfg, who)
    aid = account["id"]
    name, bucket, database = names(args, cfg)
    ours = cfg.get("worker") == name and cfg.get("accountId") == aid
    steps = []

    exists = worker_exists(name, aid)
    if exists and not ours and not args.adopt:
        need("deploy", {"what": "name-taken",
                        "tell": "This account already has a Worker called %s. Choose another name with --name, or "
                                "pass --adopt if it's an enTalkenator library from an earlier setup." % name})

    r2, r2_err = r2_state(bucket, aid)
    if r2 == "off":
        need("deploy", r2_need(aid))
    if r2 == "error":
        done({"ok": False, "stage": "r2", "error": r2_err}, 1)
    if r2 == "missing":
        log("Creating the R2 bucket %s ..." % bucket)
        code, out, err = wrangler(["r2", "bucket", "create", bucket], aid)
        if code != 0:
            if 10042 in code_in(out + err):
                need("deploy", r2_need(aid))
            done({"ok": False, "stage": "r2-create", "error": error_text(out + err)}, 1)
        steps.append("created R2 bucket " + bucket)

    d1, d1_id = d1_find(database, aid)
    if d1 == "error":
        done({"ok": False, "stage": "d1", "error": d1_id}, 1)
    if d1 == "missing":
        log("Creating the D1 database %s ..." % database)
        code, out, err = wrangler(["d1", "create", database], aid)
        if code != 0:
            done({"ok": False, "stage": "d1-create", "error": error_text(out + err)}, 1)
        d1, d1_id = d1_find(database, aid)
        if not d1_id:
            done({"ok": False, "stage": "d1-create", "error": "Created the database but couldn't find its ID."}, 1)
        steps.append("created D1 database " + database)

    auth = args.auth or cfg.get("authMode") or "passphrase"
    settings = {
        "AUTH_MODE": auth,
        "LIBRARY_TITLE": args.title or cfg.get("title") or DEFAULT_TITLE,
        "ACCESS_TEAM_DOMAIN": (args.access_team_domain if args.access_team_domain is not None else cfg.get("accessTeamDomain")) or "",
        "ACCESS_AUD": (args.access_aud if args.access_aud is not None else cfg.get("accessAud")) or "",
    }
    if auth == "access" and not (settings["ACCESS_TEAM_DOMAIN"] and settings["ACCESS_AUD"]):
        done({"ok": False, "error": "Access mode needs --access-team-domain and --access-aud (see the setup-library skill)."}, 1)
    write_deploy_config(name, bucket, database, d1_id, aid, settings)

    log("Uploading the Worker %s ..." % name)
    code, out, err = wrangler(["deploy", "--config", DEPLOY_CONFIG], aid, timeout=600)
    text = out + err
    if code != 0:
        if re.search(r"workers\.dev subdomain", text, re.I):
            need("deploy", {"what": "subdomain",
                            "tell": "This account doesn't have a workers.dev address yet. In the dashboard, open "
                                    "Workers & Pages; the first visit asks you to choose a subdomain (any free name; "
                                    "it becomes part of the library's address). Then deploy again.",
                            "url": DASH + aid + "/workers-and-pages"})
        if 10042 in code_in(text):
            need("deploy", r2_need(aid))
        done({"ok": False, "stage": "deploy", "error": error_text(text, 1500)}, 1)
    url = worker_url(text, name) or cfg.get("url")
    if not url:
        done({"ok": False, "stage": "deploy", "error": "Deployed, but couldn't find the workers.dev address in "
                                                        "Wrangler's output: " + error_text(text)}, 1)
    steps.append("deployed the Worker")

    # The Worker also creates its tables on first use, so a failure here isn't fatal.
    code, out, err = wrangler(["d1", "migrations", "apply", "DB", "--remote", "--config", DEPLOY_CONFIG], aid)
    migrations = "applied" if code == 0 else "skipped: " + error_text(out + err, 300)

    have = secret_names(name, aid)
    if have is None:
        done({"ok": False, "stage": "secrets", "error": "Couldn't list the Worker's secrets."}, 1)
    new = {}
    if "UPLOAD_TOKEN" not in have or not cfg.get("token") or not ours:
        new["UPLOAD_TOKEN"] = secrets.token_urlsafe(32)
    if "FEED_TOKEN" not in have or args.new_feed_address:
        new["FEED_TOKEN"] = secrets.token_urlsafe(32)
    if new:
        put_secrets(name, new, aid)
        steps.append("set " + " and ".join(sorted(new)))
    passphrase_set = "APP_PASSPHRASE" in have

    cfg.update({
        "url": url, "worker": name, "accountId": aid, "bucket": bucket, "database": database,
        "databaseId": d1_id, "authMode": auth, "title": settings["LIBRARY_TITLE"],
        "accessTeamDomain": settings["ACCESS_TEAM_DOMAIN"], "accessAud": settings["ACCESS_AUD"],
        "passphraseSet": passphrase_set, "deployedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "deployedVersion": read_version(), "source": "deploy",
    })
    if "UPLOAD_TOKEN" in new:
        cfg["token"] = new["UPLOAD_TOKEN"]
    publish.write_config(path, cfg)

    up = wait_until_up(url)
    nxt = []
    if auth == "passphrase" and not passphrase_set:
        nxt.append("passphrase")
    nxt.append("check")
    emit({"ok": True, "url": url, "appUrl": url + "/app/", "account": account["name"], "steps": steps,
          "migrations": migrations, "answering": up, "authMode": auth, "passphraseSet": passphrase_set,
          "feedAddressChanged": "FEED_TOKEN" in new and "FEED_TOKEN" in have, "configFile": path, "next": nxt,
          "note": "Tokens went straight to Cloudflare and the config file; they are not shown."})


# ---------------------------------------------------------------------------
# Passphrase


def cmd_passphrase(args):
    path = config_path(args)
    cfg = publish.read_config(path)
    if not cfg.get("worker"):
        done({"ok": False, "error": "No library has been deployed from this computer yet. Run deploy first."}, 1)
    if not dialog.available():
        done({"ok": False, "stage": "passphrase", "needs": [{
            "what": "terminal",
            "tell": "Ask the person to run this in their own terminal and type the passphrase when asked:",
            "command": "%s secret put APP_PASSPHRASE --name %s" % (
                " ".join(shlex.quote(c) for c in wrangler_cmd() or ["npx", "wrangler"]), shlex.quote(cfg["worker"]))}]}, 2)
    first = "Choose a passphrase for your enTalkenator library: at least 10 characters. A few words is easiest to type on a phone."
    prompt = first
    for _ in range(4):
        one = dialog.ask_secret(prompt)
        if one is None:
            done({"ok": False, "cancelled": True, "tell": "The passphrase dialog was cancelled; nothing changed."}, 1)
        if len(one.strip()) < 10:
            prompt = "That's shorter than 10 characters. " + first
            continue
        two = dialog.ask_secret("Type the same passphrase again to confirm it.")
        if two is None:
            done({"ok": False, "cancelled": True, "tell": "The passphrase dialog was cancelled; nothing changed."}, 1)
        if one == two:
            break
        prompt = "The two didn't match. " + first
    else:
        done({"ok": False, "tell": "No passphrase was set after several tries."}, 1)
    put_secrets(cfg["worker"], {"APP_PASSPHRASE": one}, cfg.get("accountId"))
    cfg["passphraseSet"] = True
    publish.write_config(path, cfg)
    emit({"ok": True, "set": "APP_PASSPHRASE", "note": "The passphrase went straight to Cloudflare; it isn't stored "
          "on this computer or shown here. Signing in once lasts 180 days on each device."})


def cmd_open(args):
    cfg = publish.read_config(config_path(args))
    if not cfg.get("url"):
        done({"ok": False, "error": "No library is set up yet."}, 1)
    page = cfg["url"] + "/app/" + (args.page or "")
    if sys.platform == "darwin":
        subprocess.run(["open", page])
    emit({"ok": True, "opened": page})


# ---------------------------------------------------------------------------
# Remove


def cmd_remove(args):
    path = config_path(args)
    cfg = publish.read_config(path)
    if not cfg.get("worker") or not cfg.get("accountId"):
        done({"ok": False, "error": "This computer has no record of a deployed library, so there's nothing it "
                                    "can safely remove. Delete it in the Cloudflare dashboard instead."}, 1)
    if not args.yes:
        done({"ok": False, "error": "This deletes every program in the library, its podcast feeds, the web app, "
                                    "the R2 bucket and the database, for good. Re-run with --yes to confirm."}, 1)
    aid, name = cfg["accountId"], cfg["worker"]
    report = {"worker": name, "bucket": cfg.get("bucket"), "database": cfg.get("database")}

    # The Worker's own API deletes each program with its files, which empties the bucket.
    deleted = 0
    if cfg.get("url") and cfg.get("token"):
        try:
            items = publish.request(cfg["url"], cfg["token"], "GET", "items", retries=1)["items"]
            for it in items:
                publish.request(cfg["url"], cfg["token"], "DELETE", "items/" + urllib.parse.quote(it["id"], safe=""), retries=2)
                deleted += 1
        except (publish.ApiError, KeyError) as e:
            report["itemsError"] = str(e)
    report["programsDeleted"] = deleted

    code, out, err = wrangler(["delete", "--name", name, "--force"], aid)
    report["workerDeleted"] = code == 0 or 10007 in code_in(out + err)
    if not report["workerDeleted"]:
        report["workerError"] = error_text(out + err)

    if cfg.get("bucket"):
        code, out, err = wrangler(["r2", "bucket", "delete", cfg["bucket"]], aid)
        report["bucketDeleted"] = code == 0 or 10006 in code_in(out + err)
        if not report["bucketDeleted"]:
            report["bucketError"] = error_text(out + err) + " (A bucket must be empty before it can be deleted; " \
                                                            "empty it in the dashboard if files remain.)"
    if cfg.get("database"):
        code, out, err = wrangler(["d1", "delete", cfg["database"], "--skip-confirmation"], aid)
        report["databaseDeleted"] = code == 0 or "Couldn't find" in (out + err)
        if not report["databaseDeleted"]:
            report["databaseError"] = error_text(out + err)

    ok = report["workerDeleted"] and report.get("bucketDeleted", True) and report.get("databaseDeleted", True)
    if ok:
        os.remove(path)
        try:
            os.remove(DEPLOY_CONFIG)
        except OSError:
            pass
    report["ok"] = ok
    report["configRemoved"] = ok
    emit(report)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="settings file (default ~/.config/entalkenator/library.json)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("status", parents=[common])
    p.set_defaults(func=cmd_status)
    p = sub.add_parser("install-wrangler", parents=[common])
    p.set_defaults(func=cmd_install_wrangler)
    p = sub.add_parser("login", parents=[common])
    p.add_argument("--again", action="store_true", help="log in even if already logged in")
    p.add_argument("--timeout", type=int, default=600)
    p.set_defaults(func=cmd_login)

    target = argparse.ArgumentParser(add_help=False)
    target.add_argument("--account-id", help="which Cloudflare account, if the login has several")
    target.add_argument("--name", help="the Worker's name, part of the address (default %s)" % DEFAULT_NAME)

    p = sub.add_parser("preflight", parents=[common, target])
    p.set_defaults(func=cmd_preflight)

    p = sub.add_parser("deploy", parents=[common, target])
    p.add_argument("--title", help="the library's name in podcast apps (default %r)" % DEFAULT_TITLE)
    p.add_argument("--auth", choices=["passphrase", "access"], help="how the web app is protected (default passphrase)")
    p.add_argument("--access-team-domain", help="for --auth access: https://<team>.cloudflareaccess.com")
    p.add_argument("--access-aud", help="for --auth access: the Access application's AUD tag")
    p.add_argument("--adopt", action="store_true", help="deploy over an existing Worker of that name")
    p.add_argument("--new-feed-address", action="store_true",
                   help="change the secret part of the feed address (old subscriptions stop working)")
    p.set_defaults(func=cmd_deploy)

    p = sub.add_parser("passphrase", parents=[common])
    p.set_defaults(func=cmd_passphrase)
    p = sub.add_parser("open", parents=[common])
    p.add_argument("page", nargs="?", help="e.g. '#/feeds' for the podcast feeds page")
    p.set_defaults(func=cmd_open)
    p = sub.add_parser("remove", parents=[common])
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_remove)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
