#!/usr/bin/env python3
"""Checks that scripts/status.py tells Claude to stop, and where to send the person, when it
isn't running on their Mac (in Cowork's Linux sandbox, say), and reports normally when it is.

    python3 tests/test_status.py

Reads no real settings: the config folder is a temporary one.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STATUS = os.path.join(ROOT, "scripts", "status.py")

passed, failed = 0, []


def check(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print("  ok   " + name)
    else:
        failed.append(name)
        print("  FAIL %s %s" % (name, detail))


# Runs status.py as if on another platform and from another folder, without touching the file.
PRETEND = r"""
import sys
sys.argv = ["status.py"] + sys.argv[1:]
platform, root = %r, %r
sys.path.insert(0, %r)
import status
status.sys.platform = platform
if root:
    status.ROOT = root
status.main()
"""


def run(*args, platform=None, root=None, env=None):
    if platform is None and root is None:
        cmd = [sys.executable, STATUS] + list(args)
    else:
        code = PRETEND % (platform or sys.platform, root, os.path.dirname(STATUS))
        cmd = [sys.executable, "-c", code] + list(args)
    p = subprocess.run(cmd, capture_output=True, text=True, env=env)
    return p.returncode, p.stdout


def main():
    tmp = tempfile.mkdtemp(prefix="entalk-status-")
    env = dict(os.environ, ENTALKENATOR_CONFIG_DIR=tmp, ENTALKENATOR_HOME=tmp)
    try:
        if sys.platform == "darwin":
            code, out = run("--hook", env=env)
            check("on a Mac, the hook reports the setup", code == 0 and "status at session start" in out
                  and "NOT on" not in out, out[:200])

        code, out = run("--hook", platform="linux", env=env)
        check("off a Mac, the hook says to stop and where to go instead",
              code == 0 and "NOT on the person's Mac" in out and "Code tab" in out and "Don't install" in out, out)
        check("...without listing voices or the library to set up", "Voices:" not in out and "Library:" not in out)

        code, out = run(platform="linux", env=env)
        s = json.loads(out)
        check("off a Mac, the JSON says so and carries the start-here steps",
              s.get("mac") is False and s.get("where") == "other" and "Code tab" in (s.get("startHere") or ""), s)

        code, out = run("--hook", platform="linux", root="/sessions/brave-quiet-turing/mnt/enTalkenator", env=env)
        check("in a Cowork mount, the hook names Cowork", "looks like Cowork" in out, out)
        code, out = run(platform="linux", root="/sessions/brave-quiet-turing/mnt/enTalkenator", env=env)
        check("...and the JSON says where: cowork", json.loads(out).get("where") == "cowork", out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d passed, %d failed" % (passed, len(failed)))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
