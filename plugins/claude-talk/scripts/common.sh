# Shared paths and config for the claude-talk shell scripts. Source, don't run.
CLAUDE_TALK_HOME="${CLAUDE_TALK_HOME:-$HOME/.claude/claude-talk}"
VENV_PY="$CLAUDE_TALK_HOME/venv/bin/python"

# User settings written by the installer / setup (voice, speed, name).
[ -f "$CLAUDE_TALK_HOME/config.env" ] && . "$CLAUDE_TALK_HOME/config.env"

export CLAUDE_TALK_HOME
export KOKORO_VOICE="${KOKORO_VOICE:-af_heart}"
export KOKORO_SPEED="${KOKORO_SPEED:-1.0}"

# Claude's own voice loudness (0–100), independent of the system volume, and
# ducking of other audio while Claude speaks. See the README "Configuration".
export CLAUDE_TALK_VOLUME="${CLAUDE_TALK_VOLUME:-100}"
export CLAUDE_TALK_DUCK="${CLAUDE_TALK_DUCK:-on}"
export CLAUDE_TALK_DUCK_RATIO="${CLAUDE_TALK_DUCK_RATIO:-0.5}"
export CLAUDE_TALK_DUCK_HOLD="${CLAUDE_TALK_DUCK_HOLD:-1.2}"
