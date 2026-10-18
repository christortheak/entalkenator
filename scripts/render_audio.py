#!/usr/bin/env python3
"""Render an enTalkenator transcript ("[Name]: text" turns) to one .m4a, one voice per role.

Engines (scripts/voices.py describes each: cost, speed, download, what it needs):
  kokoro      Kokoro-82M through mlx-audio, local and free on Apple Silicon. Weights Apache-2.0.
  qwen3       Qwen3-TTS 1.7B through mlx-audio, local and free (16 GB+ Apple Silicon). Each
              --voices value is a description ("a gravelly older judge, British"): the
              VoiceDesign model makes a reference clip from it once (kept in
              ~/.cache/entalkenator/qwen3-voices), and the Base model clones that clip for
              every turn, so the voice stays the same. Weights Apache-2.0.
  say         The macOS `say` command. No install, no cost, robotic.
  gemini      Google Gemini TTS (--model gemini-3.8-flash-tts or gemini-3.8-flash-lite-tts)
              through the Interactions API, one request per turn. Paid; needs a key.
  openai      OpenAI speech (--model gpt-4o-mini-tts, tts-1 or tts-1-hd). Paid; needs a key.
  elevenlabs  ElevenLabs (--model eleven_v4 or eleven_v4_turbo). Paid; needs a key.
--engine auto uses the person's saved choice (voices.py default), else Kokoro if it's
installed, else the macOS voices. It never picks a paid engine on its own. API keys come
from the environment or the macOS Keychain (see voices.py key set).

--styles "Role=brisk, skeptical;..." gives each role a delivery note. Gemini and
gpt-4o-mini-tts follow it; the other engines ignore it. A directed program's per-turn notes
(delivery.json beside the transcript, from engine.py finalize) are added to the role's note.
--lines "Role=a line in character;..." (qwen3) is what each designed voice reads for its
reference clip; a clone copies its delivery, so a line in character makes a livelier voice.
--speeds "Role=0.95;..." (kokoro, say) sets a role's pace. Kokoro blends voices given as
"af_heart,af_bella". Pauses between turns follow the conversation unless --gap is given.

Clips are cached in --work-dir by text and voice, so a re-run only renders what changed.
--redo 5,23 renders those turns again (a local voice's new take, say, for a turn whose
words-per-minute was flagged under checkTurns: much faster than the speaker's usual pace
often means words were dropped).
Kokoro and Qwen3-TTS render in a child process (the setup_tts.sh venv) that hard-exits
when done, so an MLX crash can't take the finished clips or the stitching with it. Clips
are stitched in Python, loudness-matched, and encoded to AAC (24 kHz mono, like the app)
with afconvert, or ffmpeg when afconvert is missing.

Next to the .m4a it writes <name>.chapters.json: one Podcasting 2.0 chapter per turn
(start and end time, speaker), which the enTalkenator library uses to follow the
transcript and podcast apps show as chapters.

Prints JSON: output path, duration, engine, voice map, timings, chapters file.
"""

import argparse
import array
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
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import voices as voice_options  # noqa: E402  (API keys and the person's default)

ENT_HOME = os.environ.get("ENTALKENATOR_HOME", os.path.expanduser("~/.cache/entalkenator"))
VENV_PY = os.path.join(ENT_HOME, "venv", "bin", "python")
KOKORO_MODEL = os.environ.get("ENTALKENATOR_KOKORO_MODEL", "mlx-community/Kokoro-82M-bf16")
QWEN_DESIGN_MODEL = voice_options.QWEN_DESIGN_REPO
QWEN_BASE_MODEL = voice_options.QWEN_BASE_REPO
QWEN_VOICE_DIR = os.path.join(ENT_HOME, "qwen3-voices")
# The line each designed voice reads for its reference clip, and a pacing note added to every
# description: clones copy the reference's pace, and an unprompted reference runs fast.
QWEN_REF_LINE = ("Thank you. Let me make sure I understand the point, because the answer to this question "
                 "matters a great deal to everything that follows.")
QWEN_PACE = " Speaking at a natural, unhurried conversational pace."
QWEN_CHUNK_WORDS = 90  # longer stretches can drift or lose their last words
# A batch runs until its longest piece is done, so pieces of similar length go together.
# Measured on an M5 Pro (860 words, one voice): batches of 4 in transcript order took 0.53x
# the audio's length, sorted by length 0.44x, batches of 8 sorted 0.38x, one at a time 0.94x.
# Preloading the reference clip and clearing MLX's cache made no measurable difference. A
# whole program runs nearer 0.45-0.55x: voice design, model loads, and heat on a long render.
QWEN_BATCH = 8
QWEN_WINDOW = 16  # pieces sorted together; each window's turns are written as soon as it's done
GEMINI_URL = os.environ.get("ENTALKENATOR_GEMINI_BASE", "https://generativelanguage.googleapis.com") + "/v1beta/interactions"
OPENAI_URL = os.environ.get("ENTALKENATOR_OPENAI_BASE", "https://api.openai.com") + "/v1/audio/speech"
ELEVENLABS_BASE = os.environ.get("ENTALKENATOR_ELEVENLABS_BASE", "https://api.elevenlabs.io")
DEFAULT_MODELS = {"kokoro": KOKORO_MODEL, "qwen3": "Qwen3-TTS-12Hz-1.7B (VoiceDesign + Base, 8-bit)",
                  "say": "macOS say", "gemini": os.environ.get("ENTALKENATOR_GEMINI_TTS_MODEL", "gemini-3.8-flash-tts"),
                  "openai": "gpt-4o-mini-tts", "elevenlabs": "eleven_v4"}
DEFAULT_STYLE = "Natural and conversational, as in a live discussion."

