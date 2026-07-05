#!/usr/bin/env python3
"""Interactive configuration for claude-talk: pick a voice, set speed and an
optional name, and write config.env. Run by install.sh (needs a real terminal).
Re-run any time with:  install.sh --configure
"""

import os
import subprocess
import sys
import tempfile

import kokoro_common as kc

kc.find_espeak()

MODEL, VOICES = kc.model_paths()
CONFIG = os.path.join(kc.data_dir(), "config.env")

CURATED = [
    ("af_heart", "American female — warm (default)"),
    ("af_bella", "American female — expressive"),
    ("af_nicole", "American female — soft"),
    ("af_sarah", "American female — bright"),
    ("am_michael", "American male"),
    ("am_adam", "American male — deep"),
    ("bf_emma", "British female"),
    ("bm_george", "British male"),
]


def play(kokoro, voice, speed, text, gain=1.0):
    import numpy as np
    import soundfile as sf

    parts, sr = [], 24000
    for piece in kc.chunk(text):
        samples, sr = kokoro.create(piece, voice=voice, speed=speed, lang="en-us")
        parts.append(samples)
    audio = np.concatenate(parts)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        out = f.name
    sf.write(out, audio, sr)
    try:
        subprocess.run(["afplay", "-v", f"{gain:.3f}", out], check=False)
    finally:
        os.unlink(out)


def main():
    from kokoro_onnx import Kokoro

    print("\nLoading the voice model (a few seconds)...")
    kokoro = Kokoro(MODEL, VOICES)

    voice = "af_heart"
    speed = 1.0

    print("\n=== Pick a voice ===")
    for i, (v, d) in enumerate(CURATED, 1):
        print(f"  {i}. {v:11}  {d}")
    print("  Enter a number to hear it, type a voice id directly, or press")
    print("  Enter / 'ok' to keep the current one.")
    while True:
        choice = input(f"\nVoice [{voice}] > ").strip().lower()
        if choice in ("", "ok", "done", "q"):
            break
        if choice.isdigit() and 1 <= int(choice) <= len(CURATED):
            voice = CURATED[int(choice) - 1][0]
        else:
            voice = choice
        try:
            print(f"  playing {voice}...")
            play(kokoro, voice, speed, f"Hi, this is {voice}. This is how I sound.")
        except Exception as e:
            print(f"  (couldn't play {voice}: {e})")

    while True:
        sp = input(f"\nSpeed 0.8-1.3 [{speed}] (Enter to keep) > ").strip()
        if sp == "":
            break
        try:
            speed = float(sp)
            print("  previewing...")
            play(kokoro, voice, speed, "This is the speaking pace.")
        except ValueError:
            print("  enter a number like 1.1")

    volume = 100
    vol_in = input(f"\nHow loud should I be? 0-100 [{volume}] (Enter to keep) > ").strip()
    if vol_in:
        try:
            volume = min(100, max(0, int(float(vol_in))))
        except ValueError:
            print("  keeping 100")
    try:
        print("  previewing volume...")
        play(kokoro, voice, speed, "This is my speaking volume.", volume / 100.0)
    except Exception:
        pass

    duck_in = input(
        "\nDim other audio (music, video) while I speak? [Y/n] > "
    ).strip().lower()
    duck = "off" if duck_in in ("n", "no", "off", "0", "false") else "on"

    name = input("\nWhat should I call you? (optional, Enter to skip) > ").strip()

    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    with open(CONFIG, "w") as f:
        f.write(f"KOKORO_VOICE={voice}\n")
        f.write(f"KOKORO_SPEED={speed}\n")
        f.write(f'CLAUDE_TALK_NAME="{name}"\n')
        f.write(f"CLAUDE_TALK_VOLUME={volume}\n")
        f.write(f"CLAUDE_TALK_DUCK={duck}\n")

    print(f"\nSaved {CONFIG}")
    print(f"  voice  = {voice}")
    print(f"  speed  = {speed}")
    print(f"  volume = {volume}")
    print(f"  duck   = {duck}")
    if name:
        print(f"  name   = {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
