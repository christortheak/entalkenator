#!/bin/bash
# Installs the local Kokoro voice engine (mlx-audio + misaki) into a private venv
# and pre-downloads the Kokoro-82M weights. Run once; render_audio.py finds the
# venv on its own afterwards.
#
#   setup_tts.sh            install (about 660 MB of downloads, about 1.3 GB on disk)
#   setup_tts.sh --check    exit 0 if the engine is already installed
#
# Location: $ENTALKENATOR_HOME (default ~/.cache/entalkenator). Model weights go to
# the normal Hugging Face cache (~/.cache/huggingface/hub).
set -euo pipefail

HOME_DIR="${ENTALKENATOR_HOME:-$HOME/.cache/entalkenator}"
VENV="$HOME_DIR/venv"
PY="$VENV/bin/python"
MODEL="${ENTALKENATOR_KOKORO_MODEL:-mlx-community/Kokoro-82M-bf16}"
HERE="$(cd "$(dirname "$0")" && pwd)"

if [ "${1:-}" = "--check" ]; then
    [ -x "$PY" ] && "$PY" -c "import mlx_audio, misaki.en" 2>/dev/null && exit 0
    exit 1
fi

if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
    echo "Kokoro via mlx-audio needs an Apple Silicon Mac. Use --engine say or --engine gemini instead." >&2
    exit 2
fi

mkdir -p "$HOME_DIR"
if command -v uv >/dev/null 2>&1; then
    # misaki needs Python < 3.13 and mlx-audio needs >= 3.10.
    uv venv --allow-existing --python 3.12 "$VENV"
    uv pip install --python "$PY" -r "$HERE/requirements-kokoro.txt"
else
    BASE_PY=""
    for p in python3.12 python3.11 python3.10; do
        command -v "$p" >/dev/null 2>&1 && { BASE_PY="$p"; break; }
    done
    if [ -z "$BASE_PY" ]; then
        echo "Need uv (https://docs.astral.sh/uv/) or Python 3.10-3.12 to install the Kokoro engine." >&2
        exit 3
    fi
    "$BASE_PY" -m venv "$VENV"
    "$PY" -m pip install --upgrade pip
    "$PY" -m pip install -r "$HERE/requirements-kokoro.txt"
fi

echo "Downloading $MODEL weights and voices..."
"$PY" - "$MODEL" <<'EOF'
import sys
from mlx_audio.tts.utils import load_model
load_model(sys.argv[1])
print("Kokoro ready.")
EOF
