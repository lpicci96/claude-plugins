#!/usr/bin/env bash
# claude-talk — replay the NEXT spoken line (step forward through history toward
# the most recent). Interrupts whatever is playing, then replays the newer line
# from cache (zero LLM tokens). Pairs with back.sh. No-op if already at the
# newest line. Wired to a hotkey (see ~/.config/skhd/skhdrc).
DIR="$(cd "$(dirname "$0")" && pwd)"
. "$DIR/common.sh"
if [ -x "$VENV_PY" ] && [ -f "$DIR/client.py" ]; then
  "$VENV_PY" "$DIR/client.py" --stop 2>/dev/null
  "$VENV_PY" "$DIR/client.py" --forward 2>>"$CLAUDE_TALK_HOME/tts.err"
fi
exit 0
