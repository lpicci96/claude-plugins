#!/usr/bin/env python3
"""Persistent Kokoro TTS daemon for claude-talk.

Loads the model once and stays resident on a Unix socket. Requests are JSON:
  {"voice": "...", "speed": 1.0, "text": "...", "remember": true}
                                                  -> enqueue speech; if remember,
                                                     cache the rendered wav so it
                                                     can be replayed instantly
  {"stop": true}                                  -> stop NOW: kill current
                                                     playback and drop the queue
  {"replay": true}                                -> replay the cached wav with no
                                                     re-synthesis (zero latency)

Playback runs on a background thread, so a stop request can interrupt speech
that is already playing or still queued (barge-in). Single instance (flock);
exits after IDLE_TIMEOUT with no requests to free RAM.

Local only: Unix socket (a file), no network port, no outbound calls at runtime.
"""

import atexit
import fcntl
import json
import os
import queue
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading

import kokoro_common as kc

kc.find_espeak()

MODEL, VOICES = kc.model_paths()
DATA = kc.data_dir()
SOCK = os.path.join(DATA, "tts.sock")
LOCK = os.path.join(DATA, "daemon.lock")
LAST_WAV = os.path.join(DATA, "last.wav")  # cached audio of the last "proper" line
IDLE_TIMEOUT = 1800  # exit after 30 min with no requests


