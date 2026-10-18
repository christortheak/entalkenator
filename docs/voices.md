# Voice options

What enTalkenator offers for voicing programs, why, and what was measured. The catalogue the
skills present lives in `scripts/voices.py`; update its figures and `CATALOG_DATE` when this
page changes. Researched on 17 October 2026.

## The options

| Option | Where | Licence / terms | Quality evidence | Speed (M5 Pro) | Cost per 30 min |
|---|---|---|---|---|---|
| Kokoro-82M | Mac (mlx-audio) | Apache-2.0; espeak-ng fallback GPL-3.0 | Artificial Analysis Elo 1061, 7th of 17 open models; TTS Arena V2 ties Chatterbox | 16× real time | free |
| Qwen3-TTS 1.7B (VoiceDesign + Base, 8-bit) | Mac (mlx-audio), 16 GB+ | Apache-2.0, not gated | Not on the arenas; Seed-TTS English WER 1.24 (maker's figure) | 4× real time (batched per voice) | free |
| macOS `say` | Mac | Apple system voices | Clearly synthetic | instant | free |
| Gemini 3.8 Flash TTS | Google | Free tier may use prompts for training; EEA/UK/CH paid only | Elo 1275, #5 overall | cloud | $0.41 (≈$0.81 from Jan 2027) |
| Gemini 3.8 Flash-Lite TTS | Google | as above | Elo 1248, #8 | cloud | $0.28 (≈$0.55 from 2027) |
| OpenAI gpt-4o-mini-tts | OpenAI | Must disclose AI voices to listeners | Not ranked | cloud | ≈$0.45 |
| OpenAI tts-1 / tts-1-hd | OpenAI | as above | tts-1-hd Elo 1103 | cloud | ≈$0.47 / $0.95 |
| ElevenLabs v4 / v4 Turbo | ElevenLabs | Free plan non-commercial with attribution | Elo ≈1330, #1–2 | cloud | ≈$2.50 / $1.25 |

Cloud costs assume 30 minutes ≈ 5,250 words ≈ 31,500 characters at enTalkenator's 175 words a
minute. Gemini: 25 audio tokens a second at $9 (Flash) or $6 (Flash-Lite) per million, both
doubling on 1 January 2027. OpenAI tts-1: $15 per million characters (tts-1-hd $30);
gpt-4o-mini-tts: OpenAI's estimate of $0.015 a minute. ElevenLabs: about $0.08 per 1,000
characters for v4, half that for Turbo. Arena Elo comes from short single-voice clips with
±18–33 confidence intervals: it says nothing about conversations or long-form consistency, so
treat gaps under about 40 points as noise.

Every library episode says its voices are AI-generated (`cloud/src/feed.ts`), which covers
OpenAI's disclosure rule and is true for every option.

## Why these, and not others

- **Open models above Kokoro on the leaderboard are unusable here.** They either have
  non-commercial or research-only licences (Breeze TTS 2, Fish S2 Pro, Voxtral, Higgs Audio v3,
  OmniVoice, Echo TTS, Spark, IndexTTS2), are far too big (Fish Drama 3, a 35B mixture of
  experts), or run only on NVIDIA hardware (Step-Audio-EditX). No permissively licensed model
  small enough for an 8–16 GB Mac clearly beats Kokoro line by line.
- **What newer open models add is control.** That means instructions, voices designed from a
  description, and cloning. Qwen3-TTS 1.7B is the strongest permissive option, so it's the
  "expressive" choice. VoxCPM2 (Apache-2.0, 3.2 GB) is the runner-up: high fidelity, but its
  makers warn of speeding up, buzzing, or failing to stop on long text. Chatterbox (MIT,
  watermarked) and Pocket TTS (CC-BY) are roughly Kokoro's quality.
- **Whole-conversation (dialogue) models aren't ready.** MOSS-TTSD v1.0 (Apache-2.0, 5 speakers,
  8.9 GB at 8-bit) is the only one that clears licence, speaker count, and Mac support. It's 8B,
  and by its own measure about 4% of lines come out in the wrong voice. VibeVoice is
  research-only, and Microsoft withdrew part of it in 2025. Dia2 caps out at 2 speakers and 2
  minutes. Worth revisiting as an opt-in for 32 GB+ Macs.
