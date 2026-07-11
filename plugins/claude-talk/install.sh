#!/usr/bin/env bash
# claude-talk installer (macOS). Sets up the local Kokoro voice: espeak-ng, a
# Python venv, the model (~340 MB), a copy of the scripts, and your voice config.
# Re-run the voice picker any time with:  install.sh --configure
set -euo pipefail

DATA="${CLAUDE_TALK_HOME:-$HOME/.claude/claude-talk}"
SRC="$(cd "$(dirname "$0")" && pwd)"
REL="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"

step() { printf "\n\033[1m==> %s\033[0m\n" "$1"; }

# --configure: just (re)run the interactive voice/speed/name picker.
if [ "${1:-}" = "--configure" ]; then
  exec "$DATA/venv/bin/python" "$DATA/bin/setup.py"
fi

[ "$(uname)" = "Darwin" ] || { echo "claude-talk currently supports macOS only."; exit 1; }

step "Data directory: $DATA"
mkdir -p "$DATA/bin" "$DATA/models"

step "Installing scripts"
cp -f "$SRC/scripts/"*.py "$SRC/scripts/"*.sh "$DATA/bin/"
chmod +x "$DATA/bin/"*.sh

step "Checking espeak-ng (phonemizer backend)"
if command -v brew >/dev/null 2>&1; then
  brew list espeak-ng >/dev/null 2>&1 || brew install espeak-ng
else
  echo "Homebrew not found — install it from https://brew.sh, then: brew install espeak-ng"
fi

step "Creating the Python environment"
# Reuse an existing venv so re-running the installer is safe (uv errors on an
# existing venv without --clear). The pip install step still refreshes deps.
if command -v uv >/dev/null 2>&1; then
  [ -d "$DATA/venv" ] || uv venv "$DATA/venv" --python 3.12
  uv pip install --python "$DATA/venv/bin/python" kokoro-onnx soundfile
else
  [ -d "$DATA/venv" ] || python3 -m venv "$DATA/venv"
  "$DATA/venv/bin/python" -m pip install --quiet --upgrade pip
  "$DATA/venv/bin/python" -m pip install --quiet kokoro-onnx soundfile
fi

step "Downloading the Kokoro model (~340 MB, one time)"
[ -f "$DATA/models/kokoro-v1.0.onnx" ] || curl -L -# -o "$DATA/models/kokoro-v1.0.onnx" "$REL/kokoro-v1.0.onnx"
[ -f "$DATA/models/voices-v1.0.bin" ] || curl -L -# -o "$DATA/models/voices-v1.0.bin" "$REL/voices-v1.0.bin"

if [ -t 0 ] && [ "${1:-}" != "--skip-config" ]; then
  step "Choose your voice and speed"
  "$DATA/venv/bin/python" "$DATA/bin/setup.py" || true
else
  [ -f "$DATA/config.env" ] || printf 'KOKORO_VOICE=af_heart\nKOKORO_SPEED=1.0\nCLAUDE_TALK_NAME=""\nCLAUDE_TALK_VOLUME=100\nCLAUDE_TALK_DUCK=on\nCLAUDE_TALK_SESSION_VOICE=distinct\n' > "$DATA/config.env"
fi

step "Done"
cat <<EOF

claude-talk is installed.

  • Start talking:   /talk
  • Change voice:    /talk-setup    (or: bash "$SRC/install.sh" --configure)
  • Stop talking:    /quiet

The first time you run /talk, Claude Code will ask permission to run the
claude-talk command — choose "Yes, and don't ask again" to allow it.

Optional: bind global stop / pause / repeat / step-back hotkeys (requires skhd).
This is opt-in and not set up for you — see the "Hotkeys" section of the README:
https://github.com/lpicci96/claude-plugins/tree/main/plugins/claude-talk#hotkeys-optional-opt-in-stop-pause-repeat-step-backforward
EOF
