"""A macOS dialog for typing a secret (a passphrase or an API key) without it passing through Claude.

The answer goes from the dialog straight into the calling script; nothing is printed.
"""

import shutil
import subprocess
import sys

_SCRIPT = [
    "on run argv",
    "  tell me to activate",
    '  set r to display dialog (item 1 of argv) default answer "" with hidden answer with title "enTalkenator" '
    'buttons {"Cancel", "OK"} default button "OK" cancel button "Cancel" with icon note',
    "  return text returned of r",
    "end run",
]


def available():
    return sys.platform == "darwin" and bool(shutil.which("osascript"))


def ask_secret(prompt):
    """The typed text, or None if the person cancelled (or there's no window server)."""
    cmd = ["osascript"]
    for line in _SCRIPT:
        cmd += ["-e", line]
    p = subprocess.run(cmd + [prompt], capture_output=True, text=True)
    if p.returncode != 0:
        return None
    return p.stdout.rstrip("\n")
