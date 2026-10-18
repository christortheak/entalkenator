# Developing enTalkenator

Notes for changing enTalkenator itself, not for using it. `CLAUDE.md` is written for the
Claude that guides a *user*, so when you open this folder to work on it, tell Claude you're
developing and point it here.

## Layout and conventions

- `CLAUDE.md`, `README.md`, and the three skills in `.claude/skills/` are the product's
  voice. Write them for someone who isn't technical: plain words, one step at a time, and
  never a secret in the chat.
- Scripts are standard-library Python 3.9+, so they run on the macOS system Python, and
  every command prints JSON. When the person has to do something, a script exits with
  `"ok": false` and a `needs` list (`what`, `tell`, optional `url`) instead of prompting.
- Skills call scripts by relative path (`python3 scripts/engine.py ...`) so the allow rules
  in `.claude/settings.json` match them. Keep those rules in step when adding a script or a
  destructive command (destructive ones go under `ask`).
- Anything per-person lives outside the folder, because an update replaces the folder:
  - `~/.cache/entalkenator/` holds the Kokoro venv, Wrangler, and the generated deploy config
  - `~/.config/entalkenator/` holds `library.json` (the library address and upload token,
    mode 600) and `setup.json` (whether the welcome was given)
  - The one exception is `programs/`, which the README tells people to carry across.
- `library.py` only ever runs Wrangler commands. Don't read Wrangler's OAuth token to call
  Cloudflare's API directly: Claude Code's auto-mode safety check blocks that as credential
  handling, which would break setup for users.

## Templates

`templates/` is a snapshot of the app's `enTalkenator/Templates/*.json`. When the app's
templates change, copy them over. `engine.py` reproduces the app's template semantics
exactly; `docs/how-it-works.md` has the details.

## Tests

```bash
for t in tests/test_*.py; do python3 "$t" || echo "FAILED: $t"; done
```

None needs an account or touches real settings:
- `test_library.py` runs `library.py`'s setup paths against `tests/fake_wrangler.py`.
- `test_voices.py` checks the voice options and the cloud engines against a stand-in server,
  with `tests/fake_security.py` in place of the Keychain.
- `test_sources.py` checks reading, stitching, the faithfulness audit, and the text-layer path
  conversations use.
- `test_chapters.py` renders with `say` and checks the chapters AVFoundation reads (macOS).
- `test_status.py` checks that off a Mac (Cowork, say) the status tells Claude to stop and
  send the person to Claude Code.
The library Worker's own suites (`cloud/test/`) need a local `wrangler dev`; see
`cloud/README.md`.

For a real-account check, deploy a library under its own name and settings file, so it
can't touch a real one:

```bash
python3 scripts/library.py deploy --name entalkenator-dev --config /tmp/entalkenator-dev.json
```

Remove it afterwards with `remove --yes --config /tmp/entalkenator-dev.json`.

## Versions and releases

- `VERSION` is the release number. `library.py deploy` records it. When it changes, the
  session-start status tells Claude to offer the person an update of their library.
- Wrangler is pinned in `scripts/library.py` (`WRANGLER_VERSION`) and in
  `cloud/package.json`. Bump both together, and re-run the cloud tests.
- `tools/make_release.sh` builds `dist/enTalkenator-<version>.zip` from the last commit,
  leaving out `tests/`, `cloud/test/`, and `tools/` (see `.gitattributes`). Commit first.
  The zip unzips to an `enTalkenator/` folder, ready to open in Claude.
