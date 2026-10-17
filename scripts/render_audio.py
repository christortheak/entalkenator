#!/usr/bin/env python3
"""Render an enTalkenator transcript ("[Name]: text" turns) to one .m4a, one voice per role.

Engines:
  kokoro  Kokoro-82M through mlx-audio, local and free on Apple Silicon (default when
          installed; run setup_tts.sh once). Model weights are Apache-2.0.
  gemini  Google gemini-3.8-flash-tts through the Interactions API, one request per turn.
          Needs GEMINI_API_KEY. Paid.
  say     The macOS `say` command. No install, no cost, robotic.

Clips are cached in --work-dir by text and voice, so a re-run only renders what changed.
Kokoro renders in a child process (the setup_tts.sh venv) that hard-exits when done, so
an MLX crash can't take the finished clips or the stitching with it. Clips are stitched
in Python, loudness-matched, and encoded to AAC (24 kHz mono, like the app) with
afconvert, or ffmpeg when afconvert is missing.

Next to the .m4a it writes <name>.chapters.json: one Podcasting 2.0 chapter per turn
(start and end time, speaker), which the enTalkenator library uses to follow the
transcript and podcast apps show as chapters.

Prints JSON: output path, duration, engine, voice map, timings, chapters file.
"""

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import wave
from concurrent.futures import ThreadPoolExecutor

SAMPLE_RATE = 24000
ENT_HOME = os.environ.get("ENTALKENATOR_HOME", os.path.expanduser("~/.cache/entalkenator"))
VENV_PY = os.path.join(ENT_HOME, "venv", "bin", "python")
KOKORO_MODEL = os.environ.get("ENTALKENATOR_KOKORO_MODEL", "mlx-community/Kokoro-82M-bf16")
GEMINI_MODEL = os.environ.get("ENTALKENATOR_GEMINI_TTS_MODEL", "gemini-3.8-flash-tts")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"

# Distinct voices, alternating register and accent so neighbouring roles contrast.
# Kokoro grades (hexgrad/Kokoro-82M VOICES.md): af_heart A, af_bella A-, bf_emma B-,
# am_michael/am_fenrir/am_puck C+, bm_george C.
KOKORO_VOICES = ["am_michael", "af_heart", "bm_george", "af_bella", "am_fenrir", "bf_emma", "am_puck", "af_nicole"]
GEMINI_VOICES = ["Charon", "Kore", "Puck", "Aoede", "Fenrir", "Leda", "Orus", "Zephyr"]
SAY_VOICES = ["Daniel", "Samantha", "Reed", "Karen", "Rishi", "Moira", "Eddy", "Flo", "Tessa", "Sandy", "Fred"]

TURN_RE = re.compile(r"^\[([^\]\n]{1,80})\]:[ \t]?", re.M)


def emit(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def parse_transcript(text):
    """Split "[Name]: text" blocks (Template.processStep's format) into (role, text) turns."""
    turns = []
    matches = list(TURN_RE.finditer(text))
    if not matches:
        return [("Narrator", text.strip())] if text.strip() else []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end():end].strip()
        if body:
            turns.append((m.group(1).strip(), body))
    return turns


def speakable(text):
    """Strip markup a TTS engine would read aloud or stumble on."""
    t = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    t = re.sub(r"(?<!\w)[*_]([^*_\n]+)[*_](?!\w)", r"\1", t)
    t = re.sub(r"^\s*#+\s*", "", t, flags=re.M)
    t = re.sub(r"^\s*[-*•]\s+", "", t, flags=re.M)
    t = t.replace("*", "")
    return t.strip()


# ---------------------------------------------------------------------------
# WAV helpers (16-bit mono PCM at SAMPLE_RATE)


def write_wav(path, pcm16_bytes, rate=SAMPLE_RATE):
    tmp = path + ".tmp"
    with wave.open(tmp, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm16_bytes)
    os.replace(tmp, path)


