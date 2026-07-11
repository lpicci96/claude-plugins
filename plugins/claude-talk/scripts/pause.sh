#!/usr/bin/env bash
# claude-talk — pause / resume the line that's currently speaking (toggle).
# Pure shell, zero LLM tokens. Wired to a hotkey (see ~/.config/skhd/skhdrc).
# No-op if nothing is playing or claude-talk isn't set up.
DIR="$(cd "$(dirname "$0")" && pwd)"
. "$DIR/common.sh"
[ -x "$VENV_PY" ] && [ -f "$DIR/client.py" ] && "$VENV_PY" "$DIR/client.py" --pause 2>/dev/null
exit 0