- **Cloud.** Gemini Flash is the best quality per dollar, with the simplest key. ElevenLabs v4
  ranks first, at about five times the price. OpenAI is here because people already have
  keys, and gpt-4o-mini-tts takes per-speaker instructions. Hume's TTS API shuts down on 13
  November 2026; Cartesia, Inworld, Fish, and others were good but added nothing these don't.

## Listening test on this Mac

The same excerpt was rendered with each option: turns 13–20 of the St. Mary v. Roy oral
argument (three judges questioning the appellee's counsel, 714 words), with identical stitching.

| Version | Render time | Audio | Speaking rate | Peak memory |
|---|---|---|---|---|
| Kokoro (current voices) | 16 s | 4:09 | 159–188 wpm | about 2 GB |
| Qwen3-TTS, designed once then cloned | 64 s | 3:45 | 174–214 wpm (rushed) | 9.4 GB |
| Qwen3-TTS, designed every turn | 119 s | 4:40 | 147–159 wpm | 9.6 GB |
| Qwen3-TTS engine as shipped (pacing note, 90-word pieces) | 59 s incl. designing 4 voices | 4:02 | 171–189 wpm | ≈10 GB |

Cloning copies the pace of its reference clip, and an unprompted reference runs fast. So the
engine adds a pacing note to every voice description, and splits turns into pieces of about
90 words to avoid drift and lost endings. A voice designed fresh every turn is slower and more
deliberate, but can change between turns, so the engine designs once and clones.

## How each engine is wired

- **kokoro, qwen3:** run in a child process with the setup_tts.sh venv, so an MLX crash can't
  take finished clips with it. For Qwen3, VoiceDesign renders a reference line once per
  description (kept in `~/.cache/entalkenator/qwen3-voices/`, keyed by the description), then
  the Base model clones it for every turn. Turns are batched per voice, since mlx-audio
  clones one shared reference per batch.
- **gemini:** `POST /v1beta/interactions`, one request per turn, with the delivery note in
  `speech_metadata.style`. Replies are WAV.
- **openai:** `POST /v1/audio/speech` with `response_format: "pcm"` (24 kHz 16-bit mono),
  `instructions` for gpt-4o-mini-tts, and `speed` for tts-1. Text is split under 3,500
  characters.
- **elevenlabs:** `POST /v1/text-to-speech/{voice_id}?output_format=pcm_24000`. Voices come
  from the account's `GET /v1/voices` and are matched by name. Text is split under 9,000
  characters.
- **Keys:** the environment first, then the login Keychain: service `enTalkenator`, account
  `gemini` / `openai` / `elevenlabs`. `voices.py key set` takes the key from a macOS dialog
  and checks it with a free read-only request. It then hands the key to `security -i` on stdin,
  so it never appears in a process listing, and reads it back, because `security -i` exits 0
  even when a command fails. Items made by `security` trust it, so later reads don't
  prompt.

The cloud engines are tested against a stand-in server (`tests/test_voices.py`), not the real
services, because no keys were available. The first real run with each provider is the real
test.

## Sources

- Leaderboards: https://artificialanalysis.ai/text-to-speech/leaderboard ·
  https://tts-agi-tts-arena-v2.hf.space/leaderboard
- Gemini: https://ai.google.dev/gemini-api/docs/speech-generation ·
  https://ai.google.dev/gemini-api/docs/pricing · https://ai.google.dev/gemini-api/terms
- OpenAI: https://developers.openai.com/api/docs/guides/text-to-speech
- ElevenLabs: https://elevenlabs.io/docs/models ·
  https://elevenlabs.io/docs/api-reference/text-to-speech/convert ·
  https://elevenlabs.io/pricing/api
- Qwen3-TTS: https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice ·
  https://github.com/QwenLM/Qwen3-TTS/issues
- VoxCPM2: https://huggingface.co/openbmb/VoxCPM2 · https://voxcpm.readthedocs.io/en/latest/usage_guide.html
- MOSS-TTSD: https://github.com/OpenMOSS/MOSS-TTSD · https://arxiv.org/abs/2603.19739
- VibeVoice: https://huggingface.co/microsoft/VibeVoice-1.5B
- Hume TTS shutdown: https://dev.hume.ai/docs/text-to-speech-tts/overview
- Apple Silicon speeds: https://soniqo.audio/benchmarks
