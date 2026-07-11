#!/usr/bin/env bash
# claude-talk — speak text aloud via local Kokoro TTS.
# Fast path: persistent daemon (~1-2s). Fallbacks: one-shot synth, then macOS
# `say`. Never blocks the daemon and never hard-fails. Text from args or stdin.
DIR="$(cd "$(dirname "$0")" && pwd)"
. "$DIR/common.sh"

SID="${CLAUDE_CODE_SESSION_ID:-nosession}"

# Mode control (not speech): `--on` (run by /talk) marks this session as being in
# conversation mode — so the UserPromptSubmit hook can remind Claude to keep
# speaking — and warms the daemon so the first spoken line isn't slow. `--off`
# (run by /quiet) clears the mark. Old marks are tidied after a day.
case "${1-}" in
  --on)
    touch "$CLAUDE_TALK_HOME/talk-mode-$SID" 2>/dev/null
    find "$CLAUDE_TALK_HOME" -maxdepth 1 -name 'talk-mode-*' -mtime +1 -delete 2>/dev/null
    [ -x "$VENV_PY" ] && [ -f "$DIR/client.py" ] && "$VENV_PY" "$DIR/client.py" --warm 2>/dev/null
    exit 0 ;;
  --off)
    rm -f "$CLAUDE_TALK_HOME/talk-mode-$SID" 2>/dev/null
    exit 0 ;;
esac

TEXT="$*"
[ -z "$TEXT" ] && TEXT="$(cat)"

# Robustness: the interim / wait-done flags belong *before* the command as an env
# var (`KOKORO_NOWAIT=1 talk.sh "…"`). If one lands as a trailing argument
# instead, honor it as the flag rather than reading "KOKORO_NOWAIT=1" aloud.
tail_tok="${TEXT##* }"
case "$tail_tok" in
  KOKORO_NOWAIT=*)
    export KOKORO_NOWAIT="${tail_tok#KOKORO_NOWAIT=}"
    case "$TEXT" in
      *" "*) TEXT="${TEXT% *}" ;;  # drop the trailing token
      *)     TEXT="" ;;            # it was the only token
    esac
    ;;
  KOKORO_WAIT_DONE=*)
    export KOKORO_WAIT_DONE="${tail_tok#KOKORO_WAIT_DONE=}"
    case "$TEXT" in
      *" "*) TEXT="${TEXT% *}" ;;
      *)     TEXT="" ;;
    esac
    ;;
esac

[ -z "$TEXT" ] && exit 0

# Remember the last "proper" line so a repeat hotkey can replay it with zero LLM
# tokens (see repeat.sh). Skip fire-and-forget interim lines (KOKORO_NOWAIT) so
# repeat replays the real reply, not a throwaway "validating…" chirp.
[ -z "$KOKORO_NOWAIT" ] && printf '%s' "$TEXT" > "$CLAUDE_TALK_HOME/last.txt" 2>/dev/null

# Drop a per-session "just spoke" marker so a user who has a completion chime
# can silence it while claude-talk is speaking (opt-in; see the README
# "Completion chime" section). Harmless for everyone else. Also tidy markers
# older than a day.
touch "$CLAUDE_TALK_HOME/spoke-$SID" 2>/dev/null
find "$CLAUDE_TALK_HOME" -maxdepth 1 -name 'spoke-*' -mtime +1 -delete 2>/dev/null

# Keep the talk-mode mark fresh while we're actively speaking, so the day-old
# cleanup never clears an in-use session (but don't create it — that's --on's job).
[ -f "$CLAUDE_TALK_HOME/talk-mode-$SID" ] && touch "$CLAUDE_TALK_HOME/talk-mode-$SID" 2>/dev/null

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
