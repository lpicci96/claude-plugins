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