# Distinct voices, alternating register and accent so neighbouring roles contrast.
# Kokoro grades (hexgrad/Kokoro-82M VOICES.md): af_heart A, af_bella A-, bf_emma B-,
# am_michael/am_fenrir/am_puck C+, bm_george C.
KOKORO_VOICES = ["am_michael", "af_heart", "bm_george", "af_bella", "am_fenrir", "bf_emma", "am_puck", "af_nicole"]
GEMINI_VOICES = ["Charon", "Kore", "Puck", "Aoede", "Fenrir", "Leda", "Orus", "Zephyr"]
# Google's own one-word characters for each voice, and how each is usually heard.
GEMINI_DESCRIPTIONS = {
    "Zephyr": "bright, female", "Puck": "upbeat, male", "Charon": "informative, male", "Kore": "firm, female",
    "Fenrir": "excitable, male", "Leda": "youthful, female", "Orus": "firm, male", "Aoede": "breezy, female",
    "Callirrhoe": "easy-going, female", "Autonoe": "bright, female", "Enceladus": "breathy, male",
    "Iapetus": "clear, male", "Umbriel": "easy-going, male", "Algieba": "smooth, male", "Despina": "smooth, female",
    "Erinome": "clear, female", "Algenib": "gravelly, male", "Rasalgethi": "informative, male",
    "Laomedeia": "upbeat, female", "Achernar": "soft, female", "Alnilam": "firm, male", "Schedar": "even, male",
    "Gacrux": "mature, female", "Pulcherrima": "forward, female", "Achird": "friendly, male",
    "Zubenelgenubi": "casual, male", "Vindemiatrix": "gentle, female", "Sadachbia": "lively, male",
    "Sadaltager": "knowledgeable, male", "Sulafat": "warm, female",
}
GEMINI_VOICES += [v for v in GEMINI_DESCRIPTIONS if v not in GEMINI_VOICES]
# OpenAI's voices, ordered for contrast; tts-1 and tts-1-hd have only the first nine.
OPENAI_DESCRIPTIONS = {
    "onyx": "deep, male", "nova": "bright, female", "echo": "even, male", "shimmer": "clear, female",
    "fable": "expressive, male, British-leaning", "coral": "warm, female", "ash": "clear, male",
    "sage": "calm, female", "alloy": "neutral", "cedar": "natural, male (recommended by OpenAI)",
    "marin": "natural, female (recommended by OpenAI)", "ballad": "soft, male", "verse": "versatile, male",
}
OPENAI_TTS1_VOICES = ["onyx", "nova", "echo", "shimmer", "fable", "coral", "ash", "sage", "alloy"]
OPENAI_VOICES = ["cedar", "marin"] + OPENAI_TTS1_VOICES + ["ballad", "verse"]
# Qwen3-TTS voices are descriptions. These are fallbacks: the showrunner should describe
# each character from its sketch (age, accent, temperament, pace).
QWEN_VOICES = [
    "A warm, clear American man in his forties with a steady, conversational delivery.",
    "A bright, articulate American woman in her thirties, engaged and friendly.",
    "A measured British man in his sixties with a deep, resonant voice.",
    "A crisp, confident British woman in her fifties, precise and composed.",
    "A lively American man in his twenties, quick and curious.",
    "A calm American woman in her sixties with a low, thoughtful voice.",
    "A relaxed Australian man in his thirties, good-humoured and easy-going.",
    "A soft-spoken Irish woman in her forties, warm and attentive.",
]
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


def split_text(text, max_words=None, max_chars=None):
    """Split at sentence ends into pieces under the limits (a very long sentence stays whole)."""
    sentences = re.split(r"(?<=[.!?…])\s+", " ".join(text.split()))
    pieces, cur = [], []
    for s in sentences:
        trial = cur + [s]
        joined = " ".join(trial)
        if cur and ((max_words and len(joined.split()) > max_words) or (max_chars and len(joined) > max_chars)):
            pieces.append(" ".join(cur))
            cur = [s]
        else:
            cur = trial
    if cur:
        pieces.append(" ".join(cur))
    return pieces


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

    def __init__(self, speed, model=None, styles=None):
        import numpy as np
        from mlx_audio.tts.utils import load_model
        self.np = np
        self.speed = speed
        self.model = load_model(KOKORO_MODEL)
        self.rate = getattr(self.model, "sample_rate", SAMPLE_RATE)

    def render(self, text, voice, path, style=None, speed=None):
        np = self.np
        lang = voice[0] if voice[:1] in ("a", "b") else "a"
        parts = []
        gap = np.zeros(int(0.25 * self.rate), dtype="float32")
        for r in self.model.generate(text=text, voice=voice, speed=speed or self.speed, lang_code=lang):
            if parts:
                parts.append(gap)
            parts.append(np.asarray(r.audio, dtype="float32"))
        if not parts:
            raise RuntimeError("Kokoro produced no audio")
        write_float_wav(path, np.concatenate(parts), self.rate, np)


def write_float_wav(path, a, rate, np):
    """Float samples in [-1, 1] at `rate` -> 16-bit mono WAV at SAMPLE_RATE."""
    a = np.asarray(a, dtype="float32").reshape(-1)
    if rate != SAMPLE_RATE:
        idx = np.linspace(0, len(a) - 1, int(len(a) * SAMPLE_RATE / rate))
        a = np.interp(idx, np.arange(len(a)), a).astype("float32")
    write_wav(path, (np.clip(a, -1, 1) * 32767).astype("<i2").tobytes())


