#!/usr/bin/env python3
"""Checks the voice options without real accounts: scripts/voices.py (catalogue, keys, default)
and the cloud engines in render_audio.py (OpenAI, ElevenLabs, Gemini) against a stand-in server
that checks each request the way the real API documents it and answers with a short tone.

    python3 tests/test_voices.py

Touches no real keys, Keychain or settings: `security` is replaced by tests/fake_security.py, and
everything else goes to a temporary folder.
"""

import base64
import io
import json
import math
import os
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VOICES = os.path.join(ROOT, "scripts", "voices.py")
RENDER = os.path.join(ROOT, "scripts", "render_audio.py")
FAKE_SECURITY = os.path.join(HERE, "fake_security.py")
GOOD = {"openai": "sk-test-0123456789abcdefghijklmnop", "elevenlabs": "xi-test-0123456789abcdefghijkl",
        "gemini": "AIza-test-0123456789abcdefghijkl"}

passed, failed = 0, []
seen = []  # (path, headers, body) of every request the stand-in received


def check(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print("  ok   " + name)
    else:
        failed.append(name)
        print("  FAIL %s %s" % (name, detail))


def tone(seconds=0.4, rate=24000):
    return b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 220 * i / rate))) for i in range(int(seconds * rate)))


def wav_bytes(pcm, rate=24000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


ELEVEN_VOICES = [
    {"voice_id": "VOICEaaaaaaaaaaaaaaa", "name": "George", "labels": {"gender": "male", "accent": "british", "age": "middle aged"}},
    {"voice_id": "VOICEbbbbbbbbbbbbbbb", "name": "Sarah", "labels": {"gender": "female", "accent": "american", "age": "young"}},
    {"voice_id": "VOICEccccccccccccccc", "name": "Brian", "labels": {"gender": "male", "accent": "american"}},
]


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        u = urlparse(self.path)
        seen.append((u.path, dict(self.headers), None))
        if u.path == "/v1/models":
            ok = self.headers.get("Authorization") == "Bearer " + GOOD["openai"]
            return self.reply(200 if ok else 401, {"data": []} if ok else {"error": {"message": "bad key"}})
        if u.path == "/v1/voices":
            ok = self.headers.get("xi-api-key") == GOOD["elevenlabs"]
            return self.reply(200 if ok else 401, {"voices": ELEVEN_VOICES} if ok else {"detail": "bad key"})
        if u.path == "/v1beta/models":
            ok = self.headers.get("x-goog-api-key") == GOOD["gemini"]
            return self.reply(200 if ok else 400, {"models": []} if ok else {"error": "API key not valid"})
        self.reply(404, {})

    def do_POST(self):
        u = urlparse(self.path)
        body = self.body()
        seen.append((u.path + ("?" + u.query if u.query else ""), dict(self.headers), body))
        if u.path == "/v1/audio/speech":
            if self.headers.get("Authorization") != "Bearer " + GOOD["openai"]:
                return self.reply(401, {"error": "bad key"})
            if body.get("response_format") != "pcm" or not body.get("input") or not body.get("voice"):
                return self.reply(400, {"error": "bad request"})
            return self.reply(200, tone(), "application/octet-stream")
        if u.path.startswith("/v1/text-to-speech/"):
            if self.headers.get("xi-api-key") != GOOD["elevenlabs"]:
                return self.reply(401, {"detail": "bad key"})
            if parse_qs(u.query).get("output_format") != ["pcm_24000"] or not body.get("model_id"):
                return self.reply(422, {"detail": "bad request"})
            return self.reply(200, tone(), "application/octet-stream")
        if u.path == "/v1beta/interactions":
            if self.headers.get("x-goog-api-key") != GOOD["gemini"]:
                return self.reply(400, {"error": "bad key"})
            audio = base64.b64encode(wav_bytes(tone())).decode()
            return self.reply(200, {"steps": [{"content": [{"type": "audio", "data": audio}]}]})
        self.reply(404, {})


TRANSCRIPT = """[Judge]: Counsel, what does the statute actually require here?

[Counsel]: It requires notice, Your Honor, and notice was given. The record shows it twice.

[Judge]: Then why did the agency say otherwise?

[Counsel]: Because it read the wrong page. Page four, not page two.
"""


def main():
    tmp = tempfile.mkdtemp(prefix="entalk-voices-")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % server.server_port
    env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "GEMINI_API_KEY", "ELEVENLABS_API_KEY")}
    store = os.path.join(tmp, "keychain.json")
    env.update({"ENTALKENATOR_CONFIG_DIR": os.path.join(tmp, "config"),
                "ENTALKENATOR_SECURITY": " ".join(shlex.quote(x) for x in (sys.executable, FAKE_SECURITY)),
                "FAKE_SECURITY_STORE": store,
                "ENTALKENATOR_OPENAI_BASE": base, "ENTALKENATOR_ELEVENLABS_BASE": base, "ENTALKENATOR_GEMINI_BASE": base})

    def keychain():
        try:
            with open(store) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"items": {}, "argv": []}
    transcript = os.path.join(tmp, "transcript.txt")
    with open(transcript, "w") as f:
        f.write(TRANSCRIPT)

    def run(script, *args, stdin=None):
        p = subprocess.run([sys.executable, script] + list(args), input=stdin, capture_output=True, text=True, env=env)
        try:
            return p.returncode, json.loads(p.stdout), p.stdout + p.stderr
        except ValueError:
            return p.returncode, {}, p.stdout + p.stderr

    try:
        print("catalogue")
        code, out, _ = run(VOICES, "list", "--words", "5250")
        ids = [o["id"] for o in out.get("options", [])]
        check("lists every option", {"kokoro", "qwen3", "say", "gemini:gemini-3.8-flash-tts", "openai:tts-1",
                                     "elevenlabs:eleven_v4"} <= set(ids), ids)
        by = {o["id"]: o for o in out["options"]}
        check("30 minutes of Gemini Flash costs about $0.41", abs(by["gemini:gemini-3.8-flash-tts"]["estimate"]["costUSD"] - 0.41) < 0.02)
        check("cloud options without a key say so, with a link", by["openai:tts-1"]["needs"][0]["what"] == "key"
              and "platform.openai.com" in by["openai:tts-1"]["needs"][0]["url"])
        check("free options cost nothing", by["kokoro"]["estimate"]["costUSD"] == 0)

        print("keys")
        code, out, raw = run(VOICES, "key", "set", "openai", "--stdin", stdin="sk-wrong-0123456789abcdefghijklmnop\n")
        check("a rejected key isn't saved", code == 1 and not keychain()["items"], out)
        code, out, raw = run(VOICES, "key", "set", "openai", "--stdin", stdin=GOOD["openai"] + "\n")
        kc = keychain()
        check("a working key is checked and saved in the Keychain", code == 0 and out.get("checked") is True
              and kc["items"].get("enTalkenator|openai", {}).get("password") == GOOD["openai"], out)
        check("...with a label people can find in Keychain Access", "enTalkenator" in kc["items"]["enTalkenator|openai"]["label"])
        check("the key never appears in the output", GOOD["openai"] not in raw)
        check("the key is never on a command line (only on stdin)", not any(GOOD["openai"] in " ".join(a) for a in kc["argv"]))
        check("nothing is written to the settings folder", not os.path.exists(os.path.join(tmp, "config", "keys.json")))
        for eng in ("elevenlabs", "gemini"):
            code, out, raw = run(VOICES, "key", "set", eng, "--stdin", stdin=GOOD[eng] + "\n")
            check("%s key saved" % eng, code == 0 and out.get("checked") is True and GOOD[eng] not in raw, out)
        code, out, _ = run(VOICES, "key", "check", "gemini")
        check("key check works and names the Keychain", code == 0 and out.get("ok") and out.get("from") == "Keychain", out)
        code, out, _ = run(VOICES, "key", "set", "openai", "--stdin", stdin='sk-test-quote"inside-0123456789abc\n')
        check("a key with a quote in it is refused rather than mangled", code == 1, out)
        code, out, _ = run(VOICES, "key", "set", "openai", "--stdin", stdin="short\n")
        check("an obviously incomplete key is refused", code == 1, out)

        print("auto never chooses a paid engine")
        code, out, raw = run(RENDER, "--list-voices")
        check("with keys saved but no choice made, auto stays free", code == 0 and out.get("engine") in ("kokoro", "say"), raw[-300:])
        code, out, _ = run(VOICES, "default", "set", "openai:tts-1")
        check("default set", code == 0 and out["ready"], out)
        code, out, _ = run(RENDER, "--list-voices")
        check("auto follows the person's choice", out.get("engine") == "openai" and out.get("model") == "tts-1"
              and len(out["voices"]) == 9, out)

        print("OpenAI gpt-4o-mini-tts")
        seen.clear()
        mp = os.path.join(tmp, "openai.m4a")
        code, out, raw = run(RENDER, "--transcript", transcript, "--out", mp, "--engine", "openai", "--model", "gpt-4o-mini-tts",
                             "--voices", "Judge=cedar;Counsel=marin", "--styles", "Judge=dry and unhurried")
        reqs = [b for p, h, b in seen if p == "/v1/audio/speech"]
        check("renders an .m4a with chapters", code == 0 and os.path.exists(mp) and out.get("turns") == 4, raw[-400:])
        check("one request per turn, raw PCM", len(reqs) == 4 and all(r["response_format"] == "pcm" for r in reqs))
        check("each role's delivery note becomes its instructions",
              all(r["instructions"].startswith("dry and unhurried") for r in reqs if r["voice"] == "cedar")
              and all(r["instructions"].startswith("Natural") for r in reqs if r["voice"] == "marin"), reqs)
        with open(os.path.splitext(mp)[0] + ".chapters.json") as f:
            ch = json.load(f)
        check("chapters record engine and model", ch["entalkenator"]["engine"] == "openai"
              and ch["entalkenator"]["model"] == "gpt-4o-mini-tts")

        print("OpenAI tts-1")
        seen.clear()
        code, out, raw = run(RENDER, "--transcript", transcript, "--out", os.path.join(tmp, "tts1.m4a"), "--engine", "openai:tts-1",
                             "--speed", "1.1")
        reqs = [b for p, h, b in seen if p == "/v1/audio/speech"]
        check("tts-1 gets no instructions, and speed as a number", code == 0 and reqs and all("instructions" not in r and r.get("speed") == 1.1 for r in reqs), reqs[:1])
        check("tts-1 casts from its own nine voices", set(out["voices"].values()) <= {"onyx", "nova", "echo", "shimmer", "fable", "coral", "ash", "sage", "alloy"}, out.get("voices"))

        print("ElevenLabs")
        code, out, _ = run(RENDER, "--list-voices", "--engine", "elevenlabs")
        check("lists the account's voices with their labels", code == 0 and [v["voice"] for v in out["voices"]] == ["George", "Sarah", "Brian"]
              and "british" in out["voices"][0]["sounds"], out)
        seen.clear()
        code, out, raw = run(RENDER, "--transcript", transcript, "--out", os.path.join(tmp, "eleven.m4a"), "--engine", "elevenlabs",
                             "--model", "eleven_v4_turbo", "--voices", "Judge=George;Counsel=sarah")
        tts = [(p, b) for p, h, b in seen if p.startswith("/v1/text-to-speech/")]
        check("voices are found by name, case-insensitively", code == 0 and {p.split("/")[3].split("?")[0] for p, _ in tts}
              == {"VOICEaaaaaaaaaaaaaaa", "VOICEbbbbbbbbbbbbbbb"}, raw[-300:])
        check("requests ask for 24 kHz PCM with the chosen model", all("output_format=pcm_24000" in p and b["model_id"] == "eleven_v4_turbo" for p, b in tts))
        code, out, raw = run(RENDER, "--transcript", transcript, "--out", os.path.join(tmp, "eleven2.m4a"), "--engine", "elevenlabs",
                             "--voices", "Judge=Nobody")
        check("an unknown voice name stops with a clear message", code != 0 and "no voice called" in raw, raw[-200:])

        print("Gemini Flash-Lite")
        seen.clear()
        code, out, raw = run(RENDER, "--transcript", transcript, "--out", os.path.join(tmp, "gemini.m4a"),
                             "--engine", "gemini:gemini-3.8-flash-lite-tts", "--styles", "Counsel=quick, confident")
        reqs = [b for p, h, b in seen if p == "/v1beta/interactions"]
        styles = {r["generation_config"]["speech_config"][0]["voice"]: r["input"][0]["content"][0]["annotations"][0]["style"] for r in reqs}
        check("uses the chosen model and WAV replies", code == 0 and reqs and all(r["model"] == "gemini-3.8-flash-lite-tts" for r in reqs), raw[-300:])
        check("delivery notes go in speech_metadata", "quick, confident" in styles.get(out["voices"]["Counsel"], ""), styles)

        print("mixing engines, and recorded voices")
        seen.clear()
        code, out, raw = run(RENDER, "--transcript", transcript, "--out", os.path.join(tmp, "mixed.m4a"),
                             "--engine", "say", "--engines", "Counsel=gemini:gemini-3.8-flash-lite-tts")
        reqs = [b for p, h, b in seen if p == "/v1beta/interactions"]
        check("each role can have its own engine", code == 0 and out["engine"] == "mixed"
              and out["engines"] == {"Judge": "say", "Counsel": "gemini"} and len(reqs) == 2, raw[-300:])
        chap = json.load(open(os.path.join(tmp, "mixed.chapters.json")))["entalkenator"]
        check("...and the chapters file says which", chap["engine"] == "mixed" and "Counsel: gemini" in chap["model"], chap)
        clip = os.path.join(tmp, "me.aiff")
        subprocess.run(["say", "-o", clip, "Thanks for having me. I will be honest about this paper."], check=True)
        code, out, _ = run(VOICES, "recorded", "add", "Test Voice", "--audio", clip)
        check("a recording isn't kept until it's said whose voice it is", code == 1 and "--whose" in out["error"], out)
        code, out, _ = run(VOICES, "recorded", "add", "Test Voice", "--audio", clip, "--whose", "consented")
        check("...and someone else's needs their consent stated", code == 1 and "--consent" in out["error"], out)
        code, out, _ = run(VOICES, "recorded", "add", "Test Voice", "--audio", clip, "--whose", "own",
                           "--transcript", "Thanks for having me. I will be honest about this paper.")
        check("a recording is kept as a voice, converted for the engines", code == 0 and out["ok"]
              and out["use"] == '--voices "Role=@Test Voice"' and out["seconds"] > 1, out)
        code, out, _ = run(VOICES, "recorded", "list")
        check("...listed with whose it is", [v["name"] for v in out["voices"]] == ["Test Voice"]
              and out["voices"][0]["whose"] == "own", out)
        code, out, raw = run(RENDER, "--transcript", transcript, "--out", os.path.join(tmp, "rec.m4a"), "--engine", "say",
                             "--voices", "Judge=@Test Voice")
        check("a recorded voice needs an engine that can clone", code != 0 and "needs an engine that can clone" in raw
              and 'Judge=breeze' in raw, raw[-300:])
        code, out, _ = run(VOICES, "recorded", "remove", "Test Voice")
        code2, out2, _ = run(VOICES, "recorded", "list")
        check("...and can be removed", out.get("ok") and out2["voices"] == [], (out, out2))

        print("key removal")
        code, out, _ = run(VOICES, "key", "remove", "openai")
        check("remove deletes the Keychain item", out.get("removed") is True and "enTalkenator|openai" not in keychain()["items"], out)
        code, out, raw = run(RENDER, "--transcript", transcript, "--out", os.path.join(tmp, "x.m4a"), "--engine", "openai")
        check("without a key, the engine stops and says how to add one", code != 0 and "voices.py key set openai" in raw, raw[-200:])
        code, out, raw = run(RENDER, "--list-voices")
        check("auto falls back to free voices when the chosen one has no key", out.get("engine") in ("kokoro", "say") and "aren't ready" in raw, raw[-300:])

        print("render times measured on this Mac, not quoted from another")
        home = os.path.join(tmp, "home")
        os.makedirs(home)
        henv = dict(env, ENTALKENATOR_HOME=home)

        def est(opt_id):
            p = subprocess.run([sys.executable, VOICES, "list", "--words", "1750"], capture_output=True, text=True, env=henv)
            return next(o for o in json.loads(p.stdout)["options"] if o["id"] == opt_id)["estimate"]
        e = est("breeze")
        check("before any render here, a local voice gives its relative work, not a time",
              "renderMinutes" not in e and "about 20 times Kokoro's work" in e["renderNote"], e)
        with open(os.path.join(home, "speeds.json"), "w") as f:
            json.dump({"kokoro": [{"ratio": 0.08}, {"ratio": 0.1}, {"ratio": 0.09}]}, f)
        e = est("breeze")
        check("after Kokoro has rendered here, the others are guessed from its speed",
              e.get("renderMinutes") == 18.0 and "rough guess" in e["renderNote"], e)
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        os.environ["ENTALKENATOR_HOME"] = home
        import importlib
        import voices as vmod
        importlib.reload(vmod)
        vmod.record_speed("breeze", 30, 45)
        vmod.record_speed("breeze", 600, 840)
        e = est("breeze")
        check("an engine's own renders here replace the guess (short ones don't count)",
              e.get("renderMinutes") == 14.0 and "this Mac's earlier renders" in e["renderNote"], e)
        p = subprocess.run([sys.executable, VOICES, "machine"], capture_output=True, text=True, env=henv)
        m = json.loads(p.stdout)
        check("machine lists the local voices with their work and whether they fit",
              [v["id"] for v in m["localVoices"]] == ["kokoro", "qwen3", "qwen3-preset", "breeze", "say"]
              and m["localVoices"][3]["measured"] == 1.4 and "memoryGB" in m["hardware"], m)
        e = est("gemini:gemini-3.8-flash-tts")
        check("a cloud voice needs no compute here", "renderMinutes" not in e and e["costUSD"] > 0, e)
    finally:
        server.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d passed, %d failed" % (passed, len(failed)))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
