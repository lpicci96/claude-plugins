#!/usr/bin/env python3
"""Interactive configuration for claude-talk: pick a voice, set speed, volume,
ducking, and an optional name, and write config.env. Starts from the current
config so re-running only changes what you answer. Run by install.sh (needs a
real terminal). Re-run any time with:  install.sh --configure
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
    gain = min(gain, kc.clip_ceiling(audio))  # boosted previews never clip
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        out = f.name
    sf.write(out, audio, sr)
    try:
        subprocess.run(["afplay", "-v", f"{gain:.3f}", out], check=False)
    finally:
        os.unlink(out)


KNOWN_KEYS = (
    "KOKORO_VOICE",
    "KOKORO_SPEED",
    "CLAUDE_TALK_NAME",
    "CLAUDE_TALK_VOLUME",
    "CLAUDE_TALK_DUCK",
    "CLAUDE_TALK_SESSION_VOICE",
)


def read_config():
    """Existing config.env as (values dict, extra raw lines). The values seed
    the prompts' defaults; the extra lines (e.g. advanced duck tuning) are
    rewritten verbatim so reconfiguring never drops them."""
    cfg, extras = {}, []
    try:
        with open(CONFIG) as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                if k in KNOWN_KEYS:
                    cfg[k] = v.strip().strip('"')
                else:
                    extras.append(line)
    except OSError:
        pass
    return cfg, extras


def main():
    from kokoro_onnx import Kokoro

    print("\nLoading the voice model (a few seconds)...")
    kokoro = Kokoro(MODEL, VOICES)

    cfg, extras = read_config()
    voice = cfg.get("KOKORO_VOICE") or "af_heart"
    try:
        speed = float(cfg.get("KOKORO_SPEED") or 1.0)
    except ValueError:
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

    try:
        volume = min(190, max(0, int(float(cfg.get("CLAUDE_TALK_VOLUME") or 100))))
    except ValueError:
        volume = 100
    vol_in = input(
        f"\nHow loud should I be? 100 = normal, up to 190 louder [{volume}]"
        " (Enter to keep) > "
    ).strip()
    if vol_in:
        try:
            volume = min(190, max(0, int(float(vol_in))))
        except ValueError:
            print(f"  keeping {volume}")
    try:
        print("  previewing volume...")
        play(kokoro, voice, speed, "This is my speaking volume.", volume / 100.0)
    except Exception:
        pass

    duck = "off" if cfg.get("CLAUDE_TALK_DUCK", "on").strip().lower() in (
        "0", "off", "false", "no", ""
    ) else "on"
    yn = "[Y/n]" if duck == "on" else "[y/N]"
    duck_in = input(
        f"\nDim other audio (music, video) while I speak? {yn} (Enter to keep) > "
    ).strip().lower()
    if duck_in:
        duck = "off" if duck_in in ("n", "no", "off", "0", "false") else "on"

    svoice = "same" if cfg.get("CLAUDE_TALK_SESSION_VOICE", "distinct").strip().lower() == "same" else "distinct"
    yn = "[Y/n]" if svoice == "distinct" else "[y/N]"
    sv_in = input(
        f"\nWhen you run more than one talk session at once, give each its own"
        f" voice so you can tell them apart? {yn} (Enter to keep) > "
    ).strip().lower()
    if sv_in:
        svoice = "same" if sv_in in ("n", "no", "off", "0", "false", "same") else "distinct"

    name = cfg.get("CLAUDE_TALK_NAME", "")
    name_in = input(
        f"\nWhat should I call you? [{name or 'no name'}] (Enter to keep) > "
    ).strip()
    if name_in:
        name = name_in

    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    with open(CONFIG, "w") as f:
        f.write(f"KOKORO_VOICE={voice}\n")
        f.write(f"KOKORO_SPEED={speed}\n")
        f.write(f'CLAUDE_TALK_NAME="{name}"\n')
        f.write(f"CLAUDE_TALK_VOLUME={volume}\n")
        f.write(f"CLAUDE_TALK_DUCK={duck}\n")
        f.write(f"CLAUDE_TALK_SESSION_VOICE={svoice}\n")
        for line in extras:
            f.write(f"{line}\n")

    print(f"\nSaved {CONFIG}")
    print(f"  voice  = {voice}")
    print(f"  speed  = {speed}")
    print(f"  volume = {volume}")
    print(f"  duck   = {duck}")
    print(f"  multi-session voice = {svoice}")
    if name:
        print(f"  name   = {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
