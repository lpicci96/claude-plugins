"""Shared helpers for the claude-talk Python scripts (daemon, speak, setup)."""

import os
import re
import subprocess

MAX_CHARS = 400


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


def duck_enabled():
    return os.environ.get("CLAUDE_TALK_DUCK", "1") not in ("0", "false", "False", "")


def duck_level():
    """Target system output volume (0-100) for OTHER audio while speaking."""
    try:
        return max(0, min(100, int(os.environ.get("CLAUDE_TALK_DUCK_LEVEL", "30"))))
    except ValueError:
        return 30


def duck_hold_seconds():
    """How long to keep the system ducked after a line finishes, so back-to-back
    lines in a narration don't flicker the volume up and down between them."""
    try:
        return max(0.0, float(os.environ.get("CLAUDE_TALK_DUCK_HOLD", "1.2")))
    except ValueError:
        return 1.2


def duck_boost(orig_volume, duck_to):
    """afplay gain to keep our own voice close to its original loudness while
    the system output (and everything else) is ducked to duck_to. Capped so we
    don't push the signal into clipping."""
    if orig_volume <= 0:
        return 1.0
    return min(1.8, orig_volume / max(duck_to, 1))


def get_system_volume():
    """Current macOS output volume (0-100), or None if it can't be read."""
    try:
        out = subprocess.run(
            ["osascript", "-e", "output volume of (get volume settings)"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        return int(out.stdout.strip())
    except Exception:
        return None


def set_system_volume(level):
    try:
        subprocess.run(
            ["osascript", "-e", f"set volume output volume {int(level)}"],
            capture_output=True,
            timeout=2,
        )
    except Exception:
        pass


def should_restore_volume(orig_volume):
    """Whether it's safe to restore the system volume to orig_volume.

    We only lowered the volume to duck_level(); if it's no longer sitting at
    that level, the user (or another app) changed it while we were speaking, so
    restoring would clobber their choice. Leave it alone in that case. If the
    volume can't be read, fall back to restoring (best effort)."""
    cur = get_system_volume()
    return cur is None or cur == duck_level()


def _duck_state_path():
    return os.path.join(data_dir(), "duck_state")


def save_duck_state(orig_volume):
    """Persist the pre-duck volume so a hard kill mid-speech (which skips the
    normal restore) can be recovered on the next start — otherwise the system
    is left stuck at duck_level()."""
    try:
        with open(_duck_state_path(), "w") as f:
            f.write(str(int(orig_volume)))
    except OSError:
        pass


def clear_duck_state():
    try:
        os.unlink(_duck_state_path())
    except OSError:
        pass


def recover_duck_state():
    """If a previous run was killed while ducked, restore the volume it saved
    and clear the marker. No-op when there's nothing to recover."""
    try:
        with open(_duck_state_path()) as f:
            orig = int(f.read().strip())
    except (OSError, ValueError):
        return
    set_system_volume(orig)
    clear_duck_state()


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
