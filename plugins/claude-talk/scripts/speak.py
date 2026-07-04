#!/usr/bin/env python3
"""One-shot Kokoro synthesis for claude-talk — the fallback used when the daemon
path fails. Reads text from argv or stdin, synthesizes, and plays via afplay.
"""

import os
import subprocess
import sys
import tempfile

import kokoro_common as kc

kc.find_espeak()

MODEL, VOICES = kc.model_paths()
DEFAULT_VOICE = os.environ.get("KOKORO_VOICE", "af_heart")
SPEED = float(os.environ.get("KOKORO_SPEED", "1.0"))


def main():
    text = " ".join(sys.argv[1:]).strip() or sys.stdin.read().strip()
    if not text:
        return 0

    import numpy as np
    import soundfile as sf
    from kokoro_onnx import Kokoro

    kokoro = Kokoro(MODEL, VOICES)
    parts, sr = [], 24000
    for piece in kc.chunk(text):
        samples, sr = kokoro.create(
            piece, voice=DEFAULT_VOICE, speed=SPEED, lang="en-us"
        )
        parts.append(samples)
        parts.append(np.zeros(int(sr * 0.12), dtype=samples.dtype))
    audio = np.concatenate(parts)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        out = f.name
    sf.write(out, audio, sr)

    orig_volume = kc.get_system_volume() if kc.duck_enabled() else None
    gain = 1.0
    if orig_volume is not None:
        kc.set_system_volume(kc.duck_level())
        gain = kc.duck_boost(orig_volume, kc.duck_level())
    try:
        subprocess.run(["afplay", "-v", f"{gain:.2f}", out], check=False)
    finally:
        if orig_volume is not None:
            kc.set_system_volume(orig_volume)
        os.unlink(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