class Ducker:
    """Smart output-volume ducking for a speaking burst.

    Ducks the global volume once when a burst starts and restores it once, after
    a short hold, when the burst ends — so back-to-back lines don't flicker the
    volume. Never fights the user: if the volume no longer sits at the value we
    set (they moved it, or another app did), we adopt their choice and stop
    ducking for the rest of the burst instead of clobbering it. All per-request
    settings are passed in so the long-lived daemon honors the latest config.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.ducked = False       # we have lowered the global volume
        self.suspended = False    # user took control mid-burst; back off
        self.g_orig = None        # volume before we ducked
        self.g_duck = None        # volume we ducked to
        self.timer = None         # pending restore

    def begin(self, audio, s):
        """Ensure the right duck state for the line about to play; return the
        afplay gain to use for it."""
        base = kc.gain_from_volume(s["volume"])
        with self.lock:
            self._cancel_timer()
            if not s["duck"]:
                # Ducking off (or just toggled off): undo any duck, play flat.
                self._restore_locked()
                self.suspended = False
                return base
            if self.suspended:
                return base
            cur, muted = kc.get_volume_state()
            if self.ducked:
                if cur is None or cur == self.g_duck:
                    return kc.duck_boosted_gain(base, s["ratio"], audio)
                # Volume moved out from under us -> the user is in charge now.
                self.ducked = False
                self.suspended = True
                kc.clear_duck_marker()
                return base
            if cur is None or muted or cur <= 0:
                return base
            g_duck = round(cur * s["ratio"])
            if g_duck >= cur:
                return base
            kc.set_system_volume(g_duck)
            self.g_orig, self.g_duck, self.ducked = cur, g_duck, True
            kc.save_duck_marker(cur, g_duck)
            return kc.duck_boosted_gain(base, s["ratio"], audio)

    def end(self, s):
        """A line finished: arm the restore so the volume comes back once the
        burst has been quiet for the hold window."""
        with self.lock:
            if not (self.ducked or self.suspended):
                return
            self._cancel_timer()
            self.timer = threading.Timer(s["hold"], self._on_hold)
            self.timer.daemon = True
            self.timer.start()

    def restore_now(self):
        """Restore immediately (barge-in / shutdown)."""
        with self.lock:
            self._cancel_timer()
            self._restore_locked()
            self.suspended = False

    def _on_hold(self):
        with self.lock:
            self.timer = None
            self._restore_locked()
            self.suspended = False  # burst over

    def _restore_locked(self):
        if self.ducked:
            cur, _ = kc.get_volume_state()
            if cur is None or cur == self.g_duck:
                kc.set_system_volume(self.g_orig)
            kc.clear_duck_marker()
            self.ducked = False

    def _cancel_timer(self):
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None


class Player:
    """Serial speech player on a background thread, interruptible via stop()."""

    def __init__(self, kokoro):
        self.kokoro = kokoro
        self.q = queue.Queue()
        self.lock = threading.Lock()
        self.current = None      # currently-playing afplay Popen
        self.generation = 0      # bumped on stop() to invalidate in-flight work
        self.ducker = Ducker()
        threading.Thread(target=self._run, daemon=True).start()

    def submit(self, voice, speed, text, settings, remember=False):
        with self.lock:
            gen = self.generation
        self.q.put((gen, "speak", voice, speed, text, remember, settings))

    def replay(self, settings):
        """Queue a replay of the cached wav (no synthesis)."""
        with self.lock:
            gen = self.generation
        self.q.put((gen, "replay", None, None, None, False, settings))

    def stop(self):
        with self.lock:
            self.generation += 1
            while True:
                try:
                    self.q.get_nowait()
                except queue.Empty:
                    break
            if self.current and self.current.poll() is None:
                self.current.terminate()
        # Barge-in / shutdown: give the user their volume back right away.
        self.ducker.restore_now()

    def _stale(self, gen):
        with self.lock:
            return gen != self.generation

    def _run(self):
        import numpy as np
        import soundfile as sf

        while True:
            gen, kind, voice, speed, text, remember, settings = self.q.get()
            if self._stale(gen):
                continue
            out = None
            delete_after = False
            audio = None
            try:
                if kind == "replay":
                    # No synthesis: just play the cached wav, and don't delete it.
                    if not os.path.exists(LAST_WAV):
                        continue
                    out, delete_after = LAST_WAV, False
                else:
                    parts, sr = [], 24000
                    for piece in kc.chunk(text):
                        if self._stale(gen):
                            break
                        samples, sr = self.kokoro.create(
                            piece, voice=voice, speed=speed, lang="en-us"
                        )
                        parts.append(samples)
                        parts.append(np.zeros(int(sr * 0.12), dtype=samples.dtype))
                    if self._stale(gen) or not parts:
                        continue
                    audio = np.concatenate(parts)
                    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                        out = f.name
                    sf.write(out, audio, sr)
                    delete_after = True
                gain = self.ducker.begin(audio, settings)
                with self.lock:
                    if gen != self.generation:
                        continue
                    self.current = subprocess.Popen(
                        ["afplay", "-v", f"{gain:.3f}", out]
                    )
                # Cache this line's audio so a later replay skips synthesis. Safe
                # to copy while afplay reads `out` (read-only source).
                if remember:
                    try:
                        shutil.copyfile(out, LAST_WAV)
                    except OSError:
                        pass
                self.current.wait()
            except Exception:
                pass
            finally:
                self.ducker.end(settings)
                if delete_after and out:
                    try:
                        os.unlink(out)
                    except OSError:
                        pass


def _reply(conn, msg):
    try:
        conn.sendall(msg)
    except Exception:
        pass


def _settings(req):
    """Per-request volume/duck settings, clamped to sane ranges."""

    def num(key, default, cast):
        try:
            return cast(req.get(key, default))
        except (TypeError, ValueError):
            return default

    duck = str(req.get("duck", "on")).strip().lower() not in (
        "0",
        "off",
        "false",
        "no",
        "",
    )
    return {
        "volume": min(100, max(0, num("volume", 100, lambda x: int(float(x))))),
        "duck": duck,
        "ratio": min(0.95, max(0.1, num("duck_ratio", 0.5, float))),
        "hold": max(0.0, num("duck_hold", 1.2, float)),
    }


def main():
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0  # another instance already running

    from kokoro_onnx import Kokoro

    # A prior daemon killed mid-duck may have left the volume low — put it back.
    kc.recover_duck()

    player = Player(Kokoro(MODEL, VOICES))
    # Kill any in-flight playback (and restore the volume) on shutdown so a line
    # doesn't outlive the daemon and the user gets their volume back.
    atexit.register(player.stop)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    try:
        os.unlink(SOCK)
    except FileNotFoundError:
        pass

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK)
    srv.listen(8)
    srv.settimeout(IDLE_TIMEOUT)

    while True:
        try:
            conn, _ = srv.accept()
        except socket.timeout:
            break  # idle -> exit and free RAM

        with conn:
            conn.settimeout(15)
            data = b""
            try:
                while True:
                    b = conn.recv(65536)
                    if not b:
                        break
                    data += b
            except socket.timeout:
                pass

            try:
                req = json.loads(data.decode("utf-8", "replace"))
            except Exception:
                req = {}

            if req.get("stop"):
                player.stop()
                _reply(conn, b"OK\n")
                continue

            if req.get("replay"):
                # NONE lets the client fall back to re-synthesis from saved text.
                if os.path.exists(LAST_WAV):
                    player.replay(_settings(req))
                    _reply(conn, b"OK\n")
                else:
                    _reply(conn, b"NONE\n")
                continue

            text = (req.get("text") or "").strip()
            voice = req.get("voice") or "af_heart"
            try:
                speed = float(req.get("speed") or 1.0)
            except (TypeError, ValueError):
                speed = 1.0

            if not text:
                _reply(conn, b"EMPTY\n")
                continue

            player.submit(
                voice, speed, text, _settings(req), remember=bool(req.get("remember"))
            )
            _reply(conn, b"OK\n")

    try:
        os.unlink(SOCK)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    main()
