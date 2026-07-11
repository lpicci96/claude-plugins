#!/usr/bin/env bash
# claude-talk — replay the PREVIOUS spoken line (step back through history).
# Interrupts whatever is playing, then replays the older line from cache (zero
# LLM tokens). Repeated presses keep walking back; forward.sh walks toward newer.
# No-op if there's no older line. Wired to a hotkey (see ~/.config/skhd/skhdrc).
DIR="$(cd "$(dirname "$0")" && pwd)"
. "$DIR/common.sh"
if [ -x "$VENV_PY" ] && [ -f "$DIR/client.py" ]; then
  "$VENV_PY" "$DIR/client.py" --stop 2>/dev/null
  "$VENV_PY" "$DIR/client.py" --back 2>>"$CLAUDE_TALK_HOME/tts.err"
fi
exit 0
