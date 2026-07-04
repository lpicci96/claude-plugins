#!/usr/bin/env bash
# claude-talk — speak text aloud via local Kokoro TTS.
# Fast path: persistent daemon (~1-2s). Fallbacks: one-shot synth, then macOS
# `say`. Never blocks the daemon and never hard-fails. Text from args or stdin.
DIR="$(cd "$(dirname "$0")" && pwd)"
. "$DIR/common.sh"

TEXT="$*"
[ -z "$TEXT" ] && TEXT="$(cat)"
[ -z "$TEXT" ] && exit 0

if [ -x "$VENV_PY" ]; then
  # Fast path: persistent daemon (keeps the model loaded).
  if printf '%s' "$TEXT" | "$VENV_PY" "$DIR/client.py" 2>>"$CLAUDE_TALK_HOME/tts.err"; then
    exit 0
  fi
  # Fallback: one-shot synthesis (reloads the model each call).
  if printf '%s' "$TEXT" | "$VENV_PY" "$DIR/speak.py" 2>>"$CLAUDE_TALK_HOME/tts.err"; then
    exit 0
  fi
fi

# Last resort: Apple's built-in TTS (robotic, but always available).
command -v say >/dev/null 2>&1 && say "$TEXT"
exit 0
