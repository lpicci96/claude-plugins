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


# --- Volume + ducking ------------------------------------------------------
#
# Two independent controls:
#   * Claude's own voice loudness — applied as afplay's per-instance `-v` gain,
#     which never touches the global output volume or any other app.
#   * Ducking — while Claude speaks, briefly lower the GLOBAL output volume so
#     other audio (music, a video) drops, then boost Claude's own `-v` gain to
#     compensate so Claude stays roughly as loud as before. The global cancels
#     out of the Claude-vs-other balance, so the amount Claude stands out is
#     pure digital gain and independent of the output device.
#
# The stateful "duck once per burst, restore once, never fight the user" logic
# lives in the daemon's Ducker; these are the low-level primitives it (and the
# one-shot speak.py path) build on. All settings are passed in explicitly so the
# long-lived daemon can honor per-request values instead of its stale env.


def gain_from_volume(vol):
    """Map CLAUDE_TALK_VOLUME to an afplay `-v` gain. Linear: 100 = unity (1.0).
    Kokoro peaks near -6 dBFS, so values above 100 amplify into that headroom
    (up to ~190 before clipping); playback clamps to the per-line clip ceiling so
    a boosted value never distorts. Above 100 is only audible with ducking off —
    while ducking, the compensation already pushes the gain to the ceiling."""
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


def duck_boosted_gain(base, ratio, audio=None):
    """Gain to play Claude at while ducked: boost by 1/ratio to counter the
    lowered global, capped so the line never clips."""
    if ratio <= 0:
        return base
    return min(base / ratio, clip_ceiling(audio))


def effective_ratio(base, requested):
    """The duck fraction actually applied to other audio.

    Ducking lowers the global volume and boosts Claude's gain to compensate, but
    that boost is capped at the clip ceiling. Below `base / DUCK_GAIN_CAP` the
    boost can't keep up, so Claude drops below its set volume — and since the
    Claude-vs-other gap is itself capped by that ceiling, ducking harder widens
    nothing, it only pulls Claude down. So we floor the ratio at that "sweet
    spot": the most aggressive duck that still keeps Claude at its volume, which
    already gives the maximum gap. A larger (gentler) requested ratio is honored.
    """
    if base <= 0:
        return min(0.95, max(0.1, requested))
    return min(0.95, max(base / DUCK_GAIN_CAP, requested, 0.1))


def get_volume_state():
    """Current macOS output as (volume 0-100, muted bool); (None, False) if it
    can't be read."""
    try:
        out = subprocess.run(
            [
                "osascript",
                "-e",
                "set v to get volume settings",
                "-e",
                '(output volume of v as text) & " " & (output muted of v as text)',
            ],
            capture_output=True,
            text=True,
            timeout=2,
        )
        vol_txt, muted_txt = out.stdout.strip().split()
        return int(vol_txt), muted_txt.lower() == "true"
    except Exception:
        return None, False


def set_system_volume(level):
    try:
        subprocess.run(
            ["osascript", "-e", f"set volume output volume {int(level)}"],
            capture_output=True,
            timeout=2,
        )
    except Exception:
        pass


def _duck_marker():
    return os.path.join(data_dir(), "duck_state.json")


def save_duck_marker(g_orig, g_duck):
    """Persist the pre-duck and ducked volumes so a hard kill mid-speech (which
    skips normal restore) can be recovered on the next start."""
    try:
        with open(_duck_marker(), "w") as f:
            json.dump({"g_orig": int(g_orig), "g_duck": int(g_duck)}, f)
    except OSError:
        pass


def read_duck_marker():
    try:
        with open(_duck_marker()) as f:
            d = json.load(f)
        return int(d["g_orig"]), int(d["g_duck"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def clear_duck_marker():
    try:
        os.unlink(_duck_marker())
    except OSError:
        pass


def recover_duck():
    """If a previous run was killed while ducked, restore the saved volume and
    clear the marker. Only restores if the system is still sitting at the ducked
    level — if it changed, the user or another app took over, so leave it."""
    m = read_duck_marker()
    if not m:
        return
    g_orig, g_duck = m
    cur, _ = get_volume_state()
    if cur is None or cur == g_duck:
        set_system_volume(g_orig)
    clear_duck_marker()


def duck_start(ratio):
    """One-shot duck: lower the global volume to `ratio` of its current level,
    once. Returns (g_orig, g_duck) to pass back to duck_stop, or None if there
    was nothing to duck (unreadable, muted, already low)."""
    cur, muted = get_volume_state()
    if cur is None or muted or cur <= 0:
        return None
    g_duck = round(cur * ratio)
    if g_duck >= cur:
        return None
    set_system_volume(g_duck)
    save_duck_marker(cur, g_duck)
    return cur, g_duck


def duck_stop(state):
    """Undo a duck_start — but only if the system is still at the level we set,
    so we never clobber a change the user made while we were speaking."""
    if not state:
        return
    g_orig, g_duck = state
    cur, _ = get_volume_state()
    if cur is None or cur == g_duck:
        set_system_volume(g_orig)
    clear_duck_marker()


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
        "ratio": min(0.95, max(0.1, _float("CLAUDE_TALK_DUCK_RATIO", "0.5"))),
        "hold": max(0.0, _float("CLAUDE_TALK_DUCK_HOLD", "1.2")),
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
