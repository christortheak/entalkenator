#!/usr/bin/env python3
"""A stand-in for Wrangler, for testing scripts/library.py without a Cloudflare account.

The account is a JSON file named by $FAKE_WRANGLER_STATE:
  {"loggedIn": true, "accounts": [{"id": "...", "name": "..."}], "r2": true, "subdomain": "sub",
   "buckets": [...], "databases": [{"name": ..., "uuid": ...}], "workers": {"name": {"secrets": {...}}}}
Every call is appended to "calls". Output imitates Wrangler's, errors with "[code: N]" included.
"""

import json
import os
import re
import sys
import uuid

path = os.environ["FAKE_WRANGLER_STATE"]
with open(path) as f:
    st = json.load(f)
args = sys.argv[1:]
st.setdefault("calls", []).append(args)


def save():
    with open(path, "w") as f:
        json.dump(st, f, indent=2)


def out(text, code=0):
    save()
    print(text)
    sys.exit(code)


def err(text, code=1):
    save()
    print("✘ [ERROR] " + text, file=sys.stderr)
    print("🪵  Logs were written to \"/tmp/fake.log\"", file=sys.stderr)
    sys.exit(code)


def opt(name):
    return args[args.index(name) + 1] if name in args else None


def deploy_config():
    cfg = opt("--config")
    if not cfg:
        return None
    with open(cfg) as f:
        text = re.sub(r"^\s*//.*$", "", f.read(), flags=re.M)
    return json.loads(text)


if args[:1] == ["whoami"]:
    if not st.get("loggedIn"):
        err("You are not authenticated. Please run `wrangler login`.")
    out(json.dumps({"loggedIn": True, "authType": "OAuth Token", "email": "person@example.com",
                    "accounts": st["accounts"]}))

if not st.get("loggedIn"):
    err("Not logged in.")
if len(st["accounts"]) > 1 and not os.environ.get("CLOUDFLARE_ACCOUNT_ID"):
    err("More than one account available but unable to select one in non-interactive mode.")

if args[:3] == ["r2", "bucket", "info"]:
    if not st.get("r2"):
        err("A request to the Cloudflare API failed.\n  Please enable R2 through the Cloudflare Dashboard. [code: 10042]")
    if args[3] in st["buckets"]:
        out(json.dumps({"name": args[3], "object_count": "0"}))
    err("A request to the Cloudflare API failed.\n  The specified bucket does not exist. [code: 10006]")
if args[:3] == ["r2", "bucket", "create"]:
    if not st.get("r2"):
        err("Please enable R2 through the Cloudflare Dashboard. [code: 10042]")
    st["buckets"].append(args[3])
    out("Creating bucket '%s'...\n✅ Created bucket '%s'" % (args[3], args[3]))
if args[:3] == ["r2", "bucket", "delete"]:
    if args[3] not in st["buckets"]:
        err("The specified bucket does not exist. [code: 10006]")
    st["buckets"].remove(args[3])
    out("Deleted bucket %s" % args[3])

if args[:2] == ["d1", "list"]:
    out(json.dumps(st["databases"]))
if args[:2] == ["d1", "create"]:
    st["databases"].append({"name": args[2], "uuid": str(uuid.uuid4())})
    out("✅ Successfully created DB '%s'" % args[2])
if args[:3] == ["d1", "migrations", "apply"]:
    out("✅ Migrations applied")
if args[:2] == ["d1", "delete"]:
    before = len(st["databases"])
    st["databases"] = [d for d in st["databases"] if d["name"] != args[2]]
    if len(st["databases"]) == before:
        err("Couldn't find a D1 DB with name or binding '%s'" % args[2])
    out("Deleted '%s' successfully." % args[2])

if args[:2] == ["deployments", "list"]:
    if opt("--name") in st["workers"]:
        out(json.dumps([{"id": "v1"}]))
    err("A request to the Cloudflare API failed.\n  This Worker does not exist on your account. [code: 10007]")

if args[:1] == ["deploy"]:
    cfg = deploy_config()
    if not st.get("subdomain"):
        err("You need to register a workers.dev subdomain before publishing to workers.dev")
    st["workers"].setdefault(cfg["name"], {"secrets": {}})
    st["workers"][cfg["name"]]["vars"] = cfg.get("vars")
    st["workers"][cfg["name"]]["d1"] = cfg["d1_databases"][0].get("database_id")
    out("Uploaded %s\nDeployed %s triggers\n  https://%s.%s.workers.dev\nCurrent Version ID: 1"
        % (cfg["name"], cfg["name"], cfg["name"], st["subdomain"]))

if args[:2] == ["secret", "list"]:
    name = opt("--name")
    if name not in st["workers"]:
        err("This Worker does not exist on your account. [code: 10007]")
    out(json.dumps([{"name": k, "type": "secret_text"} for k in sorted(st["workers"][name]["secrets"])]))
if args[:2] == ["secret", "bulk"]:
    name = opt("--name")
    if name not in st["workers"]:
        err("This Worker does not exist on your account. [code: 10007]")
    values = json.loads(sys.stdin.read())
    st["workers"][name]["secrets"].update(values)
    out("✨ %d secrets successfully uploaded" % len(values))

if args[:1] == ["delete"]:
    name = opt("--name")
    if name not in st["workers"]:
        err("This Worker does not exist on your account. [code: 10007]")
    del st["workers"][name]
    out("Successfully deleted %s" % name)

if args[:1] == ["--version"]:
    out("4.145.0")

err("fake_wrangler: unhandled command %r" % (args,), 99)
