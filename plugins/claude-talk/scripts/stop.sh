#!/usr/bin/env bash
# claude-talk — stop speaking immediately (barge-in). No-op if not set up.
# Called by the UserPromptSubmit hook so audio stops when you send your next
# message. Exits 0 always so it never blocks prompt submission.
DIR="$(cd "$(dirname "$0")" && pwd)"
. "$DIR/common.sh"
[ -x "$VENV_PY" ] && [ -f "$DIR/client.py" ] && "$VENV_PY" "$DIR/client.py" --stop 2>/dev/null
exit 0
