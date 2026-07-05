# Shared paths and config for the claude-talk shell scripts. Source, don't run.
CLAUDE_TALK_HOME="${CLAUDE_TALK_HOME:-$HOME/.claude/claude-talk}"
VENV_PY="$CLAUDE_TALK_HOME/venv/bin/python"

# User settings written by the installer / setup. Values already present in the
# environment win over config.env so per-call overrides work — /talk-setup
# previews voices with `KOKORO_VOICE=<voice> talk.sh "..."`.
_voice="${KOKORO_VOICE-}" _speed="${KOKORO_SPEED-}" _vol="${CLAUDE_TALK_VOLUME-}"
_duck="${CLAUDE_TALK_DUCK-}" _ratio="${CLAUDE_TALK_DUCK_RATIO-}" _hold="${CLAUDE_TALK_DUCK_HOLD-}"
_apps="${CLAUDE_TALK_DUCK_APPS-}"
[ -f "$CLAUDE_TALK_HOME/config.env" ] && . "$CLAUDE_TALK_HOME/config.env"
[ -n "$_voice" ] && KOKORO_VOICE="$_voice"
[ -n "$_speed" ] && KOKORO_SPEED="$_speed"
[ -n "$_vol" ] && CLAUDE_TALK_VOLUME="$_vol"
[ -n "$_duck" ] && CLAUDE_TALK_DUCK="$_duck"
[ -n "$_ratio" ] && CLAUDE_TALK_DUCK_RATIO="$_ratio"
[ -n "$_hold" ] && CLAUDE_TALK_DUCK_HOLD="$_hold"
[ -n "$_apps" ] && CLAUDE_TALK_DUCK_APPS="$_apps"
unset _voice _speed _vol _duck _ratio _hold _apps

export CLAUDE_TALK_HOME
export KOKORO_VOICE="${KOKORO_VOICE:-af_heart}"
export KOKORO_SPEED="${KOKORO_SPEED:-1.0}"

# Claude's own voice loudness (100 = normal, up to ~190), independent of the
# system volume, and ducking of other apps' audio while Claude speaks. Ducking
# lowers each playing app's OWN volume (never the system dial): DUCK_RATIO is the
# fraction it drops to (0.25 = 25%), DUCK_APPS is the comma-separated app list.
# See the README "Configuration".
export CLAUDE_TALK_VOLUME="${CLAUDE_TALK_VOLUME:-100}"
export CLAUDE_TALK_DUCK="${CLAUDE_TALK_DUCK:-on}"
export CLAUDE_TALK_DUCK_RATIO="${CLAUDE_TALK_DUCK_RATIO:-0.25}"
export CLAUDE_TALK_DUCK_HOLD="${CLAUDE_TALK_DUCK_HOLD:-1.2}"
export CLAUDE_TALK_DUCK_APPS="${CLAUDE_TALK_DUCK_APPS:-Spotify,Music}"
