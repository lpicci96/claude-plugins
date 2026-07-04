# Shared paths and config for the claude-talk shell scripts. Source, don't run.
CLAUDE_TALK_HOME="${CLAUDE_TALK_HOME:-$HOME/.claude/claude-talk}"
VENV_PY="$CLAUDE_TALK_HOME/venv/bin/python"

# User settings written by the installer / setup (voice, speed, name).
[ -f "$CLAUDE_TALK_HOME/config.env" ] && . "$CLAUDE_TALK_HOME/config.env"

export CLAUDE_TALK_HOME
export KOKORO_VOICE="${KOKORO_VOICE:-af_heart}"
export KOKORO_SPEED="${KOKORO_SPEED:-1.0}"