class QwenRenderer:
    """Qwen3-TTS in the child process: design each described voice once, then clone it per turn."""
    name = "qwen3"

    def __init__(self):
        import numpy as np
        import mlx.core as mx
        from mlx_audio.tts.utils import load_model
        self.np, self.mx, self.load_model = np, mx, load_model

    @staticmethod
    def ref_path(description, line=None):
        key = hashlib.sha1(("%s|%s|%s|%s" % (QWEN_DESIGN_MODEL, description, QWEN_PACE, line or QWEN_REF_LINE)).encode("utf-8"))
        return os.path.join(QWEN_VOICE_DIR, key.hexdigest()[:16] + ".wav")

    def _audio(self, results):
        parts = [self.np.asarray(r.audio, dtype="float32").reshape(-1) for r in results]
        return self.np.concatenate(parts) if parts else None

    def design(self, voices):
        """voices: (description, line) pairs; each is designed once and kept."""
        missing = [(d, l) for d, l in voices if not os.path.exists(self.ref_path(d, l))]
        if not missing:
            return
        os.makedirs(QWEN_VOICE_DIR, exist_ok=True)
        model = self.load_model(QWEN_DESIGN_MODEL)
        for d, l in missing:
            a = self._audio(model.generate_voice_design(text=l or QWEN_REF_LINE, language="English", instruct=d + QWEN_PACE))
            if a is None:
                raise RuntimeError("Qwen3-TTS couldn't design a voice for: %s" % d)
            write_float_wav(self.ref_path(d, l), a, model.sample_rate, self.np)
            with open(self.ref_path(d, l)[:-4] + ".json", "w", encoding="utf-8") as f:
                json.dump({"description": d, "pace": QWEN_PACE, "line": l or QWEN_REF_LINE, "model": QWEN_DESIGN_MODEL}, f, indent=1)
            log("  designed a voice: %s" % d[:70])
        del model
        self.mx.clear_cache()

    def render_all(self, jobs, batch=QWEN_BATCH, window=QWEN_WINDOW):
        np = self.np
        voices = sorted({(j["voice"], j.get("line") or "") for j in jobs})
        self.design(voices)
        model = self.load_model(QWEN_BASE_MODEL)
        rate = model.sample_rate
        gap = np.zeros(int(0.12 * rate), dtype="float32")
        for voice, line in voices:
            # mlx-audio clones one shared reference per batch, so each voice's pieces are batched together.
            mine = [j for j in jobs if (j["voice"], j.get("line") or "") == (voice, line) and not os.path.exists(j["path"])]
            k = 0
            while k < len(mine):
                # A window of whole turns: sorted by length inside it so batches stay even, and written
                # as soon as it's done, so a crash loses at most one window, not the whole voice.
                group, pieces = [], []
                while k < len(mine) and (not group or len(pieces) < window):
                    group.append(mine[k])
                    pieces += [(len(group) - 1, i, p) for i, p in
                               enumerate(split_text(mine[k]["text"], max_words=QWEN_CHUNK_WORDS))]
                    k += 1
                t0 = time.time()
                audio = {}
                order = sorted(pieces, key=lambda x: len(x[2].split()))
                for start in range(0, len(order), batch):
                    chunk = order[start:start + batch]
                    out = [None] * len(chunk)
                    for r in model.batch_generate(texts=[p for _, _, p in chunk], ref_audio=self.ref_path(voice, line),
                                                  ref_text=line or QWEN_REF_LINE, lang_code="english", stream=False):
                        i = getattr(r, "sequence_idx", 0)
                        a = np.asarray(r.audio, dtype="float32").reshape(-1)
                        out[i] = a if out[i] is None else np.concatenate([out[i], a])
                    for (n, i, _), a in zip(chunk, out):
                        audio[(n, i)] = a if a is not None else np.zeros(0, dtype="float32")
                self.mx.clear_cache()
                took, secs = time.time() - t0, 0.0
                for n, j in enumerate(group):
                    parts = []
                    for i in sorted(i for (m, i) in audio if m == n):
                        if parts:
                            parts.append(gap)
                        parts.append(audio[(n, i)])
                    if not parts or sum(len(x) for x in parts) == 0:
                        raise RuntimeError("Qwen3-TTS produced no audio for %s" % j["label"])
                    joined = np.concatenate(parts)
                    write_float_wav(j["path"], joined, rate, np)
                    secs += len(joined) / float(rate)
                log("  %s done (%.0f s of audio in %.1f s)" % (
                    group[0]["label"] if len(group) == 1 else "%s and %d more" % (group[0]["label"], len(group) - 1),
                    secs, took))


class SayEngine:
    name = "say"
    workers = 4

    def __init__(self, speed, model=None, styles=None):
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

    def render(self, text, voice, path, style=None, speed=None):
        txt = path + ".txt"
        with open(txt, "w", encoding="utf-8") as f:
            f.write(text)
        tmp = path + ".say.wav"
        rate_wpm = int(self.rate_wpm * (speed or 1.0))
        subprocess.run(["say", "-v", voice, "-r", str(rate_wpm), "-f", txt, "-o", tmp,
                        "--file-format=WAVE", "--data-format=LEI16@%d" % SAMPLE_RATE], check=True)
        rate, pcm = read_wav(tmp)
        write_wav(path, pcm, rate)
        os.remove(tmp)
        os.remove(txt)


