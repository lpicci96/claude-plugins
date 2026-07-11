"""Shared helpers for the claude-talk Python scripts (daemon, speak, setup)."""

import json
import os
import re
import subprocess

MAX_CHARS = 400

# Fallback boost ceiling used when the audio array isn't available (e.g. replay).
# Kokoro output peaks near 0.5 (~-6 dBFS), so ~1.9x is the most we can amplify
# before clipping; clip_ceiling() computes the exact value per line when it can.
DUCK_GAIN_CAP = 1.9

# Media apps we know how to duck (their own AppleScript `sound volume`).
DEFAULT_DUCK_APPS = ("Spotify", "Music")

# An app volume read can round-trip a point off what we set (Spotify quantizes,
# e.g. set 52 -> reads 51), so treat anything this close to a value we set as
# "unchanged" — both so we never clobber a real user change and so repeated
# duck/restore cycles don't slowly drift the volume down.
APP_SLOP = 2


def data_dir():
    """Runtime data directory (venv, model, socket, config). Fixed, not derived
    from the script location, so plugin and installed copies agree."""
    return os.environ.get("CLAUDE_TALK_HOME") or os.path.expanduser(
        "~/.claude/claude-talk"
    )


def model_paths():
    d = os.path.join(data_dir(), "models")
    return os.path.join(d, "kokoro-v1.0.onnx"), os.path.join(d, "voices-v1.0.bin")


def find_espeak():
    """Point phonemizer at the espeak-ng shared library. Honors an existing
    PHONEMIZER_ESPEAK_LIBRARY; otherwise probes Homebrew (Apple Silicon + Intel)."""
    if os.environ.get("PHONEMIZER_ESPEAK_LIBRARY"):
        return
    candidates = []
    try:
        p = subprocess.run(
            ["brew", "--prefix", "espeak-ng"], capture_output=True, text=True
        )
        if p.returncode == 0 and p.stdout.strip():
            candidates.append(
                os.path.join(p.stdout.strip(), "lib", "libespeak-ng.dylib")
            )
    except Exception:
        pass
    candidates += [
        "/opt/homebrew/lib/libespeak-ng.dylib",
        "/usr/local/lib/libespeak-ng.dylib",
    ]
    for c in candidates:
        if os.path.exists(c):
            os.environ["PHONEMIZER_ESPEAK_LIBRARY"] = c
            return


# --- Volume + per-app ducking ----------------------------------------------
#
# Two fully independent controls, and NEITHER touches the global output volume —
# so your volume dial always means exactly what you set, and it sticks:
#
#   * Claude's own voice loudness — afplay's per-instance `-v` gain, from
#     CLAUDE_TALK_VOLUME. 100 = play at the current output volume; higher
#     amplifies into Kokoro's headroom (clamped per line so it never clips).
#   * Ducking — while Claude speaks, lower the *own volume of the media apps
#     that are currently playing* (Spotify, Apple Music) via AppleScript, then
#     restore it. Only those apps dip; the system volume and Claude's voice are
#     left alone. Apps with no scriptable volume (browsers, etc.) aren't ducked.
#
# The stateful "duck once per burst, restore once, never clobber the user, never
# drift" logic lives in the daemon's Ducker; app_duck_start/app_duck_stop are the
# simple one-shot equivalents for the non-daemon fallback path.


def gain_from_volume(vol):
    """Map CLAUDE_TALK_VOLUME to an afplay `-v` gain. Linear: 100 = unity (1.0).
    Kokoro peaks near -6 dBFS, so values above 100 amplify into that headroom
    (up to ~190 before clipping); playback clamps to the per-line clip ceiling so
    a boosted value never distorts."""
    try:
        v = int(float(vol))
    except (TypeError, ValueError):
        v = 100
    return min(DUCK_GAIN_CAP, max(0.0, v / 100.0))


def clip_ceiling(audio):
    """Largest afplay gain that won't clip this line, from its actual peak.
    Falls back to DUCK_GAIN_CAP when the samples aren't available."""
    try:
        import numpy as np

        peak = float(np.max(np.abs(audio))) if audio is not None else 0.0
    except Exception:
        peak = 0.0
    if peak <= 0.0:
        return DUCK_GAIN_CAP
    return min(4.0, 0.97 / peak)


def _osa(*lines):
    """Run an AppleScript (pass each line as a separate arg); return stdout text
    stripped, or None on any error / non-zero exit."""
    try:
        cmd = ["osascript"]
        for ln in lines:
            cmd += ["-e", ln]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:
        return None


def app_duck_state(app):
    """Return (player_state, sound_volume) for a media app, read ONLY if the app
    is already running — the System Events guard means we never launch it. None
    if it isn't running or can't be read. player_state is e.g. "playing" /
    "paused"; sound_volume is 0-100."""
    r = _osa(
        'tell application "System Events"',
        f'  if exists process "{app}" then',
        f'    tell application "{app}" to (player state as text)'
        ' & " " & (sound volume as text)',
        "  end if",
        "end tell",
    )
    if not r:
        return None
    parts = r.split()
    if len(parts) < 2:
        return None
    try:
        return parts[0], int(parts[1])
    except ValueError:
        return None


