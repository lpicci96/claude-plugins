#!/usr/bin/env bash
# claude-talk — replay the last spoken line. Pure shell, zero LLM tokens.
# Fast path: the daemon replays its cached wav (no re-synthesis, near-instant).
# Fallback: re-synthesize from the text talk.sh saved to last.txt. Wired to a
# hotkey (see ~/.config/skhd/skhdrc). No-op if nothing has been said yet.
DIR="$(cd "$(dirname "$0")" && pwd)"
. "$DIR/common.sh"

f="$CLAUDE_TALK_HOME/last.txt"

if [ -x "$VENV_PY" ]; then
  # Cut off anything currently playing so a repeat always starts clean.
  "$VENV_PY" "$DIR/client.py" --stop 2>/dev/null
  # Fast path: replay the cached audio with no synthesis.
  if "$VENV_PY" "$DIR/client.py" --replay 2>>"$CLAUDE_TALK_HOME/tts.err"; then
    exit 0
  fi
  # Fallback: re-synthesize from saved text (e.g. cache not warm yet).
  [ -s "$f" ] || exit 0
  if "$VENV_PY" "$DIR/client.py" < "$f" 2>>"$CLAUDE_TALK_HOME/tts.err"; then
    exit 0
  fi
  if "$VENV_PY" "$DIR/speak.py" < "$f" 2>>"$CLAUDE_TALK_HOME/tts.err"; then
    exit 0
  fi
fi

# Last resort: Apple's built-in TTS.
[ -s "$f" ] && command -v say >/dev/null 2>&1 && say "$(cat "$f")"
exit 0
