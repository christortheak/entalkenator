#!/usr/bin/env python3
"""The voice options for enTalkenator: what each costs, needs, and sounds like, and which are ready.

Commands (each prints JSON):
  list [--words N | --transcript FILE]
                     every option, whether it's ready on this Mac, what it still needs,
                     and (with a length) the cost, and the render time once it's known here
  machine            this Mac: chip, memory, which local voices fit, and how fast each has
                     rendered here so far
  install kokoro     the free local voices (about 660 MB to download): runs setup_tts.sh
  install qwen3      the expressive local voices (Qwen3-TTS 1.7B, two models, 6.2 GB)
  install qwen3-preset  Qwen3-TTS's nine preset voices, directed line by line (3.1 GB)
  install breeze     the most natural local voices (Breeze TTS 2, 7.6 GB; non-commercial licence)

How long a local voice takes depends on the Mac, so the catalog gives each one's work
relative to Kokoro instead of times. render_audio.py records how fast each engine actually
rendered here (speeds.json), and estimates use that once there is one.
  key set ENGINE     ask for an API key (gemini, openai, elevenlabs) in a macOS dialog, check
                     it with a free request, and save it in the login Keychain. The key is
                     never printed.
  key check ENGINE   check the saved key still works
  key remove ENGINE
  default            the person's chosen option
  default set ID     choose it, e.g. kokoro, qwen3, gemini:gemini-3.8-flash-lite-tts

Keys are read from the environment first (GEMINI_API_KEY, OPENAI_API_KEY,
ELEVENLABS_API_KEY), then from the macOS login Keychain: service "enTalkenator", account
gemini / openai / elevenlabs, visible in Keychain Access. They reach the Keychain through
`security -i` on stdin, so a key never appears in a process listing. The default option is
kept in ~/.config/entalkenator/voices.json. Prices and rankings were checked on
CATALOG_DATE; Claude should present them as approximate. Python 3.9+, standard library only.
"""

import argparse
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dialog  # noqa: E402

