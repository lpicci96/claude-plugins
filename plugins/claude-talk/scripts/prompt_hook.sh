#!/usr/bin/env bash
# claude-talk — UserPromptSubmit hook. Two jobs, both no-ops if not set up:
#   1. Barge-in: stop whatever is playing the moment a new prompt is sent.
#   2. Reinforcement: if this session is in /talk mode, print a short reminder so
#      Claude keeps speaking (a UserPromptSubmit hook's stdout is added to the
#      model's context for the turn — this is what keeps it from "forgetting").
# Always exits 0 so it never blocks prompt submission.
DIR="$(cd "$(dirname "$0")" && pwd)"
. "$DIR/common.sh"

# The hook payload (JSON) arrives on stdin; pull the session id out of it.
payload="$(cat 2>/dev/null)"
sid="$(printf '%s' "$payload" | sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"

# 1. Barge-in stop, scoped to THIS session — sending a prompt here stops only
# this session's audio, never another session that happens to be speaking.
[ -x "$VENV_PY" ] && [ -f "$DIR/client.py" ] && "$VENV_PY" "$DIR/client.py" --stop "$sid" 2>/dev/null

# 2. Talk-mode reminder (only while this session has an active mark).
if [ -n "$sid" ] && [ -f "$CLAUDE_TALK_HOME/talk-mode-$sid" ]; then
  cat <<'EOF'
[claude-talk] You are in /talk voice conversation mode. Reply out loud, keep on-screen text minimal, and narrate longer work as you go:
- Interim progress (non-blocking): KOKORO_NOWAIT=1 ~/.claude/claude-talk/bin/talk.sh "short line"
- Final spoken wrap-up (blocks until it finishes speaking, so your turn ends when the audio does): KOKORO_WAIT_DONE=1 ~/.claude/claude-talk/bin/talk.sh "wrap-up"
The user runs /quiet to leave voice mode.
EOF
fi
exit 0