def http_request(url, body=None, headers=None, what="request", method=None):
    """POST JSON (or GET) with retries on rate limits and server errors; returns the raw body."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    h = {"User-Agent": "entalkenator-render/0.2"}
    if body is not None:
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    delay = 2.0
    for attempt in range(6):
        req = urllib.request.Request(url, data=data, method=method or ("POST" if data is not None else "GET"), headers=h)
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 5:
                time.sleep(delay)
                delay *= 2
                continue
            raise RuntimeError("%s HTTP %d: %s" % (what, e.code, e.read()[:300].decode("utf-8", "replace")))
        except urllib.error.URLError:
            if attempt < 5:
                time.sleep(delay)
                delay *= 2
                continue
            raise


def need_key(engine):
    key = voice_options.api_key(engine)
    if not key:
        raise SystemExit("No %s API key. Save one with `python3 scripts/voices.py key set %s`, or set %s."
                         % (engine, engine, voice_options.KEY_ENV[engine]))
    return key


def speed_note(speed):
    if abs(speed - 1.0) < 0.05:
        return ""
    return " Speak a little faster." if speed > 1 else " Speak a little slower."


class GeminiEngine:
    name = "gemini"
    voices = GEMINI_VOICES
    workers = 4

    def __init__(self, speed, model=None, styles=None):
        self.key = need_key("gemini")
        self.model = model or DEFAULT_MODELS["gemini"]
        self.speed = speed

    def render(self, text, voice, path, style=None):
        content = {"type": "text", "text": text}
        # The 3.8 TTS models read inline cues aloud, so delivery notes go in speech_metadata.
        content["annotations"] = [{"type": "speech_metadata", "style": (style or DEFAULT_STYLE) + speed_note(self.speed)}]
        body = {
            "model": self.model,
            "input": [{"type": "user_input", "content": [content]}],
            "response_format": {"type": "audio", "mime_type": "audio/wav", "sample_rate": SAMPLE_RATE},
            "generation_config": {"speech_config": [{"voice": voice}]},
        }
        data = json.loads(http_request(GEMINI_URL, body, {"x-goog-api-key": self.key}, "Gemini TTS").decode("utf-8"))
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


class OpenAIEngine:
    """POST /v1/audio/speech with response_format "pcm": 24 kHz 16-bit little-endian mono, no header."""
    name = "openai"
    workers = 4

    def __init__(self, speed, model=None, styles=None):
        self.key = need_key("openai")
        self.model = model or DEFAULT_MODELS["openai"]
        self.steerable = not self.model.startswith("tts-1")
        self.voices = OPENAI_VOICES if self.steerable else OPENAI_TTS1_VOICES
        self.speed = speed

    def render(self, text, voice, path, style=None):
        pcm = []
        for i, piece in enumerate(split_text(text, max_chars=3500)):
            body = {"model": self.model, "voice": voice, "input": piece, "response_format": "pcm"}
            if self.steerable:
                body["instructions"] = (style or DEFAULT_STYLE) + speed_note(self.speed)
            elif abs(self.speed - 1.0) >= 0.05:
                body["speed"] = self.speed
            raw = http_request(OPENAI_URL, body, {"Authorization": "Bearer " + self.key}, "OpenAI speech")
            if i:
                pcm.append(silence(0.15))
            pcm.append(raw[: len(raw) // 2 * 2])
        write_wav(path, b"".join(pcm))


class ElevenLabsEngine:
    """POST /v1/text-to-speech/{voice_id}?output_format=pcm_24000 (raw 16-bit mono at 24 kHz)."""
    name = "elevenlabs"
    workers = 4

    def __init__(self, speed, model=None, styles=None):
        self.key = need_key("elevenlabs")
        self.model = model or DEFAULT_MODELS["elevenlabs"]
        self.speed = speed
        self._library = None

    def library(self):
        """[(name, voice_id, labels)] for the voices this account can use."""
        if self._library is None:
            raw = http_request(ELEVENLABS_BASE + "/v1/voices", None, {"xi-api-key": self.key}, "ElevenLabs voices")
            self._library = [(v.get("name", "").strip(), v.get("voice_id"), v.get("labels") or {})
                             for v in json.loads(raw.decode("utf-8")).get("voices", []) if v.get("voice_id")]
        return self._library

    @property
    def voices(self):
        return [name for name, _, _ in self.library()]

    def describe(self, voice):
        for name, _, labels in self.library():
            if name == voice:
                return ", ".join(str(labels[k]) for k in ("gender", "age", "accent", "description", "use_case") if labels.get(k))
        return ""

    def voice_id(self, voice):
        for name, vid, _ in self.library():
            if voice in (vid, name) or name.lower() == voice.lower() or name.split(" - ")[0].lower() == voice.lower():
                return vid
        raise SystemExit("ElevenLabs has no voice called %r on this account. See --list-voices." % voice)

    def render(self, text, voice, path, style=None):
        vid = self.voice_id(voice)
        url = "%s/v1/text-to-speech/%s?output_format=pcm_24000" % (ELEVENLABS_BASE, urllib.request.quote(vid))
        pcm = []
        for i, piece in enumerate(split_text(text, max_chars=9000)):
            body = {"text": piece, "model_id": self.model}
            if abs(self.speed - 1.0) >= 0.05:
                body["voice_settings"] = {"speed": max(0.7, min(1.2, self.speed))}
            raw = http_request(url, body, {"xi-api-key": self.key}, "ElevenLabs speech")
            if i:
                pcm.append(silence(0.15))
            pcm.append(raw[: len(raw) // 2 * 2])
        write_wav(path, b"".join(pcm))


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


ENGINES = ("kokoro", "qwen3", "say", "gemini", "openai", "elevenlabs")
LOCAL_IN_CHILD = ("kokoro", "qwen3")


def engine_ready(name):
    if name == "kokoro":
        return bool(kokoro_python()) and voice_options.hf_cached(KOKORO_MODEL)
    if name == "qwen3":
        return (bool(kokoro_python()) and voice_options.hf_cached(QWEN_DESIGN_MODEL)
                and voice_options.hf_cached(QWEN_BASE_MODEL))
    if name == "say":
        return bool(shutil.which("say"))
    return bool(voice_options.api_key(name))


def pick_engine(requested, model=None):
    """(engine, model). "auto" is the person's saved choice if it's ready, else Kokoro, else say.
    It never chooses a paid engine on its own."""
    if requested and ":" in requested:
        requested, model = requested.split(":", 1)
    if requested == "auto":
        chosen = voice_options.chosen_default()
        if chosen:
            opt = voice_options.BY_ID[chosen]
            if engine_ready(opt["engine"]):
                return opt["engine"], model or opt.get("model") or DEFAULT_MODELS[opt["engine"]]
            log("Your chosen voices (%s) aren't ready on this Mac; using the free ones instead." % opt["label"])
        for name in ("kokoro", "say"):
            if engine_ready(name):
                return name, DEFAULT_MODELS[name]
        raise SystemExit("No voice engine available. Install the free voices with `python3 scripts/voices.py install kokoro`.")
    if requested not in ENGINES:
        raise SystemExit("Unknown engine %r. Choose one of: auto, %s." % (requested, ", ".join(ENGINES)))
    if requested in LOCAL_IN_CHILD and not engine_ready(requested):
        raise SystemExit("%s isn't installed. Run `python3 scripts/voices.py install %s` (it asks first)."
                         % (requested, requested))
    return requested, model or DEFAULT_MODELS[requested]


def make_engine(name, speed, model=None):
    return {"kokoro": KokoroEngine, "say": SayEngine, "gemini": GeminiEngine, "openai": OpenAIEngine,
            "elevenlabs": ElevenLabsEngine}[name](speed, model)


class VoicePool:
    def __init__(self, voices):
        self.voices = voices


def voice_pool(name, model=None):
    if name == "kokoro":
        return VoicePool(KOKORO_VOICES)
    if name == "qwen3":
        return VoicePool(QWEN_VOICES)
    if name == "gemini":
        return VoicePool(GEMINI_VOICES)
    if name == "openai":
        return VoicePool(OPENAI_TTS1_VOICES if (model or "").startswith("tts-1") else OPENAI_VOICES)
    if name == "elevenlabs":
        return make_engine("elevenlabs", 1.0, model)
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


def describe_voice(engine_name, voice, pool=None):
    if engine_name == "kokoro" and len(voice) > 3 and voice[2] == "_":
        accent = {"a": "American", "b": "British"}.get(voice[0], voice[0])
        register = {"f": "female", "m": "male"}.get(voice[1], voice[1])
        return "%s %s" % (accent, register)
    if engine_name == "say":
        loc = getattr(_say_pool(), "locale", {}).get(voice, "")
        return ("%s %s" % (LOCALE_NAMES.get(loc, loc), SAY_DESCRIPTIONS.get(voice.split(" (")[0], ""))).strip()
    if engine_name == "gemini":
        return GEMINI_DESCRIPTIONS.get(voice, "")
    if engine_name == "openai":
        return OPENAI_DESCRIPTIONS.get(voice, "")
    if engine_name == "qwen3":
        return voice
    if engine_name == "elevenlabs" and hasattr(pool, "describe"):
        return pool.describe(voice)
    return ""


def local_worker(engine_name, jobs_file, speed):
    """Child process: render the listed clips, then exit without interpreter teardown.

    MLX has crashed natively after a long render (no traceback, no output), so the
    parent never loads it: the clips land on disk and the parent stitches them.
    """
    with open(jobs_file, "r", encoding="utf-8") as f:
        jobs = [j for j in json.load(f) if not os.path.exists(j["path"])]
    if engine_name == "qwen3":
        QwenRenderer().render_all(jobs)
    else:
        import mlx.core as mx
        engine = KokoroEngine(speed)
        clear = os.environ.get("ENTALKENATOR_KOKORO_CLEAR", "1") != "0"
        for j in jobs:
            t0 = time.time()
            engine.render(j["text"], j["voice"], j["path"], speed=j.get("speed"))
            if clear:
                mx.clear_cache()  # MLX's buffer cache otherwise grows over a long render and slows it down
            with wave.open(j["path"]) as w:
                secs = w.getnframes() / float(w.getframerate())
            log("  %s done (%.0f s of audio in %.1f s)" % (j["label"], secs, time.time() - t0))
    sys.stderr.flush()
    os._exit(0)


def render_in_child(engine_name, todo, speed, work):
    """todo: dicts with text, voice, path, label."""
    jobs_file = os.path.join(work, "jobs.json")
    with open(jobs_file, "w", encoding="utf-8") as f:
        json.dump(todo, f)
    cmd = [kokoro_python(), "-X", "faulthandler", os.path.abspath(__file__), "--worker", jobs_file,
           "--engine", engine_name, "--speed", str(speed)]
    label = {"kokoro": "Kokoro", "qwen3": "Qwen3-TTS"}[engine_name]
    missing = todo
    for attempt in (1, 2):
        rc = subprocess.run(cmd, stdout=subprocess.DEVNULL).returncode
        missing = [j for j in todo if not os.path.exists(j["path"])]
        if not missing:
            break
        log("%s worker exited with code %s and %d clip(s) missing%s" %
            (label, rc, len(missing), "; retrying once" if attempt == 1 else ""))
    if missing:
        raise SystemExit("%s could not render %d clip(s). Re-run to retry; finished clips are kept." % (label, len(missing)))


def render_jobs(engine_name, model, jobs, speed, work):
    """Render every job whose clip isn't cached yet. Returns how many were rendered."""
    todo = [j for j in jobs if not os.path.exists(j["path"])]
    if todo and engine_name in LOCAL_IN_CHILD:
        render_in_child(engine_name, todo, speed, work)
    elif todo:
        engine = make_engine(engine_name, speed, model)

        def run(job):
            if job.get("speed") and engine_name == "say":
                engine.render(job["text"], job["voice"], job["path"], job.get("style"), speed=job["speed"])
            else:
                engine.render(job["text"], job["voice"], job["path"], job.get("style"))
            log("  %s done" % job["label"])

        if engine.workers > 1 and len(todo) > 1:
            with ThreadPoolExecutor(engine.workers) as ex:
                list(ex.map(run, todo))
        else:
            for j in todo:
                run(j)
    return len(todo)