CATALOG_DATE = "2026-10-17"
WPM = 175  # the app's speaking rate, also used for template loop lengths
ENT_HOME = os.environ.get("ENTALKENATOR_HOME", os.path.expanduser("~/.cache/entalkenator"))
CONFIG_DIR = os.environ.get("ENTALKENATOR_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".config", "entalkenator")
KEYCHAIN_SERVICE = "enTalkenator"
# Tests point these at a stand-in for `security` and a throwaway store.
SECURITY = shlex.split(os.environ.get("ENTALKENATOR_SECURITY", "/usr/bin/security"))
KEYCHAIN = os.environ.get("ENTALKENATOR_KEYCHAIN")  # a keychain file; default: the login keychain
DEFAULT_FILE = os.path.join(CONFIG_DIR, "voices.json")
HF_HUB = os.path.join(os.environ.get("HF_HOME", os.path.expanduser("~/.cache/huggingface")), "hub")

KOKORO_REPO = "mlx-community/Kokoro-82M-bf16"
QWEN_DESIGN_REPO = "mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit"
QWEN_BASE_REPO = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit"
BREEZE_REPO = "mlx-community/Breeze-TTS-2-mlx"
QWEN_PRESET_REPO = "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit"
SPEEDS_FILE = os.path.join(ENT_HOME, "speeds.json")
# Raised when an engine's renderer gets much faster, so renders timed before don't skew estimates.
SPEED_VERSIONS = {"breeze": 2}  # 2: several lines at once (breeze_fast.py)

KEY_ENV = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY", "elevenlabs": "ELEVENLABS_API_KEY"}
KEY_PAGES = {
    "gemini": "https://aistudio.google.com/apikey",
    "openai": "https://platform.openai.com/api-keys",
    "elevenlabs": "https://elevenlabs.io/app/developers/api-keys",
}

# Rates as of CATALOG_DATE. perMinute is per minute of finished audio; perKChars per 1,000
# characters of text. Rankings are the Artificial Analysis Speech Arena (single lines, not
# conversations); Kokoro scores 1061 there. A local option's `work` is its compute per
# minute of audio relative to Kokoro, measured on one Mac (an M5 Pro); the ratio holds
# roughly across Apple Silicon even though the times don't.
OPTIONS = [
    {"id": "kokoro", "engine": "kokoro", "label": "Kokoro", "where": "this Mac", "default": True,
     "summary": "Free natural-sounding voices that run on this Mac. Light: the quickest local voices, fine on any Apple Silicon Mac.",
     "quality": "Clear and natural for its size; mid-table among open models (Elo 1061). Delivery is even rather than expressive.",
     "voices": "28 English voices, American and British, male and female, and blends of two",
     "work": 1, "compute": "light", "download": "about 660 MB to download, 1.3 GB on disk", "memory": "about 2 GB",
     "requires": ["apple-silicon"], "license": "Apache-2.0 (its pronunciation fallback, espeak-ng, is GPL-3.0)",
     "cost": "free"},
    {"id": "qwen3", "engine": "qwen3", "label": "Qwen3-TTS (expressive)", "where": "this Mac",
     "summary": "Free expressive voices on this Mac. Each character's voice is designed from a description "
                "(\"a gravelly older judge\") and kept the same throughout. Heavy: about 7 times Kokoro's work.",
     "quality": "More expressive and varied than Kokoro; not yet on the public leaderboards. Occasionally drops "
                "a turn's last words, so listen to the ends of turns.",
     "voices": "any voice you can describe: age, accent, temperament, pace",
     "work": 7, "compute": "heavy", "download": "6.2 GB (two models), plus Kokoro's install", "memory": "about 10 GB",
     "requires": ["apple-silicon", "16gb"], "license": "Apache-2.0", "cost": "free"},
    {"id": "qwen3-preset", "engine": "qwen3-preset", "label": "Qwen3-TTS preset voices (directed)", "where": "this Mac",
     "summary": "Free preset voices on this Mac that follow a delivery note on every line (\"amused, pressing\"). "
                "Only two are native English speakers, both male, so it suits programs with one or two men's "
                "voices. Medium: about 3 times Kokoro's work, since it voices several lines at once.",
     "quality": "Lively and directable in our listening test; the other seven presets speak English with a "
                "Chinese, Japanese or Korean accent.",
     "voices": "ryan and aiden (native English, male); seven more with other native languages",
     "work": 3, "compute": "medium", "download": "3.1 GB, plus Kokoro's install", "memory": "about 6 GB",
     "requires": ["apple-silicon", "16gb"], "license": "Apache-2.0", "cost": "free"},
    {"id": "breeze", "engine": "breeze", "label": "Breeze TTS 2 (most natural)", "where": "this Mac",
     "summary": "Free, the most natural local voices: each character's voice is designed from a description, "
                "and each line can be directed (\"wry, conceding the point\"); it laughs and sighs where the "
                "script says so. Very heavy: about 12 times Kokoro's work, about 20 with a delivery note on "
                "every line. Personal, educational and research use only.",
     "quality": "Preferred in our listening test; leads the open-weights Speech Arena (Elo 1215, reported "
                "September 2026). English and Chinese only.",
     "voices": "any voice you can describe; a direction per line; (laughs), (sighs), (coughs), (clears throat)",
     "work": 20, "compute": "very heavy", "download": "7.6 GB, plus Kokoro's install", "memory": "about 13 GB",
     "requires": ["apple-silicon", "24gb"],
     "license": "BreezeBlue Research and Non-Commercial License: free for personal, educational and research "
                "use; no commercial use. Don't clone a real person's voice without their consent.",
     "cost": "free"},
    {"id": "say", "engine": "say", "label": "macOS voices", "where": "this Mac",
     "summary": "The voices built into macOS. Nothing to install; robotic next to the others.",
     "quality": "Clearly synthetic", "voices": "about 10 English voices", "work": 1, "compute": "light",
     "download": "none", "memory": "minimal", "requires": ["mac"], "license": "Apple system voices", "cost": "free"},
    {"id": "gemini:gemini-3.8-flash-tts", "engine": "gemini", "model": "gemini-3.8-flash-tts",
     "label": "Google Gemini 3.8 Flash TTS", "where": "cloud",
     "summary": "Excellent quality for the price, with delivery notes for each speaker. Needs a Google AI Studio key.",
     "quality": "Ranked 5th of all voices (Elo 1275)", "voices": "30 voices with distinct characters",
     "perMinute": 0.0135, "perMinuteFrom2027": 0.027,
     "terms": "On Google's free tier, prompts may be used to improve Google's products and read by reviewers; "
              "in the EEA, UK and Switzerland only the paid tier is allowed.",
     "cost": "about $0.41 per 30 minutes until Dec 31 2026, about $0.81 from Jan 2027"},
    {"id": "gemini:gemini-3.8-flash-lite-tts", "engine": "gemini", "model": "gemini-3.8-flash-lite-tts",
     "label": "Google Gemini 3.8 Flash-Lite TTS", "where": "cloud",
     "summary": "Gemini's budget voices: nearly as good, about a third cheaper. Same key and voices.",
     "quality": "Ranked 8th of all voices (Elo 1248)", "voices": "30 voices with distinct characters",
     "perMinute": 0.0093, "perMinuteFrom2027": 0.0183,
     "terms": "As for Gemini Flash.", "cost": "about $0.28 per 30 minutes until Dec 31 2026, about $0.55 from Jan 2027"},
    {"id": "openai:gpt-4o-mini-tts", "engine": "openai", "model": "gpt-4o-mini-tts",
     "label": "OpenAI gpt-4o-mini-tts", "where": "cloud",
     "summary": "OpenAI's steerable voices: each speaker can be given a manner (\"brisk, skeptical\"). Needs an OpenAI key with credit.",
     "quality": "Good; not ranked on the main leaderboard", "voices": "13 voices",
     "perMinute": 0.015, "terms": "OpenAI's usage policy requires telling listeners the voices are AI-generated "
                                  "(enTalkenator's library says so on every program).",
     "cost": "about $0.45 per 30 minutes"},
    {"id": "openai:tts-1", "engine": "openai", "model": "tts-1", "label": "OpenAI tts-1", "where": "cloud",
     "summary": "OpenAI's original voices. Same key as gpt-4o-mini-tts; no delivery instructions.",
     "quality": "Fair (tts-1-hd ranks Elo 1103; tts-1 is a little lower)", "voices": "9 voices",
     "perKChars": 0.015, "terms": "As for gpt-4o-mini-tts.", "cost": "about $0.47 per 30 minutes"},
    {"id": "openai:tts-1-hd", "engine": "openai", "model": "tts-1-hd", "label": "OpenAI tts-1-hd", "where": "cloud",
     "summary": "The higher-quality version of tts-1, at twice the price.",
     "quality": "Elo 1103", "voices": "9 voices", "perKChars": 0.03, "terms": "As for gpt-4o-mini-tts.",
     "cost": "about $0.95 per 30 minutes"},
    {"id": "elevenlabs:eleven_v4", "engine": "elevenlabs", "model": "eleven_v4", "label": "ElevenLabs v4",
     "where": "cloud",
     "summary": "The best-sounding voices available, with a very large voice library. About 5x Gemini's price.",
     "quality": "Ranked 1st-2nd of all voices (Elo about 1330)", "voices": "thousands, in the ElevenLabs voice library",
     "perKChars": 0.08,
     "terms": "ElevenLabs' free plan is for non-commercial use and asks for credit to elevenlabs.io when "
              "content is published; a private library is personal use.",
     "cost": "about $2.50 per 30 minutes"},
    {"id": "elevenlabs:eleven_v4_turbo", "engine": "elevenlabs", "model": "eleven_v4_turbo",
     "label": "ElevenLabs v4 Turbo", "where": "cloud",
     "summary": "ElevenLabs' faster, half-price model, ranked level with v4 on single lines.",
     "quality": "Ranked 1st-2nd of all voices (Elo about 1330)", "voices": "thousands, in the ElevenLabs voice library",
     "perKChars": 0.04, "terms": "As for ElevenLabs v4.", "cost": "about $1.25 per 30 minutes"},
]
BY_ID = {o["id"]: o for o in OPTIONS}


def emit(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def done(obj, code=0):
    emit(obj)
    sys.exit(code)


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_private(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# What this Mac has


def keychain_available():
    return bool(os.environ.get("ENTALKENATOR_SECURITY")) or (sys.platform == "darwin" and os.path.exists(SECURITY[0]))


def _kc():
    return [KEYCHAIN] if KEYCHAIN else []


def keychain_get(account):
    """The stored secret, or None. Never printed."""
    if not keychain_available():
        return None
    p = subprocess.run(SECURITY + ["find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account, "-w"] + _kc(),
                       capture_output=True, text=True)
    return (p.stdout.strip() or None) if p.returncode == 0 else None


def keychain_set(account, secret, label):
    """Add or replace the item. The secret goes to `security -i` on stdin, never on a command line."""
    if any(c in secret for c in '"\\') or any(c.isspace() for c in secret):
        raise ValueError("unexpected characters in the secret")
    quote = lambda v: '"%s"' % v.replace('"', "")  # noqa: E731
    line = "add-generic-password -U -s %s -a %s -l %s -w %s %s" % (
        quote(KEYCHAIN_SERVICE), quote(account), quote(label), quote(secret), " ".join(quote(k) for k in _kc()))
    subprocess.run(SECURITY + ["-i"], input=line.strip() + "\n", capture_output=True, text=True)
    # `security -i` exits 0 even when a command fails, so read the item back.
    return keychain_get(account) == secret


def keychain_delete(account):
    p = subprocess.run(SECURITY + ["delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account] + _kc(),
                       capture_output=True, text=True)
    return p.returncode == 0


def api_key(engine):
    """The engine's API key from the environment or the Keychain, or None. Never printed."""
    env = os.environ.get(KEY_ENV.get(engine, ""), "").strip()
    if env:
        return env
    return keychain_get(engine)


def key_source(engine):
    if os.environ.get(KEY_ENV.get(engine, ""), "").strip():
        return "environment (%s)" % KEY_ENV[engine]
    if keychain_get(engine):
        return "Keychain"
    return None


def hf_cached(repo):
    snap = os.path.join(HF_HUB, "models--" + repo.replace("/", "--"), "snapshots")
    return any(os.listdir(os.path.join(snap, d)) for d in os.listdir(snap)) if os.path.isdir(snap) else False


def venv_ready():
    return bool(glob.glob(os.path.join(ENT_HOME, "venv", "lib", "python3.*", "site-packages", "mlx_audio")))


def memory_gb():
    try:
        out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5).stdout
        return round(int(out.strip()) / 2 ** 30)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def chip():
    try:
        return subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True,
                              timeout=5).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def hardware():
    mac = sys.platform == "darwin"
    return {"mac": mac, "appleSilicon": mac and os.uname().machine == "arm64", "memoryGB": memory_gb() if mac else None,
            "chip": chip() if mac else None}


def measured_ratio(engine):
    """Render seconds per second of audio for this engine on this Mac: the median of recent renders."""
    rows = (read_json(SPEEDS_FILE).get(engine) or [])[-8:]
    version = SPEED_VERSIONS.get(engine, 1)
    ratios = sorted(r["ratio"] for r in rows if r.get("ratio") and r.get("v", 1) == version)
    return ratios[len(ratios) // 2] if ratios else None


def record_speed(engine, audio_seconds, render_seconds):
    """Called by render_audio.py after a render; only renders long enough to mean something count."""
    if audio_seconds < 60 or render_seconds <= 0:
        return
    data = read_json(SPEEDS_FILE)
    row = {"ratio": round(render_seconds / audio_seconds, 3), "audioSeconds": round(audio_seconds),
           "at": time.strftime("%Y-%m-%d")}
    if engine in SPEED_VERSIONS:
        row["v"] = SPEED_VERSIONS[engine]
    data.setdefault(engine, []).append(row)
    data[engine] = data[engine][-8:]
    write_private(SPEEDS_FILE, data)


def needs_for(opt, hw):
    """What has to happen before this option can be used, in order."""
    out = []
    req = opt.get("requires", [])
    if "mac" in req and not hw["mac"]:
        out.append({"what": "unsupported", "tell": "Needs a Mac."})
    if "apple-silicon" in req and not hw["appleSilicon"]:
        out.append({"what": "unsupported", "tell": "Needs a Mac with Apple Silicon (M1 or later)."})
    for gb in (16, 24):
        if "%dgb" % gb in req and (hw["memoryGB"] or 0) < gb:
            out.append({"what": "unsupported", "tell": "Needs at least %d GB of memory (this Mac has %s GB)."
                        % (gb, hw["memoryGB"])})
    if out:
        return out
    eng = opt["engine"]
    if eng == "kokoro" and not (venv_ready() and hf_cached(KOKORO_REPO)):
        out.append({"what": "install", "tell": "Install the voices: `voices.py install kokoro` (%s)." % opt["download"]})
    if eng == "qwen3":
        if not venv_ready():
            out.append({"what": "install", "tell": "Install Kokoro first; Qwen3-TTS uses the same engine."})
        if not (hf_cached(QWEN_DESIGN_REPO) and hf_cached(QWEN_BASE_REPO)):
            out.append({"what": "install", "tell": "Download the models: `voices.py install qwen3` (6.2 GB)."})
    if eng == "qwen3-preset":
        if not venv_ready():
            out.append({"what": "install", "tell": "Install Kokoro first; the presets use the same engine."})
        if not hf_cached(QWEN_PRESET_REPO):
            out.append({"what": "install", "tell": "Download the model: `voices.py install qwen3-preset` (3.1 GB)."})
    if eng == "breeze":
        if not venv_ready():
            out.append({"what": "install", "tell": "Install Kokoro first; Breeze uses the same engine."})
        if not hf_cached(BREEZE_REPO):
            out.append({"what": "install", "tell": "Download the model: `voices.py install breeze` (7.6 GB)."})
    if eng == "say" and not shutil.which("say"):
        out.append({"what": "unsupported", "tell": "The macOS `say` command isn't available."})
    if eng in KEY_ENV and not api_key(eng):
        out.append({"what": "key", "tell": "Needs a %s API key: `voices.py key set %s`. Get one at %s."
                    % (eng.capitalize() if eng != "openai" else "OpenAI", eng, KEY_PAGES[eng]), "url": KEY_PAGES[eng]})
    return out


def estimate(opt, words, chars):
    minutes = words / float(WPM)
    est = {"audioMinutes": round(minutes, 1)}
    if opt["where"] == "cloud":
        est["renderNote"] = "a few minutes; turns are sent four at a time"
    elif measured_ratio(opt["engine"]):
        est["renderMinutes"] = round(max(0.2, minutes * measured_ratio(opt["engine"])), 1)
        est["renderNote"] = "from this Mac's earlier renders with this engine"
    elif measured_ratio("kokoro") and opt.get("work"):
        est["renderMinutes"] = round(max(0.2, minutes * measured_ratio("kokoro") * opt["work"]), 1)
        est["renderNote"] = "a rough guess from Kokoro's speed on this Mac; the first render will tell"
    else:
        est["renderNote"] = ("not known on this Mac yet: %s (%s); a short sample render would tell"
                             % (opt.get("compute"), "Kokoro's work" if opt.get("work") == 1 else
                                "about %d times Kokoro's work" % opt.get("work", 1)))
    if "perMinute" in opt:
        est["costUSD"] = round(minutes * opt["perMinute"], 2)
        if "perMinuteFrom2027" in opt:
            est["costUSDFrom2027"] = round(minutes * opt["perMinuteFrom2027"], 2)
    elif "perKChars" in opt:
        est["costUSD"] = round(chars / 1000.0 * opt["perKChars"], 2)
    else:
        est["costUSD"] = 0
    return est


def transcript_size(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    body = re.sub(r"^\[[^\]\n]{1,80}\]:[ \t]?", "", text, flags=re.M)
    return len(body.split()), len(body)


def chosen_default():
    d = read_json(DEFAULT_FILE)
    return d.get("id") if d.get("id") in BY_ID else None


# ---------------------------------------------------------------------------
# Commands


def cmd_list(args):
    hw = hardware()
    words = chars = None
    if args.transcript:
        words, chars = transcript_size(args.transcript)
    elif args.words:
        words, chars = args.words, args.words * 6
    out = []
    for opt in OPTIONS:
        row = {k: v for k, v in opt.items() if k not in ("perMinute", "perMinuteFrom2027", "perKChars", "requires")}
        row["needs"] = needs_for(opt, hw)
        row["ready"] = not row["needs"]
        if opt["engine"] in KEY_ENV:
            row["key"] = key_source(opt["engine"])
        if words:
            row["estimate"] = estimate(opt, words, chars)
        out.append(row)
    emit({"asOf": CATALOG_DATE, "hardware": hw, "default": chosen_default(),
          "length": {"words": words, "audioMinutes": round(words / float(WPM), 1)} if words else None,
          "options": out,
          "note": "Prices and rankings change; present them as approximate. Rankings are for single lines, not conversations."})


def cmd_machine(args):
    hw = hardware()
    local = [o for o in OPTIONS if o["where"] == "this Mac"]
    emit({"hardware": hw,
          "localVoices": [{"id": o["id"], "label": o["label"], "compute": o.get("compute"), "work": o.get("work"),
                           "memory": o.get("memory"),
                           "fits": not any(n["what"] == "unsupported" for n in needs_for(o, hw)),
                           "measured": measured_ratio(o["engine"])} for o in local],
          "note": "work is relative to Kokoro. measured is render seconds per second of audio on this Mac "
                  "(median of recent renders); null until an engine has rendered at least a minute here."})


def run_venv_python(code, *argv):
    py = os.path.join(ENT_HOME, "venv", "bin", "python")
    return subprocess.run([py, "-c", code] + list(argv))


def cmd_install(args):
    hw = hardware()
    opt = BY_ID[args.engine]
    blocking = [n for n in needs_for(opt, hw) if n["what"] == "unsupported"]
    if blocking:
        done({"ok": False, "needs": blocking}, 2)
    if not venv_ready() or (args.engine == "kokoro" and not hf_cached(KOKORO_REPO)):
        p = subprocess.run(["bash", os.path.join(HERE, "setup_tts.sh")])
        if p.returncode != 0:
            done({"ok": False, "stage": "kokoro", "error": "setup_tts.sh failed (exit %d); see its messages above." % p.returncode}, 1)
    if args.engine in ("qwen3", "qwen3-preset", "breeze"):
        code = ("import sys\nfrom huggingface_hub import snapshot_download\n"
                "for r in sys.argv[1:]:\n    print('Downloading', r, flush=True)\n    snapshot_download(r)\n")
        repos = {"qwen3": (QWEN_DESIGN_REPO, QWEN_BASE_REPO), "qwen3-preset": (QWEN_PRESET_REPO,),
                 "breeze": (BREEZE_REPO,)}[args.engine]
        p = run_venv_python(code, *repos)
        if p.returncode != 0:
            done({"ok": False, "stage": "download", "error": "The download failed; run it again to resume."}, 1)
    left = needs_for(opt, hw)
    emit({"ok": not left, "installed": args.engine, "needs": left})


def check_key(engine, key):
    """(ok, message) from a free, read-only request."""
    if engine == "gemini":
        url = os.environ.get("ENTALKENATOR_GEMINI_BASE", "https://generativelanguage.googleapis.com") + "/v1beta/models"
        headers = {"x-goog-api-key": key}
    elif engine == "openai":
        url = os.environ.get("ENTALKENATOR_OPENAI_BASE", "https://api.openai.com") + "/v1/models"
        headers = {"Authorization": "Bearer " + key}
    else:
        url = os.environ.get("ENTALKENATOR_ELEVENLABS_BASE", "https://api.elevenlabs.io") + "/v1/voices"
        headers = {"xi-api-key": key}
    req = urllib.request.Request(url, headers=dict(headers, **{"User-Agent": "entalkenator-voices/0.1"}))
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read(1)
        return True, "The key works."
    except urllib.error.HTTPError as e:
        if e.code in (400, 401, 403):
            return False, "The service rejected the key (HTTP %d). Check it was copied in full." % e.code
        return None, "Couldn't check the key (HTTP %d); it was saved anyway." % e.code
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        return None, "Couldn't reach the service to check the key (%s); it was saved anyway." % getattr(e, "reason", e)


def cmd_key(args):
    eng = args.engine
    names = {"gemini": "Google AI Studio (Gemini)", "openai": "OpenAI", "elevenlabs": "ElevenLabs"}
    if args.action == "remove":
        removed = keychain_delete(eng)
        emit({"ok": True, "removed": removed, "engine": eng, "stillSet": key_source(eng)})
        return
    if args.action == "check":
        key = api_key(eng)
        if not key:
            done({"ok": False, "engine": eng, "error": "No key saved for %s." % eng}, 1)
        ok, msg = check_key(eng, key)
        emit({"ok": ok is not False, "engine": eng, "from": key_source(eng), "message": msg})
        return
    # set
    if args.stdin:
        key = sys.stdin.readline().strip()
    else:
        if not dialog.available():
            done({"ok": False, "needs": [{"what": "terminal", "tell": "Set %s in your shell profile instead." % KEY_ENV[eng]}]}, 2)
        key = dialog.ask_secret("Paste your %s API key. It's kept in your Mac's Keychain and never shown to Claude."
                                % names[eng])
        if key is None:
            done({"ok": False, "cancelled": True, "tell": "Cancelled; nothing was saved."}, 1)
        key = key.strip()
    if len(key) < 20 or any(c.isspace() or c in '"\\' for c in key):
        done({"ok": False, "error": "That doesn't look like a complete API key; nothing was saved."}, 1)
    if not keychain_available():
        done({"ok": False, "needs": [{"what": "terminal", "tell": "No Keychain here. Set %s in your shell profile instead." % KEY_ENV[eng]}]}, 2)
    ok, msg = check_key(eng, key)
    if ok is False:
        done({"ok": False, "engine": eng, "message": msg + " Nothing was saved."}, 1)
    if not keychain_set(eng, key, "enTalkenator: %s API key" % names[eng]):
        done({"ok": False, "engine": eng, "error": "The Keychain didn't accept the key (is it locked?). Nothing was saved."}, 1)
    emit({"ok": True, "engine": eng, "saved": "Keychain (Keychain Access: search for enTalkenator)",
          "checked": ok is True, "message": msg})


def cmd_default(args):
    if args.action != "set":
        d = chosen_default()
        emit({"default": d, "label": BY_ID[d]["label"] if d else None,
              "note": None if d else "No choice saved yet; Kokoro is used when it's installed, else the macOS voices."})
        return
    choice = args.id
    if choice in ("gemini", "openai", "elevenlabs"):
        choice = next(o["id"] for o in OPTIONS if o["engine"] == choice)
    if choice not in BY_ID:
        done({"ok": False, "error": "Unknown option %r. Choose one of: %s" % (args.id, ", ".join(BY_ID))}, 1)
    left = needs_for(BY_ID[choice], hardware())
    write_private(DEFAULT_FILE, {"id": choice, "setAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    emit({"ok": True, "default": choice, "label": BY_ID[choice]["label"], "ready": not left, "needs": left})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--words", type=int, help="estimate for a program of this many spoken words")
    g.add_argument("--transcript", help="estimate for this transcript")
    p.set_defaults(func=cmd_list)
    p = sub.add_parser("install")
    p.add_argument("engine", choices=["kokoro", "qwen3", "qwen3-preset", "breeze"])
    p.set_defaults(func=cmd_install)
    p = sub.add_parser("machine")
    p.set_defaults(func=cmd_machine)
    p = sub.add_parser("key")
    p.add_argument("action", choices=["set", "check", "remove"])
    p.add_argument("engine", choices=sorted(KEY_ENV))
    p.add_argument("--stdin", action="store_true", help="read the key from stdin instead of a dialog")
    p.set_defaults(func=cmd_key)
    p = sub.add_parser("default")
    p.add_argument("action", nargs="?", choices=["set"])
    p.add_argument("id", nargs="?")
    p.set_defaults(func=cmd_default)
    args = ap.parse_args()
    if getattr(args, "action", None) == "set" and args.cmd == "default" and not args.id:
        ap.error("default set needs an option id")
    args.func(args)


if __name__ == "__main__":
    main()
