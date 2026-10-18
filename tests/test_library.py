#!/usr/bin/env python3
"""Checks scripts/library.py's setup paths against a stand-in Wrangler (tests/fake_wrangler.py),
including the ones a set-up account can't show: no login, several accounts, R2 switched off,
no workers.dev subdomain, a name that's taken, updates, a new feed address, and removal.

    python3 tests/test_library.py

Needs Node.js on the PATH (library.py checks for it). Touches no real account or config.
"""

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LIB = os.path.join(ROOT, "scripts", "library.py")
FAKE = os.path.join(HERE, "fake_wrangler.py")

passed, failed = 0, []


def check(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print("  ok   " + name)
    else:
        failed.append(name)
        print("  FAIL %s %s" % (name, detail))


class Account:
    def __init__(self, tmp, **state):
        os.makedirs(tmp, exist_ok=True)
        self.tmp = tmp
        self.state_file = os.path.join(tmp, "account.json")
        self.config = os.path.join(tmp, "library.json")
        base = {"loggedIn": True, "accounts": [{"id": "acct1", "name": "Person's Account"}], "r2": True,
                "subdomain": "person", "buckets": [], "databases": [], "workers": {}}
        base.update(state)
        self.save(base)

    def load(self):
        with open(self.state_file) as f:
            return json.load(f)

    def save(self, st):
        with open(self.state_file, "w") as f:
            json.dump(st, f)

    def update(self, **kw):
        st = self.load()
        st.update(kw)
        self.save(st)

    def lib(self, *args):
        env = dict(os.environ, FAKE_WRANGLER_STATE=self.state_file, ENTALKENATOR_DEPLOY_WAIT="0",
                   ENTALKENATOR_HOME=os.path.join(self.tmp, "home"),
                   ENTALKENATOR_WRANGLER=" ".join(shlex.quote(x) for x in (sys.executable, FAKE)))
        p = subprocess.run([sys.executable, LIB] + list(args) + ["--config", self.config],
                           capture_output=True, text=True, env=env, cwd=self.tmp)
        try:
            return p.returncode, json.loads(p.stdout), p.stdout
        except ValueError:
            return p.returncode, {}, p.stdout + p.stderr

    def cfg(self):
        try:
            with open(self.config) as f:
                return json.load(f)
        except OSError:
            return None


def needs(out):
    return [n["what"] for n in out.get("needs", [])]


def main():
    if not shutil.which("node"):
        raise SystemExit("Node.js isn't on the PATH; library.py's checks need it.")
    tmp = tempfile.mkdtemp(prefix="entalk-lib-")
    try:
        print("not logged in")
        a = Account(os.path.join(tmp, "a"), loggedIn=False)
        code, out, _ = a.lib("preflight")
        check("preflight asks for a login", code == 2 and needs(out) == ["login"], out)
        code, out, _ = a.lib("deploy")
        check("deploy asks for a login", code == 2 and needs(out) == ["login"], out)

        print("several accounts")
        b = Account(os.path.join(tmp, "b"), accounts=[{"id": "acct1", "name": "Home"}, {"id": "acct2", "name": "Work"}])
        code, out, _ = b.lib("preflight")
        check("preflight lists the accounts to choose from",
              code == 2 and needs(out) == ["account"] and len(out["needs"][0]["accounts"]) == 2, out)
        code, out, _ = b.lib("preflight", "--account-id", "acct2")
        check("--account-id picks one", code == 0 and out["account"]["id"] == "acct2", out)
        code, out, _ = b.lib("deploy", "--account-id", "acct2")
        check("deploy with --account-id works", code == 0 and out.get("ok"), out)
        code, out, _ = b.lib("deploy")
        check("the account is remembered for the next deploy", code == 0 and out.get("ok"), out)

        print("R2 switched off")
        c = Account(os.path.join(tmp, "c"), r2=False)
        code, out, _ = c.lib("preflight")
        check("preflight says R2 needs switching on, with a dashboard link",
              out.get("ok") is False and needs(out) == ["r2"], out)
        check("the link points at this account's R2 page", "acct1/r2" in (out.get("needs") or [{}])[0].get("url", ""), out)
        code, out, _ = c.lib("deploy")
        check("deploy stops before creating anything", code == 2 and needs(out) == ["r2"] and not c.load()["databases"], out)
        c.update(r2=True)
        code, out, _ = c.lib("deploy")
        check("deploy works once R2 is on", code == 0 and out.get("ok"), out)

        print("no workers.dev subdomain")
        d = Account(os.path.join(tmp, "d"), subdomain=None)
        code, out, _ = d.lib("deploy")
        check("deploy asks for a subdomain, with a link", code == 2 and needs(out) == ["subdomain"]
              and "workers-and-pages" in out["needs"][0]["url"], out)
        st = d.load()
        check("the bucket and database were created and are reused next time",
              st["buckets"] == ["entalkenator-library"] and len(st["databases"]) == 1)
        d.update(subdomain="person")
        code, out, _ = d.lib("deploy")
        st = d.load()
        check("deploy works once the subdomain exists, without duplicating anything",
              code == 0 and out["url"] == "https://entalkenator-library.person.workers.dev"
              and len(st["databases"]) == 1 and st["buckets"] == ["entalkenator-library"], out)

        print("first deploy")
        e = Account(os.path.join(tmp, "e"))
        code, out, raw = e.lib("deploy", "--title", "My Library")
        st = e.load()
        w = st["workers"].get("entalkenator-library", {})
        cfg = e.cfg() or {}
        check("creates bucket and database, deploys, sets both tokens",
              code == 0 and out["steps"] == ["created R2 bucket entalkenator-library", "created D1 database entalkenator-library",
                                             "deployed the Worker", "set FEED_TOKEN and UPLOAD_TOKEN"], out)
        check("the deploy config carries the database id",
              w.get("d1") == st["databases"][0]["uuid"], w)
        dc = os.path.join(e.tmp, "home", "deploy", "wrangler.json")
        with open(dc) as f:
            dcfg = json.loads("\n".join(l for l in f.read().splitlines() if not l.startswith("//")))
        check("the deploy config lives outside the folder, with absolute paths into cloud/",
              dcfg["account_id"] == "acct1" and os.path.isfile(dcfg["main"]) and os.path.isdir(dcfg["assets"]["directory"])
              and os.path.isdir(dcfg["d1_databases"][0]["migrations_dir"]), dcfg)
        check("the title and passphrase mode reach the Worker", (w.get("vars") or {}).get("LIBRARY_TITLE") == "My Library"
              and w["vars"].get("AUTH_MODE") == "passphrase")
        check("saves the address and upload token", cfg.get("url") == out["url"] and cfg.get("token") == w["secrets"]["UPLOAD_TOKEN"])
        check("config file is private (600)", oct(os.stat(e.config).st_mode & 0o777) == "0o600")
        check("no token appears in the output", w["secrets"]["UPLOAD_TOKEN"] not in raw and w["secrets"]["FEED_TOKEN"] not in raw)
        check("next step is the passphrase", out["next"][0] == "passphrase" and out["passphraseSet"] is False, out)
        check("migrations applied", out["migrations"] == "applied")

        print("update (deploy again)")
        feed_before = w["secrets"]["FEED_TOKEN"]
        upload_before = w["secrets"]["UPLOAD_TOKEN"]
        st["workers"]["entalkenator-library"]["secrets"]["APP_PASSPHRASE"] = "set by the person"
        e.save(st)
        code, out, _ = e.lib("deploy")
        st = e.load()
        w = st["workers"]["entalkenator-library"]
        check("re-deploy creates nothing and changes no secret", code == 0 and out["steps"] == ["deployed the Worker"], out)
        check("feed address and upload token are kept", w["secrets"]["FEED_TOKEN"] == feed_before
              and w["secrets"]["UPLOAD_TOKEN"] == upload_before)
        check("title is remembered", w["vars"]["LIBRARY_TITLE"] == "My Library")
        check("passphrase seen as set, so the next step is just the check", out["passphraseSet"] and out["next"] == ["check"], out)

        print("new feed address")
        code, out, _ = e.lib("deploy", "--new-feed-address")
        w = e.load()["workers"]["entalkenator-library"]
        check("only FEED_TOKEN changes", code == 0 and w["secrets"]["FEED_TOKEN"] != feed_before
              and w["secrets"]["UPLOAD_TOKEN"] == upload_before and out["feedAddressChanged"], out)

        print("lost config: a fresh computer, same account")
        os.remove(e.config)
        code, out, _ = e.lib("deploy")
        check("an existing Worker of that name isn't taken over silently", code == 2 and needs(out) == ["name-taken"], out)
        code, out, _ = e.lib("deploy", "--adopt")
        w = e.load()["workers"]["entalkenator-library"]
        check("--adopt deploys, issues a new upload token, keeps the feed address",
              code == 0 and out["steps"] == ["deployed the Worker", "set UPLOAD_TOKEN"]
              and (e.cfg() or {}).get("token") == w["secrets"]["UPLOAD_TOKEN"], out)

        print("bad name")
        code, out, _ = e.lib("deploy", "--name", "My Library!")
        check("an invalid name is refused with an explanation", code == 1 and "won't work" in out.get("error", ""), out)

        print("remove")
        e.update()
        cfg = e.cfg()
        cfg["url"] = "http://127.0.0.1:9"  # nothing listens here, so program deletion reports an error
        with open(e.config, "w") as f:
            json.dump(cfg, f)
        code, out, _ = e.lib("remove")
        check("remove without --yes refuses", code == 1 and "--yes" in out.get("error", ""), out)
        check("...and deletes nothing", "entalkenator-library" in e.load()["workers"])
        code, out, _ = e.lib("remove", "--yes")
        st = e.load()
        check("remove --yes deletes the Worker, bucket and database", code == 0 and out["ok"] and not st["workers"]
              and not st["buckets"] and not st["databases"], out)
        check("...and the local settings", e.cfg() is None)
        code, out, _ = e.lib("remove", "--yes")
        check("remove with nothing recorded refuses", code == 1 and "no record" in out.get("error", ""), out)

        print("publishing within the library's limits")
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import publish
        qwen = ("A warm, precise American woman in her fifties with a low, steady voice; patient and amused, "
                "speaking with the clipped authority of a senior judge.")
        label = publish.fit(qwen, publish.LIMITS["speaker voice"])
        check("a long Qwen3 voice description becomes a label the library accepts, cut at a clause",
              len(qwen) > 120 and len(label) <= 120 and label.endswith("steady voice…"), label)
        check("...and a short one is left alone", publish.fit("bf_emma", 120) == "bf_emma")
        check("a text with no breaks is still cut to the limit", len(publish.fit("x" * 500, 300)) == 300)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d passed, %d failed" % (passed, len(failed)))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
