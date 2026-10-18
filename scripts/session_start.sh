#!/bin/bash
# SessionStart hook: tells Claude what's set up, so it can greet a first-time visitor.
# Never fails and never blocks. On a Mac without the Command Line Tools, /usr/bin/python3
# is only a stub that pops up an installer, so it isn't run in that case.
DIR="$(cd "$(dirname "$0")/.." && pwd)"
if [ "$(uname -s)" = "Darwin" ] && ! xcode-select -p >/dev/null 2>&1 \
   && [ "$(command -v python3)" = "/usr/bin/python3" ]; then
    echo "enTalkenator: Python isn't available yet. On this Mac it comes with Apple's Command Line Tools:"
    echo "the person runs 'xcode-select --install' (or anything that asks for them) and clicks Install."
    echo "Nothing else in this folder works until then, so help with that first."
    exit 0
fi
python3 "$DIR/scripts/status.py" --hook 2>/dev/null || echo "enTalkenator: couldn't read the setup status."
exit 0
