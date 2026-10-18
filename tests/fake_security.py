#!/usr/bin/env python3
"""A stand-in for macOS `security`, for testing scripts/voices.py without touching a real Keychain.

Items live in the JSON file named by $FAKE_SECURITY_STORE. Every command line it's run with is
appended to "argv" in that file, so tests can check that no secret was ever passed as an argument.
Handles: find-generic-password -s S -a A -w, delete-generic-password -s S -a A, and
`-i` with add-generic-password lines on stdin.
"""

import json
import os
import shlex
import sys

path = os.environ["FAKE_SECURITY_STORE"]
try:
    with open(path) as f:
        st = json.load(f)
except (OSError, ValueError):
    st = {"items": {}, "argv": []}
st["argv"].append(sys.argv[1:])


def save():
    with open(path, "w") as f:
        json.dump(st, f)


def opt(args, name):
    return args[args.index(name) + 1] if name in args else None


def key(args):
    return "%s|%s" % (opt(args, "-s"), opt(args, "-a"))


args = sys.argv[1:]
if args[:1] == ["-i"]:
    for line in sys.stdin.read().splitlines():
        cmd = shlex.split(line)
        if cmd[:1] == ["add-generic-password"]:
            st["items"][key(cmd)] = {"password": opt(cmd, "-w"), "label": opt(cmd, "-l")}
    save()
    sys.exit(0)
if args[:1] == ["find-generic-password"]:
    save()
    item = st["items"].get(key(args))
    if not item:
        print("security: SecKeychainSearchCopyNext: The specified item could not be found in the keychain.", file=sys.stderr)
        sys.exit(44)
    print(item["password"])
    sys.exit(0)
if args[:1] == ["delete-generic-password"]:
    found = st["items"].pop(key(args), None)
    save()
    sys.exit(0 if found else 44)
save()
sys.exit(2)
