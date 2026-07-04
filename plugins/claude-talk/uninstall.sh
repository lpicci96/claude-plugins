#!/usr/bin/env bash
# claude-talk uninstaller — removes the local data (venv, model, scripts, config).
DATA="${CLAUDE_TALK_HOME:-$HOME/.claude/claude-talk}"

# Stop a running daemon, if any.
[ -f "$DATA/venv/bin/python" ] && "$DATA/venv/bin/python" "$DATA/bin/client.py" --stop 2>/dev/null || true
pkill -f "$DATA/bin/daemon.py" 2>/dev/null || true

rm -rf "$DATA"
echo "Removed $DATA."
echo "To remove the plugin itself:  /plugin uninstall claude-talk@lpicci96"