def pace_outliers(jobs, spans, roles, fast=1.25, slow=0.7):
    """Turns spoken much faster or slower than that speaker's usual pace. A synthetic voice that
    drops a turn's last words, or stalls and repeats, shows up here; nobody has to listen for it."""
    rows = []
    for i, (j, (a, b)) in enumerate(zip(jobs, spans)):
        words = len(j["text"].split())
        if words >= 12 and b > a:
            rows.append((i, roles[i], words, words * 60.0 / (b - a)))

    def median(xs):
        xs = sorted(xs)
        return xs[len(xs) // 2] if len(xs) % 2 else (xs[len(xs) // 2 - 1] + xs[len(xs) // 2]) / 2

    everyone = [r[3] for r in rows]
    out = []
    for i, role, words, wpm in rows:
        mine = [r[3] for r in rows if r[1] == role]
        usual = median(mine) if len(mine) >= 3 else (median(everyone) if len(everyone) >= 3 else None)
        if usual and (wpm > usual * fast or wpm < usual * slow):
            out.append({"turn": i + 1, "speaker": role, "words": words, "wpm": round(wpm), "usual": round(usual),
                        "why": "much faster than usual: words may be missing" if wpm > usual * fast
                        else "much slower than usual: a long pause or repeated words"})
    return out


def clip_path(work, prefix, engine_name, model, voice, speed, style, text):
    key = hashlib.sha1(("%s|%s|%s|%s|%s|%s" % (engine_name, model, voice, speed, style or "", text)).encode("utf-8"))
    return os.path.join(work, "%s-%s.wav" % (prefix, key.hexdigest()[:12]))


def turn_gaps(turns, deliveries, base=0.45):
    """The pause after each turn, fitted to the conversation: a quick comeback follows almost at once,
    a question gets a beat, a long answer a breath, and a turn marked as cutting in overlaps slightly.
    A little seeded variation keeps it from sounding metronomic."""
    gaps = []
    for i, (role, text) in enumerate(turns):
        if i + 1 == len(turns):
            gaps.append(0.0)
            break
        nrole, ntext = turns[i + 1]
        words = len(ntext.split())
        if (deliveries.get(i + 2) or {}).get("cutsIn"):
            gaps.append(-0.18)
            continue
        if nrole == role:
            g = 0.35  # the same speaker going on
        elif words <= 8:
            g = 0.22
        elif text.rstrip().endswith("?"):
            g = 0.6
        elif words >= 150:
            g = 0.6
        else:
            g = base
        jitter = (int(hashlib.sha1(("%d|%s" % (i, ntext[:40])).encode("utf-8")).hexdigest()[:4], 16) / 65535.0 - 0.5) * 0.1
        gaps.append(round(g + jitter, 3))
    return gaps


def mix_into(buf, clip, at):
    """Add 16-bit samples of clip into bytearray buf starting at sample `at`, extending buf as needed."""
    a = array.array("h")
    a.frombytes(bytes(buf[at * 2:]))
    b = array.array("h")
    b.frombytes(clip)
    for k in range(min(len(a), len(b))):
        b[k] = max(-32768, min(32767, a[k] + b[k]))
    del buf[at * 2:]
    buf.extend(b.tobytes())


def stitch(paths, gaps, out, bitrate, work):
    """Join 16-bit mono clips, each followed by its gap (seconds; negative overlaps the next clip),
    and encode to .m4a. Returns (total seconds, [(start, end) seconds of each clip in the output])."""
    buf = bytearray(silence(0.3))
    spans = []
    pending = 0.0
    for path, gap in zip(paths, gaps):
        rate, clip = read_wav(path)
        if rate != SAMPLE_RATE:
            raise SystemExit("Clip %s is %d Hz, expected %d" % (path, rate, SAMPLE_RATE))
        clip = normalize_pcm16(clip)
        start = len(buf) // 2
        if pending < 0:
            start = max(start - min(int(-pending * SAMPLE_RATE), len(clip) // 4), 0)
            mix_into(buf, clip, start)
        else:
            buf.extend(clip)
        spans.append((start / SAMPLE_RATE, (start + len(clip) // 2) / SAMPLE_RATE))
        if gap > 0:
            buf.extend(silence(gap))
        pending = gap
    buf.extend(silence(0.3))
    joined = bytes(buf)
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
    if not role:
        return words
    return "%s: %s" % (role, words) if words else role


HEADING_RE = re.compile(r"^[ \t]*(#{1,4})[ \t]+(.+?)[ \t]*$", re.M)


def write_chapters(path, turns, spans, seconds, engine_name, model, voices, transcript, plan=None, depth=4):
    """Podcasting 2.0 chapters, plus every turn's timing for the library.

    A narration (turns with "# Heading" lines) gets one chapter per heading, so podcast apps
    list the document's own outline, not its footnotes and figures. A conversation gets the
    chapters the showrunner chose (plan: [{"turn", "title"}], from engine.py chapters), or one
    chapter per turn ("Moderator: Welcome…") without a plan. Either way
    entalkenator.turnTimes holds each turn's start, end and speaker, which the library uses to
    make the transcript follow the audio.
    """
    turn_times = [{"start": 0 if i == 0 else round(start, 3), "end": round(end, 3), "speaker": role}
                  for i, ((role, _), (start, end)) in enumerate(zip(turns, spans))]
    chapters = []
    if any(HEADING_RE.search(text) for _, text in turns):
        for i, ((role, text), (start, end)) in enumerate(zip(turns, spans)):
            spoken_total = max(1, len(speakable(text).split()))
            for m in HEADING_RE.finditer(text):
                if len(m.group(1)) > depth:
                    continue  # deeper than the person wants in their chapter list
                # A heading partway into a turn starts in proportion to the words before it.
                before = len(speakable(text[:m.start()]).split())
                t = start + (end - start) * before / float(spoken_total)
                chapters.append({"startTime": round(t, 3), "title": chapter_title("", m.group(2), 80).lstrip(": ")})
        if not chapters:
            chapters.append({"startTime": 0, "title": "Opening"})
        if chapters[0]["startTime"] < 1:
            chapters[0]["startTime"] = 0
        else:  # text before the first heading
            chapters.insert(0, {"startTime": 0, "title": "Opening"})
        # A heading followed at once by a subheading ("Results", then its first subsection)
        # would be a chapter of a second or two: make them one ("Results: Accurate prediction…").
        merged = []
        for c in chapters:
            if merged and c["startTime"] - merged[-1]["startTime"] < 6:
                merged[-1]["title"] = chapter_title("", merged[-1]["title"] + ": " + c["title"], 90).lstrip(": ")
            else:
                merged.append(c)
        chapters = merged
        for c, nxt in zip(chapters, chapters[1:] + [None]):
            c["endTime"] = round(nxt["startTime"] if nxt else seconds, 3)
    elif plan:
        marks = sorted((c for c in plan if 1 <= int(c["turn"]) <= len(turns)), key=lambda c: int(c["turn"]))
        for c in marks:
            chapters.append({"startTime": turn_times[int(c["turn"]) - 1]["start"], "title": c["title"], "turn": int(c["turn"])})
        if not chapters or chapters[0]["startTime"] > 0:
            chapters.insert(0, {"startTime": 0, "title": "Opening", "turn": 1})
        for c, nxt in zip(chapters, chapters[1:] + [None]):
            c["endTime"] = round(nxt["startTime"] if nxt else seconds, 3)
    else:
        for i, ((role, text), tt) in enumerate(zip(turns, turn_times)):
            chapters.append({"startTime": tt["start"], "endTime": tt["end"], "title": chapter_title(role, speakable(text)),
                             "speaker": role, "turn": i + 1})
    doc = {
        "version": "1.2.0",
        "chapters": chapters,
        # Extra fields for the library; podcast apps ignore them.
        "entalkenator": {
            "durationSeconds": round(seconds, 3),
            "engine": engine_name,
            "model": model,
            "voices": voices,
            "transcript": os.path.basename(transcript),
            "turnCount": len(turn_times),
            "turnTimes": turn_times,
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


def audition(args, engine_name, model, pool, turns, roles, styles):
    """Per role, one short .m4a: each candidate voice reads the role's first line."""
    out_dir = os.path.abspath(args.out)
    work = os.path.abspath(args.work_dir or os.path.join(out_dir, "audition.clips"))
    os.makedirs(work, exist_ok=True)
    wanted = {}
    for part in re.split(r"[;\n]", args.candidates or ""):
        if "=" in part:
            role, vs = part.split("=", 1)
            # Qwen3-TTS candidates are descriptions with commas in them, so they're split on "|".
            wanted[role.strip()] = [v.strip() for v in vs.split("|" if "|" in vs else ",") if v.strip()]
    first_line = {}
    for role, text in turns:
        first_line.setdefault(role, sample_line(speakable(text)))
    plan, jobs = [], []
    for role in roles:
        cands = wanted.get(role) or pool.voices[:4]
        entry = {"role": role, "sample": first_line[role], "order": []}
        for n, voice in enumerate(cands, 1):
            path = clip_path(work, "aud", engine_name, model, voice, args.speed, styles.get(role), first_line[role])
            jobs.append({"text": first_line[role], "voice": voice, "path": path, "style": styles.get(role),
                         "label": "%s in %s" % (role, voice[:40])})
            entry["order"].append({"n": n, "voice": voice, "sounds": describe_voice(engine_name, voice, pool), "path": path})
        plan.append(entry)
    rendered = render_jobs(engine_name, model, jobs, args.speed, work)
    for entry in plan:
        f = os.path.join(out_dir, "audition-%s.m4a" % re.sub(r"[^A-Za-z0-9]+", "-", entry["role"]).strip("-").lower())
        paths = [o.pop("path") for o in entry["order"]]
        entry["seconds"] = round(stitch(paths, [0.9] * len(paths), f, args.bitrate, work)[0], 1)
        entry["file"] = f
    emit({"engine": engine_name, "model": model, "rendered": rendered, "auditions": plan,
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


def embed_chapters(m4a, chapters_json):
    """Put the chapters inside the .m4a too: Overcast and Apple Podcasts read only those."""
    if not m4a.lower().endswith((".m4a", ".m4b", ".mp4")):
        return False
    try:
        import mp4chapters
        with open(chapters_json, encoding="utf-8") as f:
            doc = json.load(f)
        mp4chapters.add_chapters(m4a, [(c["startTime"], c["title"]) for c in doc["chapters"]])
        return True
    except Exception as e:  # the audio is fine without them; say so and carry on
        log("Couldn't embed chapters in the .m4a (%s); the separate chapters file still works." % e)
        return False


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
        engine = sys.argv[sys.argv.index("--engine") + 1] if "--engine" in sys.argv else "kokoro"
        local_worker(engine, sys.argv[2], speed)
        return
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--transcript", help='file of "[Name]: text" turns')
    ap.add_argument("--out", help="output .m4a")
    ap.add_argument("--engine", default="auto",
                    help="auto, kokoro, qwen3, say, gemini, openai or elevenlabs; or an option id like "
                         "gemini:gemini-3.8-flash-lite-tts (see voices.py list)")
    ap.add_argument("--model", help="the engine's model, e.g. tts-1, gemini-3.8-flash-lite-tts, eleven_v4_turbo")
    ap.add_argument("--voices", help='overrides, e.g. "Hillford Johns=bm_george;The Author=af_heart". '
                                     "For qwen3 each value is a description of the voice.")
    ap.add_argument("--styles", help='delivery notes per role, e.g. "Judge=dry, unhurried;Counsel=quick, confident" '
                                     "(gemini and gpt-4o-mini-tts)")
    ap.add_argument("--chapter-plan", help="chapters-plan.json from engine.py chapters: where a conversation's chapters go")
    ap.add_argument("--chapter-depth", type=int, default=4,
                    help="narrations: chapters for headings down to this level (1 = top level only; default all)")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--redo", help="turn numbers to render again, e.g. 5,23")
    ap.add_argument("--gap", type=float, help="seconds of silence between turns (default: fitted to the conversation)")
    ap.add_argument("--lines", help='qwen3: the line each designed voice reads, in character, e.g. '
                                    '"Professor Swift=Well, that is a brave word for it."')
    ap.add_argument("--speeds", help='kokoro and say: a role\'s pace, e.g. "Professor Swift=0.95;Rigel=1.05"')
    ap.add_argument("--delivery", help="per-turn delivery notes (default: delivery.json beside the transcript)")
    ap.add_argument("--bitrate", type=int, default=48000)
    ap.add_argument("--work-dir", help="clip cache (default: <out>.clips/)")
    ap.add_argument("--list-voices", action="store_true")
    ap.add_argument("--audition", action="store_true",
                    help="instead of the show, write audition-<role>.m4a files into --out (a directory)")
    ap.add_argument("--candidates", help='audition voices, e.g. "Janson Skullbottom=bm_george,am_fenrir;..." '
                                         '(qwen3 descriptions are separated by "|"; default: the engine\'s first '
                                         "four voices for every role)")
    args = ap.parse_args()

    engine_name, model = pick_engine(args.engine, args.model)
    pool = voice_pool(engine_name, model)
    if args.list_voices:
        listing = {"engine": engine_name, "model": model,
                   "voices": [{"voice": v, "sounds": describe_voice(engine_name, v, pool)} for v in pool.voices],
                   "default order": "roles get these in order of first appearance unless --voices says otherwise"}
        if engine_name == "qwen3":
            listing["note"] = ("Qwen3-TTS voices are descriptions, and these are only fallbacks. Describe each role "
                               "instead (gender, age, accent, temperament, pace) and pass them with --voices.")
        if engine_name in ("gemini", "openai", "elevenlabs"):
            listing["note"] = "Gender and character are as the provider describes them or as commonly heard; audition when unsure."
        emit(listing)
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
    styles = parse_overrides(args.styles)
    if args.audition:
        audition(args, engine_name, model, pool, turns, roles, styles)
        return
    voices = assign_voices(roles, pool, parse_overrides(args.voices))
    lines = parse_overrides(args.lines)
    speeds = {r: float(v) for r, v in parse_overrides(args.speeds).items()}
    dfile = args.delivery or os.path.join(os.path.dirname(os.path.abspath(args.transcript)), "delivery.json")
    deliveries = {}
    if os.path.isfile(dfile):
        with open(dfile, encoding="utf-8") as f:
            deliveries = {d["turn"]: d for d in json.load(f).get("turns", [])}

    out = os.path.abspath(args.out)
    work = os.path.abspath(args.work_dir or (os.path.splitext(out)[0] + ".clips"))
    os.makedirs(work, exist_ok=True)

    jobs = []
    for i, (role, text) in enumerate(turns):
        spoken = speakable(text)
        # Only engines that take direction get a note, so adding notes doesn't re-render the others.
        note = (deliveries.get(i + 1) or {}).get("note")
        style = "; ".join(x for x in (styles.get(role), note) if x) or None
        style = style if engine_name in ("gemini", "openai") else styles.get(role)
        speed = args.speed * speeds.get(role, 1.0) if engine_name in ("kokoro", "say") else args.speed
        line = lines.get(role) if engine_name == "qwen3" else None
        job = {"text": spoken, "voice": voices[role], "style": style,
               "label": "turn %d/%d (%s)" % (i + 1, len(turns), role),
               "path": clip_path(work, "%03d" % (i + 1), engine_name, model,
                                 voices[role] + ("|" + line if line else ""), speed, style, spoken)}
        if speed != args.speed:
            job["speed"] = speed
        if line:
            job["line"] = line
        jobs.append(job)
    if args.redo:
        for n in [int(x) for x in args.redo.replace(" ", "").split(",") if x]:
            if not 1 <= n <= len(jobs):
                raise SystemExit("--redo %d: the transcript has %d turns." % (n, len(jobs)))
            if os.path.exists(jobs[n - 1]["path"]):
                os.remove(jobs[n - 1]["path"])
    log("%s (%s): %d turns, voices %s" % (engine_name, model, len(jobs), voices))
    t1 = time.time()
    rendered = render_jobs(engine_name, model, jobs, args.speed, work)
    render_s = time.time() - t1
    gaps = [args.gap] * len(jobs) if args.gap is not None else turn_gaps(turns, deliveries)
    seconds, spans = stitch([j["path"] for j in jobs], gaps, out, args.bitrate, work)
    chapters = os.path.splitext(out)[0] + ".chapters.json"
    plan = None
    if args.chapter_plan:
        with open(args.chapter_plan, encoding="utf-8") as f:
            plan = json.load(f).get("chapters")
    write_chapters(chapters, turns, spans, seconds, engine_name, model, voices, args.transcript, plan, args.chapter_depth)
    embedded = embed_chapters(out, chapters)

    emit({
        "out": out,
        "engine": engine_name,
        "model": model,
        "voices": voices,
        "turns": len(jobs),
        "rendered": rendered,
        "durationSeconds": round(seconds, 1),
        "renderSeconds": round(render_s, 1),
        "chapters": chapters,
        "embeddedChapters": embedded,
        "clips": work,
        "checkTurns": pace_outliers(jobs, spans, [r for r, _ in turns]),
    })


if __name__ == "__main__":
    main()