def read_wav(path):
    with wave.open(path, "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError("%s is not 16-bit mono" % path)
        return w.getframerate(), w.readframes(w.getnframes())


def silence(seconds, rate=SAMPLE_RATE):
    return b"\x00\x00" * int(seconds * rate)


def normalize_pcm16(pcm, target_rms=0.08, peak_cap=0.97):
    """Even out loudness between voices (RMS to about -22 dBFS, peak-limited)."""
    try:
        import numpy as np
        a = np.frombuffer(pcm, dtype="<i2").astype("float32") / 32768.0
        if a.size == 0:
            return pcm
        rms = float(np.sqrt(np.mean(a * a)))
        peak = float(np.max(np.abs(a)))
        if rms < 1e-5:
            return pcm
        gain = min(target_rms / rms, peak_cap / max(peak, 1e-5))
        return (np.clip(a * gain, -1, 1) * 32767).astype("<i2").tobytes()
    except ImportError:
        pass
    try:
        import audioop  # stdlib up to Python 3.12
        rms = audioop.rms(pcm, 2) / 32768.0
        peak = audioop.max(pcm, 2) / 32768.0
        if rms < 1e-5:
            return pcm
        gain = min(target_rms / rms, peak_cap / max(peak, 1e-5))
        return audioop.mul(pcm, 2, gain)
    except ImportError:
        return pcm


# ---------------------------------------------------------------------------
# engines


class KokoroEngine:
    name = "kokoro"
    voices = KOKORO_VOICES
    workers = 1

    def __init__(self, speed):
        import numpy as np
        from mlx_audio.tts.utils import load_model
        self.np = np
        self.speed = speed
        self.model = load_model(KOKORO_MODEL)
        self.rate = getattr(self.model, "sample_rate", SAMPLE_RATE)

    def render(self, text, voice, path):
        np = self.np
        lang = voice[0] if voice[:1] in ("a", "b") else "a"
        parts = []
        gap = np.zeros(int(0.25 * self.rate), dtype="float32")
        for r in self.model.generate(text=text, voice=voice, speed=self.speed, lang_code=lang):
            if parts:
                parts.append(gap)
            parts.append(np.asarray(r.audio, dtype="float32"))
        if not parts:
            raise RuntimeError("Kokoro produced no audio")
        a = np.concatenate(parts)
        if self.rate != SAMPLE_RATE:
            idx = np.linspace(0, len(a) - 1, int(len(a) * SAMPLE_RATE / self.rate))
            a = np.interp(idx, np.arange(len(a)), a).astype("float32")
        write_wav(path, (np.clip(a, -1, 1) * 32767).astype("<i2").tobytes())


class SayEngine:
    name = "say"
    workers = 4

    def __init__(self, speed):
        self.rate_wpm = int(185 * speed)
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
        english = []  # (name, locale); names look like "Daniel" or "Daniel (English (UK))"
        for line in out.splitlines():
            m = re.match(r"^(.+?)\s+([a-z]{2,3}_[A-Za-z0-9]+)\s+#", line)
            if m and m.group(2).startswith("en_") and (m.group(1), m.group(2)) not in english:
                english.append((m.group(1).strip(), m.group(2)))
        self.locale = dict(english)
        self.voices = []
        for base in SAY_VOICES:
            variants = [n for n, loc in english if n.split(" (")[0] == base]
            variants.sort(key=lambda n: (self.locale[n] not in ("en_US", "en_GB"), n))
            if variants:
                self.voices.append(variants[0])
        if not self.voices:
            self.voices = [n for n, _ in english][:8]

    def render(self, text, voice, path):
        txt = path + ".txt"
        with open(txt, "w", encoding="utf-8") as f:
            f.write(text)
        tmp = path + ".say.wav"
        subprocess.run(["say", "-v", voice, "-r", str(self.rate_wpm), "-f", txt, "-o", tmp,
                        "--file-format=WAVE", "--data-format=LEI16@%d" % SAMPLE_RATE], check=True)
        rate, pcm = read_wav(tmp)
        write_wav(path, pcm, rate)
        os.remove(tmp)
        os.remove(txt)


class GeminiEngine:
    name = "gemini"
    voices = GEMINI_VOICES
    workers = 4

    def __init__(self, speed):
        self.key = os.environ.get("GEMINI_API_KEY", "")
        if not self.key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        self.style = None if abs(speed - 1.0) < 0.05 else ("Speak a little faster." if speed > 1 else "Speak a little slower.")

    def render(self, text, voice, path):
        content = {"type": "text", "text": text}
        # The 3.8 TTS models read inline cues aloud, so delivery notes go in speech_metadata.
        content["annotations"] = [{"type": "speech_metadata",
                                   "style": self.style or "Natural, conversational, as in a live public debate."}]
        body = {
            "model": GEMINI_MODEL,
            "input": [{"type": "user_input", "content": [content]}],
            "response_format": {"type": "audio", "mime_type": "audio/wav", "sample_rate": SAMPLE_RATE},
            "generation_config": {"speech_config": [{"voice": voice}]},
        }
        req = urllib.request.Request(GEMINI_URL, data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json", "x-goog-api-key": self.key})
        delay = 2.0
        for attempt in range(6):
            try:
                with urllib.request.urlopen(req, timeout=180) as resp:
                    data = json.load(resp)
                break
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < 5:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise RuntimeError("Gemini TTS HTTP %d: %s" % (e.code, e.read()[:300].decode("utf-8", "replace")))
            except urllib.error.URLError:
                if attempt < 5:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise
        audio_b64 = None
        for step in data.get("steps", []):
            for block in step.get("content", []) or []:
                if block.get("type") == "audio" and block.get("data"):
                    audio_b64 = block["data"]
        if audio_b64 is None and isinstance(data.get("output_audio"), dict):
            audio_b64 = data["output_audio"].get("data")
        if not audio_b64:
            raise RuntimeError("Gemini TTS returned no audio: %s" % json.dumps(data)[:300])
        raw = base64.b64decode(audio_b64)
        if raw[:4] == b"RIFF":
            tmp = path + ".dl.wav"
            with open(tmp, "wb") as f:
                f.write(raw)
            rate, pcm = read_wav(tmp)
            os.remove(tmp)
        else:  # headerless audio/l16
            rate, pcm = SAMPLE_RATE, raw
        write_wav(path, pcm, rate)


def kokoro_importable():
    try:
        import mlx_audio  # noqa: F401
        return True
    except ImportError:
        return False


def kokoro_python():
    """An interpreter that can import mlx_audio: this one, or the setup_tts.sh venv."""
    if kokoro_importable():
        return sys.executable
    if os.path.exists(VENV_PY):
        return VENV_PY
    return None


def pick_engine(requested):
    if requested in ("auto", "kokoro"):
        if kokoro_python():
            return "kokoro"
        if requested == "kokoro":
            raise SystemExit("Kokoro is not installed. Run scripts/setup_tts.sh, or pass --engine say / gemini.")
    if requested == "gemini" or (requested == "auto" and os.environ.get("GEMINI_API_KEY")):
        return "gemini"
    if requested in ("say", "auto") and shutil.which("say"):
        return "say"
    raise SystemExit("No voice engine available (need Kokoro, GEMINI_API_KEY, or macOS `say`).")


def make_engine(name, speed):
    return {"kokoro": KokoroEngine, "say": SayEngine, "gemini": GeminiEngine}[name](speed)


class VoicePool:
    def __init__(self, voices):
        self.voices = voices


def voice_pool(name):
    if name == "kokoro":
        return VoicePool(KOKORO_VOICES)
    if name == "gemini":
        return VoicePool(GEMINI_VOICES)
    return SayEngine(1.0)


SAY_DESCRIPTIONS = {
    "Daniel": "male", "Samantha": "female", "Reed": "male", "Karen": "female", "Rishi": "male",
    "Moira": "female", "Eddy": "male", "Flo": "female", "Tessa": "female", "Sandy": "female",
    "Fred": "male (dated)",
}
LOCALE_NAMES = {"en_US": "American", "en_GB": "British", "en_AU": "Australian", "en_IE": "Irish",
                "en_IN": "Indian English", "en_ZA": "South African"}


_SAY = []


def _say_pool():
    if not _SAY:
        _SAY.append(SayEngine(1.0))
    return _SAY[0]


def describe_voice(engine_name, voice):
    if engine_name == "kokoro" and len(voice) > 3 and voice[2] == "_":
        accent = {"a": "American", "b": "British"}.get(voice[0], voice[0])
        register = {"f": "female", "m": "male"}.get(voice[1], voice[1])
        return "%s %s" % (accent, register)
    if engine_name == "say":
        loc = getattr(_say_pool(), "locale", {}).get(voice, "")
        return ("%s %s" % (LOCALE_NAMES.get(loc, loc), SAY_DESCRIPTIONS.get(voice.split(" (")[0], ""))).strip()
    return ""


def kokoro_worker(jobs_file, speed):
    """Child process: render the listed clips, then exit without interpreter teardown.

    MLX has crashed natively after a long render (no traceback, no output), so the
    parent never loads it: the clips land on disk and the parent stitches them.
    """
    engine = KokoroEngine(speed)
    with open(jobs_file, "r", encoding="utf-8") as f:
        jobs = json.load(f)
    for j in jobs:
        if not os.path.exists(j["path"]):
            engine.render(j["text"], j["voice"], j["path"])
            log("  %s done" % j["label"])
    sys.stderr.flush()
    os._exit(0)


def render_kokoro_in_child(todo, speed, work):
    """todo: dicts with text, voice, path, label."""
    jobs_file = os.path.join(work, "jobs.json")
    with open(jobs_file, "w", encoding="utf-8") as f:
        json.dump(todo, f)
    cmd = [kokoro_python(), "-X", "faulthandler", os.path.abspath(__file__), "--worker", jobs_file,
           "--speed", str(speed)]
    missing = todo
    for attempt in (1, 2):
        rc = subprocess.run(cmd, stdout=subprocess.DEVNULL).returncode
        missing = [j for j in todo if not os.path.exists(j["path"])]
        if not missing:
            break
        log("Kokoro worker exited with code %s and %d clip(s) missing%s" %
            (rc, len(missing), "; retrying once" if attempt == 1 else ""))
    if missing:
        raise SystemExit("Kokoro could not render %d clip(s). Re-run to retry; finished clips are kept." % len(missing))


def render_jobs(engine_name, jobs, speed, work):
    """Render every job whose clip isn't cached yet. Returns how many were rendered."""
    todo = [j for j in jobs if not os.path.exists(j["path"])]
    if todo and engine_name == "kokoro":
        render_kokoro_in_child(todo, speed, work)
    elif todo:
        engine = make_engine(engine_name, speed)

        def run(job):
            engine.render(job["text"], job["voice"], job["path"])
            log("  %s done" % job["label"])

        if engine.workers > 1 and len(todo) > 1:
            with ThreadPoolExecutor(engine.workers) as ex:
                list(ex.map(run, todo))
        else:
            for j in todo:
                run(j)
    return len(todo)


def clip_path(work, prefix, engine_name, voice, speed, text):
    key = hashlib.sha1(("%s|%s|%s|%s" % (engine_name, voice, speed, text)).encode("utf-8")).hexdigest()[:12]
    return os.path.join(work, "%s-%s.wav" % (prefix, key))


def stitch(paths, gap, out, bitrate, work):
    """Join 16-bit mono clips with silence between them and encode to .m4a.

    Returns (total seconds, [(start, end) seconds of each clip in the output]).
    """
    pcm = [silence(0.3)]
    spans = []
    samples = len(pcm[0]) // 2
    for path in paths:
        rate, clip = read_wav(path)
        if rate != SAMPLE_RATE:
            raise SystemExit("Clip %s is %d Hz, expected %d" % (path, rate, SAMPLE_RATE))
        clip = normalize_pcm16(clip)
        spans.append((samples / SAMPLE_RATE, (samples + len(clip) // 2) / SAMPLE_RATE))
        pcm.append(clip)
        pcm.append(silence(gap))
        samples += len(clip) // 2 + len(pcm[-1]) // 2
    pcm.append(silence(0.3))
    joined = b"".join(pcm)
    full_wav = os.path.join(work, "full-%s.wav" % os.getpid())
    write_wav(full_wav, joined)
    encode_m4a(full_wav, out, bitrate)
    os.remove(full_wav)
    return len(joined) / 2.0 / SAMPLE_RATE, spans


def chapter_title(role, text, limit=60):
    """A chapter title like "Role: opening words…" for a podcast app's chapter list."""
    words = " ".join(text.split())
    if len(words) > limit:
        words = words[:limit].rsplit(" ", 1)[0].rstrip(",;:.-") + "…"
    return "%s: %s" % (role, words) if words else role


def write_chapters(path, turns, spans, seconds, engine_name, voices, transcript):
    """One Podcasting 2.0 chapter per turn, in transcript order.

    The enTalkenator library pairs chapter N with turn N of the transcript to show
    who is speaking. Chapters start where each clip starts (the first at 0) and end
    where its speech ends; the silence between turns belongs to no chapter.
    """
    chapters = []
    for i, ((role, text), (start, end)) in enumerate(zip(turns, spans)):
        chapters.append({
            "startTime": 0 if i == 0 else round(start, 3),
            "endTime": round(end, 3),
            "title": chapter_title(role, speakable(text)),
            "speaker": role,
            "turn": i + 1,
        })
    doc = {
        "version": "1.2.0",
        "chapters": chapters,
        # Extra fields for the library; podcast apps ignore them.
        "entalkenator": {
            "durationSeconds": round(seconds, 3),
            "engine": engine_name,
            "voices": voices,
            "transcript": os.path.basename(transcript),
            "turns": len(chapters),
        },
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def sample_line(text, min_words=18, max_words=40):
    """The opening sentence or two of a turn, for auditioning a voice."""
    out = []
    for sent in re.split(r"(?<=[.!?])\s+", " ".join(text.split())):
        out.append(sent)
        if len(" ".join(out).split()) >= min_words:
            break
    words = " ".join(out).split()
    return " ".join(words[:max_words])


def audition(args, engine_name, pool, turns, roles):
    """Per role, one short .m4a: each candidate voice reads the role's first line."""
    out_dir = os.path.abspath(args.out)
    work = os.path.abspath(args.work_dir or os.path.join(out_dir, "audition.clips"))
    os.makedirs(work, exist_ok=True)
    wanted = {}
    for part in re.split(r"[;\n]", args.candidates or ""):
        if "=" in part:
            role, vs = part.split("=", 1)
            wanted[role.strip()] = [v.strip() for v in vs.split(",") if v.strip()]
    first_line = {}
    for role, text in turns:
        first_line.setdefault(role, sample_line(speakable(text)))
    plan, jobs = [], []
    for role in roles:
        cands = wanted.get(role) or pool.voices[:4]
        entry = {"role": role, "sample": first_line[role], "order": []}
        for n, voice in enumerate(cands, 1):
            path = clip_path(work, "aud", engine_name, voice, args.speed, first_line[role])
            jobs.append({"text": first_line[role], "voice": voice, "path": path,
                         "label": "%s in %s" % (role, voice)})
            entry["order"].append({"n": n, "voice": voice, "sounds": describe_voice(engine_name, voice), "path": path})
        plan.append(entry)
    rendered = render_jobs(engine_name, jobs, args.speed, work)
    for entry in plan:
        f = os.path.join(out_dir, "audition-%s.m4a" % re.sub(r"[^A-Za-z0-9]+", "-", entry["role"]).strip("-").lower())
        entry["seconds"] = round(stitch([o.pop("path") for o in entry["order"]], 0.9, f, args.bitrate, work)[0], 1)
        entry["file"] = f
    emit({"engine": engine_name, "rendered": rendered, "auditions": plan,
          "note": "Each file plays the candidates in the listed order, about a second apart."})


def assign_voices(roles, engine, overrides):
    voices = {}
    used = set()
    for role, v in overrides.items():
        if role in roles:
            voices[role] = v
            used.add(v)
    pool = [v for v in engine.voices if v not in used]
    for role in roles:
        if role not in voices:
            voices[role] = pool.pop(0) if pool else engine.voices[len(voices) % len(engine.voices)]
    return voices


def parse_overrides(spec):
    out = {}
    if not spec:
        return out
    for part in re.split(r"[;\n]", spec):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip() and v.strip():
                out[k.strip()] = v.strip()
    return out


def encode_m4a(wav_path, out_path, bitrate):
    tmp = out_path + ".tmp.m4a"
    if shutil.which("afconvert"):
        subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", "-b", str(bitrate), wav_path, tmp],
                       check=True, capture_output=True)
    elif shutil.which("ffmpeg"):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav_path, "-c:a", "aac",
                        "-b:a", str(bitrate), "-ar", str(SAMPLE_RATE), "-ac", "1", tmp], check=True)
    else:
        raise SystemExit("Need afconvert (macOS) or ffmpeg to write .m4a.")
    os.replace(tmp, out_path)


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--worker":
        speed = float(sys.argv[sys.argv.index("--speed") + 1]) if "--speed" in sys.argv else 1.0
        kokoro_worker(sys.argv[2], speed)
        return
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--transcript", help='file of "[Name]: text" turns')
    ap.add_argument("--out", help="output .m4a")
    ap.add_argument("--engine", default="auto", choices=["auto", "kokoro", "gemini", "say"])
    ap.add_argument("--voices", help='overrides, e.g. "Hillford Johns=bm_george;The Author=af_heart"')
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--gap", type=float, default=0.45, help="seconds of silence between turns")
    ap.add_argument("--bitrate", type=int, default=48000)
    ap.add_argument("--work-dir", help="clip cache (default: <out>.clips/)")
    ap.add_argument("--list-voices", action="store_true")
    ap.add_argument("--audition", action="store_true",
                    help="instead of the show, write audition-<role>.m4a files into --out (a directory)")
    ap.add_argument("--candidates", help='audition voices, e.g. "Janson Skullbottom=bm_george,am_fenrir;..." '
                                         "(default: the engine's first four voices for every role)")
    args = ap.parse_args()

    engine_name = pick_engine(args.engine)
    pool = voice_pool(engine_name)
    if args.list_voices:
        emit({"engine": engine_name, "voices": [{"voice": v, "sounds": describe_voice(engine_name, v)} for v in pool.voices],
              "default order": "roles get these in order of first appearance unless --voices says otherwise"})
        return

    if not args.transcript or not args.out:
        ap.error("--transcript and --out are required")
    with open(args.transcript, "r", encoding="utf-8") as f:
        turns = parse_transcript(f.read())
    if not turns:
        raise SystemExit("Transcript has no turns.")
    roles = []
    for r, _ in turns:
        if r not in roles:
            roles.append(r)
    if args.audition:
        audition(args, engine_name, pool, turns, roles)
        return
    voices = assign_voices(roles, pool, parse_overrides(args.voices))

    out = os.path.abspath(args.out)
    work = os.path.abspath(args.work_dir or (os.path.splitext(out)[0] + ".clips"))
    os.makedirs(work, exist_ok=True)

    jobs = []
    for i, (role, text) in enumerate(turns):
        spoken = speakable(text)
        jobs.append({"text": spoken, "voice": voices[role], "label": "turn %d/%d (%s)" % (i + 1, len(turns), role),
                     "path": clip_path(work, "%03d" % (i + 1), engine_name, voices[role], args.speed, spoken)})
    log("%s: %d turns, voices %s" % (engine_name, len(jobs), voices))
    t1 = time.time()
    rendered = render_jobs(engine_name, jobs, args.speed, work)
    render_s = time.time() - t1
    seconds, spans = stitch([j["path"] for j in jobs], args.gap, out, args.bitrate, work)
    chapters = os.path.splitext(out)[0] + ".chapters.json"
    write_chapters(chapters, turns, spans, seconds, engine_name, voices, args.transcript)

    emit({
        "out": out,
        "engine": engine_name,
        "model": KOKORO_MODEL if engine_name == "kokoro" else (GEMINI_MODEL if engine_name == "gemini" else "macOS say"),
        "voices": voices,
        "turns": len(jobs),
        "rendered": rendered,
        "durationSeconds": round(seconds, 1),
        "renderSeconds": round(render_s, 1),
        "chapters": chapters,
        "clips": work,
    })


if __name__ == "__main__":
    main()