def set_app_volume(app, level):
    _osa(f'tell application "{app}" to set sound volume to {int(level)}')


def _marker():
    return os.path.join(data_dir(), "duck_state.json")


def save_app_duck_marker(state):
    """Persist {app: {"orig": o, "duck": d}} so a hard kill mid-duck (which skips
    the normal restore) can be recovered on the next start — otherwise the app is
    left sitting quiet."""
    try:
        with open(_marker(), "w") as f:
            json.dump(state, f)
    except OSError:
        pass


def clear_app_duck_marker():
    try:
        os.unlink(_marker())
    except OSError:
        pass


def recover_duck():
    """If a previous run was killed while an app was ducked, restore that app's
    volume — but only if it's still sitting where we ducked it (else the user or
    the app itself took over; leave it)."""
    try:
        with open(_marker()) as f:
            state = json.load(f)
    except (OSError, ValueError):
        return
    if isinstance(state, dict):
        for app, v in state.items():
            try:
                orig, duck = int(v["orig"]), int(v["duck"])
            except (TypeError, ValueError, KeyError):
                continue
            st = app_duck_state(app)
            if st is None or abs(st[1] - duck) <= APP_SLOP:
                set_app_volume(app, orig)
    clear_app_duck_marker()


def app_duck_start(apps, ratio):
    """One-shot duck for the non-daemon path: lower each currently-playing app's
    own volume to `ratio` of its level, once. Returns {app: {orig, duck}} to hand
    back to app_duck_stop (empty if nothing was playing to duck)."""
    state = {}
    for app in apps:
        st = app_duck_state(app)
        if st is None or st[0] != "playing" or st[1] <= 0:
            continue
        orig = st[1]
        duck = round(orig * ratio)
        if duck >= orig:
            continue
        set_app_volume(app, duck)
        state[app] = {"orig": orig, "duck": duck}
    if state:
        save_app_duck_marker(state)
    return state


def app_duck_stop(state):
    """Undo app_duck_start — restore each app only if it's still where we set it,
    so we never clobber a change the user made while we were speaking."""
    if not state:
        return
    for app, v in state.items():
        st = app_duck_state(app)
        if st is None or abs(st[1] - v["duck"]) <= APP_SLOP:
            set_app_volume(app, v["orig"])
    clear_app_duck_marker()


def duck_apps(env=None):
    """Which apps to duck: CLAUDE_TALK_DUCK_APPS (comma-separated), or the
    defaults. Names are AppleScript application names, e.g. "Spotify", "Music"."""
    raw = (env or os.environ).get("CLAUDE_TALK_DUCK_APPS", "")
    apps = [a.strip() for a in raw.split(",") if a.strip()]
    return apps or list(DEFAULT_DUCK_APPS)


def env_settings():
    """Volume + ducking settings read from the environment, for the one-shot
    speak.py path (the daemon gets equivalent values per request)."""

    def _float(name, default):
        try:
            return float(os.environ.get(name, default))
        except ValueError:
            return float(default)

    duck = os.environ.get("CLAUDE_TALK_DUCK", "on").strip().lower() not in (
        "0",
        "off",
        "false",
        "no",
        "",
    )
    return {
        "volume": os.environ.get("CLAUDE_TALK_VOLUME", "100"),
        "duck": duck,
        "ratio": min(0.95, max(0.0, _float("CLAUDE_TALK_DUCK_RATIO", "0.25"))),
        "hold": max(0.0, _float("CLAUDE_TALK_DUCK_HOLD", "1.2")),
        "apps": duck_apps(),
    }


def chunk(text):
    """Split text into <=MAX_CHARS pieces on sentence boundaries."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks, cur = [], ""
    for s in sentences:
        if len(cur) + len(s) + 1 <= MAX_CHARS:
            cur = (cur + " " + s).strip()
        else:
            if cur:
                chunks.append(cur)
            cur = s
    if cur:
        chunks.append(cur)
    return chunks or [text]


def stream_pieces(text):
    """Split text into small, playable pieces — roughly one sentence each — so
    streaming playback can start the first piece while the rest still synthesizes.
    A sentence longer than MAX_CHARS is broken on commas, then hard-wrapped by
    length, so time-to-first-audio stays small even for a long opening sentence."""
    pieces = []
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) <= MAX_CHARS:
            pieces.append(sentence)
            continue
        cur = ""
        for part in re.split(r"(?<=,)\s+", sentence):
            if len(cur) + len(part) + 1 <= MAX_CHARS:
                cur = (cur + " " + part).strip()
            else:
                if cur:
                    pieces.append(cur)
                cur = part
        if cur:
            pieces.append(cur)
    return pieces or [text.strip()]
