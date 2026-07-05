#!/usr/bin/env python3
"""One-shot Kokoro synthesis for claude-talk — the fallback used when the daemon
path fails. Reads text from argv or stdin, synthesizes, and plays via afplay.
"""

import os
import signal
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

    # A prior run killed mid-duck may have left the volume low — put it back.
    kc.recover_duck()

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

    # Claude's own volume (independent of the system volume, never ducked), plus
    # a one-shot duck of the media apps that are playing while we speak.
    s = kc.env_settings()
    gain = min(kc.gain_from_volume(s["volume"]), kc.clip_ceiling(audio))
    duck_state = kc.app_duck_start(s["apps"], s["ratio"]) if s["duck"] else {}

    proc = None

    def _die(*_):
        # SIGTERM bypasses try/finally, so kill the child, un-duck the apps, and
        # clean up here — otherwise a hard kill leaves afplay running and an app
        # stuck quiet.
        if proc is not None and proc.poll() is None:
            proc.terminate()
        kc.app_duck_stop(duck_state)
        try:
            os.unlink(out)
        except OSError:
            pass
        os._exit(1)

    signal.signal(signal.SIGTERM, _die)
    try:
        proc = subprocess.Popen(["afplay", "-v", f"{gain:.3f}", out])
        proc.wait()
    finally:
        kc.app_duck_stop(duck_state)
        os.unlink(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
