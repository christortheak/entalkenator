#!/usr/bin/env python3
"""A throwaway MCP server that answers one question: when Cowork starts a plugin's local
server, does it run on this Mac (where the Keychain, PDFKit, `say` and Kokoro live) or in
Cowork's Linux sandbox? It also checks the other things enTalkenator would need from it.

Speaks MCP over stdio (newline-delimited JSON-RPC 2.0). Python 3.9+, standard library only.
Logs each start to ~/Library/Logs/entalkenator-mac-check.log when that folder exists.
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time

VERSION = "0.0.3"
OUT_LOCK = threading.Lock()


def send(msg):
    with OUT_LOCK:
        sys.stdout.write(json.dumps(msg) + "\n")
        sys.stdout.flush()


def log(text):
    path = os.path.expanduser("~/Library/Logs")
    if os.path.isdir(path):
        try:
            with open(os.path.join(path, "entalkenator-mac-check.log"), "a") as f:
                f.write("%s pid=%d %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), os.getpid(), text))
        except OSError:
            pass


def run(cmd, timeout=10):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, "", str(e)


def where_am_i(_args):
    home = os.path.expanduser("~")
    _, parent, _ = run(["ps", "-o", "command=", "-p", str(os.getppid())])
    safe_env = {k: os.environ.get(k) for k in ("HOME", "USER", "SHELL", "PATH", "PWD", "TMPDIR", "LANG",
                                               "CLAUDE_PLUGIN_ROOT", "CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_DATA")}
    return {
        "platform": sys.platform,
        "machine": platform.machine(),
        "macOS": platform.mac_ver()[0] or None,
        "python": "%s (%s)" % (sys.executable, platform.python_version()),
        "this file": os.path.abspath(__file__) if "__file__" in globals() else "(inline)",
        "cwd": os.getcwd(),
        "parent process": parent[:300],
        "env (selected)": safe_env,
        "env names": sorted(os.environ),
        "mac tools": {t: shutil.which(t) for t in ("osascript", "say", "afconvert", "security", "mdfind", "open")},
        "enTalkenator on this Mac": {
            "kokoro venv": os.path.isdir(os.path.join(home, ".cache", "entalkenator", "venv")),
            "wrangler": os.path.exists(os.path.join(home, ".cache", "entalkenator", "wrangler", "node_modules", ".bin", "wrangler")),
            "library configured": os.path.exists(os.path.join(home, ".config", "entalkenator", "library.json")),
            "voices configured": os.path.exists(os.path.join(home, ".config", "entalkenator", "voices.json")),
        },
    }


SEARCH_ROOTS = ["~/Library/Mobile Documents/com~apple~CloudDocs", "~/Documents", "~/Desktop", "~/Downloads", "~"]
SKIP = {"Library", "node_modules", ".git", ".Trash", "Applications", ".cache", ".npm", "Pictures", "Music", "Movies"}


def find_folder(args):
    """Find the Mac folder holding a marker file the Cowork side just wrote into its folder."""
    name = os.path.basename(str(args.get("marker", "")))
    if not name.startswith("entalkenator-marker-"):
        return {"error": "marker must be a file name starting with entalkenator-marker-"}
    t0 = time.time()
    found, how = None, None
    code, out, err = run(["mdfind", "-name", name], timeout=15)
    for line in out.splitlines():
        if os.path.basename(line) == name:
            found, how = line, "spotlight"
            break
    if not found:
        deadline = time.time() + 25
        for root in SEARCH_ROOTS:
            root = os.path.expanduser(root)
            base_depth = root.rstrip("/").count("/")
            for dirpath, dirnames, filenames in os.walk(root):
                if time.time() > deadline:
                    break
                dirnames[:] = [d for d in dirnames if d not in SKIP and not d.startswith(".")]
                if dirpath.count("/") - base_depth >= 7:
                    dirnames[:] = []
                if name in filenames:
                    found, how = os.path.join(dirpath, name), "walk of " + root
                    break
            if found:
                break
    result = {"seconds": round(time.time() - t0, 1), "spotlight exit": code}
    if not found:
        result["found"] = False
        result["note"] = "Not found by Spotlight or a 25-second walk of iCloud Drive, Documents, Desktop and Downloads."
        return result
    result.update({"found": True, "how": how, "mac folder": os.path.dirname(found)})
    try:
        with open(found, encoding="utf-8") as f:
            result["marker text"] = f.read(200)
        result["can read it"] = True
    except OSError as e:
        result["can read it"] = False
        result["read error"] = str(e)
    return result


def show_dialog(_args):
    script = ('display dialog "enTalkenator\'s Mac check can show you a dialog.\\n\\n'
              'This is where you would type an API key or passphrase, so it never goes through the chat." '
              'with title "enTalkenator" buttons {"Not now", "I can see it"} default button 2 giving up after 90')
    code, out, err = run(["osascript", "-e", script], timeout=100)
    return {"exit": code, "result": out or None, "error": err or None}


def wait(args, progress_token=None):
    seconds = max(1, min(int(args.get("seconds", 30)), 900))
    t0 = time.time()
    while time.time() - t0 < seconds:
        time.sleep(min(10, seconds - (time.time() - t0)))
        if progress_token is not None:
            send({"jsonrpc": "2.0", "method": "notifications/progress",
                  "params": {"progressToken": progress_token, "progress": round(time.time() - t0), "total": seconds}})
    return {"waited seconds": round(time.time() - t0, 1), "progress token given": progress_token is not None}


TOOLS = {
    "where_am_i": (where_am_i, "Report where this enTalkenator helper is running: operating system, chip, "
                   "which Mac tools it can see (osascript, say, afconvert, security), and its environment.",
                   {"type": "object", "properties": {}}),
    "find_folder": (find_folder, "Find, on the Mac, the folder that holds a marker file. Write a small text file "
                    "named entalkenator-marker-<random letters>.txt into your working folder first, then pass its "
                    "file name. Returns the folder's path as the Mac sees it.",
                    {"type": "object", "properties": {"marker": {"type": "string", "description": "The marker's file name"}},
                     "required": ["marker"]}),
    "show_dialog": (show_dialog, "Show a dialog on the Mac's screen and report which button the person pressed.",
                    {"type": "object", "properties": {}}),
    "wait": (wait, "Wait a number of seconds (up to 900), sending progress every 10 seconds, to measure how long "
             "one tool call may run.",
             {"type": "object", "properties": {"seconds": {"type": "integer"}}, "required": ["seconds"]}),
}


def handle_call(req):
    rid = req.get("id")
    params = req.get("params") or {}
    name = params.get("name")
    args = params.get("arguments") or {}
    token = (params.get("_meta") or {}).get("progressToken")
    log("call %s %s" % (name, json.dumps(args)[:200]))
    if name not in TOOLS:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": "Unknown tool %s" % name}})
        return
    try:
        fn = TOOLS[name][0]
        result = fn(args, token) if name == "wait" else fn(args)
        send({"jsonrpc": "2.0", "id": rid, "result": {
            "content": [{"type": "text", "text": json.dumps(result, indent=2)}], "isError": False}})
    except Exception as e:  # report, don't die
        send({"jsonrpc": "2.0", "id": rid, "result": {
            "content": [{"type": "text", "text": "%s: %s" % (type(e).__name__, e)}], "isError": True}})


def main():
    log("start argv=%r cwd=%r ppid=%d platform=%s" % (sys.argv, os.getcwd(), os.getppid(), sys.platform))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        method, rid = req.get("method"), req.get("id")
        if rid is None:
            continue  # a notification, such as notifications/initialized
        if method == "initialize":
            version = (req.get("params") or {}).get("protocolVersion") or "2025-06-18"
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "entalkenator-mac-check", "version": VERSION},
                "instructions": "Tells whether enTalkenator's helper runs on the person's Mac. Use only when asked "
                                "to check the enTalkenator Mac connection."}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": rid, "result": {"tools": [
                {"name": n, "description": d, "inputSchema": s} for n, (_, d, s) in TOOLS.items()]}})
        elif method == "tools/call":
            threading.Thread(target=handle_call, args=(req,), daemon=True).start()
        elif method == "ping":
            send({"jsonrpc": "2.0", "id": rid, "result": {}})
        else:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "Method not found: %s" % method}})
    log("stdin closed; exiting")


if __name__ == "__main__":
    main()
