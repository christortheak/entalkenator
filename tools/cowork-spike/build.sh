#!/bin/bash
# Builds the Mac-check test plugin: writes its .mcp.json (with an inline copy of the server, in
# case ${CLAUDE_PLUGIN_ROOT} isn't expanded) and zips it to dist/entalkenator-mac-check.zip.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
P="$HERE/entalkenator-mac-check"
OUT="$HERE/../../dist"
mkdir -p "$OUT"
/usr/bin/python3 - "$P" <<'PY'
import json, sys, os
p = sys.argv[1]
code = open(os.path.join(p, "server", "mac_check.py")).read()
cfg = {"mcpServers": {
    "mac-check": {"type": "stdio", "command": "python3", "args": ["${CLAUDE_PLUGIN_ROOT}/server/mac_check.py"]},
    "mac-check-inline": {"type": "stdio", "command": "/usr/bin/python3", "args": ["-c", code]},
}}
with open(os.path.join(p, ".mcp.json"), "w") as f:
    json.dump(cfg, f, indent=2)
PY
rm -f "$OUT/entalkenator-mac-check.zip"
(cd "$P" && zip -qr -X "$OUT/entalkenator-mac-check.zip" . -x '*/__pycache__/*' '*.DS_Store')
echo "$OUT/entalkenator-mac-check.zip"
