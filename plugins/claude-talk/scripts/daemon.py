#!/usr/bin/env python3
"""Persistent Kokoro TTS daemon for claude-talk.

Loads the model once and stays resident on a Unix socket. Requests are JSON:
  {"voice": "...", "speed": 1.0, "text": "..."}  -> enqueue speech
  {"stop": true}                                  -> stop NOW: kill current
                                                     playback and drop the queue

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
IDLE_TIMEOUT = 1800  # exit after 30 min with no requests


class Player:
    """Serial speech player on a background thread, interruptible via stop()."""

    def __init__(self, kokoro):
        self.kokoro = kokoro
        self.q = queue.Queue()
        self.lock = threading.Lock()
        self.current = None      # currently-playing afplay Popen
        self.generation = 0      # bumped on stop() to invalidate in-flight work
        self.duck_lock = threading.Lock()
        self.duck_orig_volume = None  # system volume before we ducked it
        self.duck_timer = None        # pending "restore after hold" timer
        threading.Thread(target=self._run, daemon=True).start()

    def submit(self, voice, speed, text):
        with self.lock:
            gen = self.generation
        self.q.put((gen, voice, speed, text))

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
        self.duck_release(immediate=True)

    def _stale(self, gen):
        with self.lock:
            return gen != self.generation

    def duck_start(self):
        """Lower system output volume so other audio doesn't compete, and
        return the afplay gain to use so our own voice stays close to its
        original loudness."""
        if not kc.duck_enabled():
            return 1.0
        with self.duck_lock:
            if self.duck_timer:
                self.duck_timer.cancel()
                self.duck_timer = None
            if self.duck_orig_volume is None:
                vol = kc.get_system_volume()
                if vol is None:
                    return 1.0
                self.duck_orig_volume = vol
                kc.set_system_volume(kc.duck_level())
            return kc.duck_boost(self.duck_orig_volume, kc.duck_level())

    def duck_release(self, immediate=False):
        """Restore system volume. By default waits a short hold so back-to-back
        lines don't flicker the volume between them; immediate=True (barge-in,
        shutdown) restores right away."""
        if not kc.duck_enabled():
            return

        def restore():
            with self.duck_lock:
                if self.duck_orig_volume is not None:
                    kc.set_system_volume(self.duck_orig_volume)
                    self.duck_orig_volume = None
                self.duck_timer = None

        with self.duck_lock:
            if self.duck_timer:
                self.duck_timer.cancel()
                self.duck_timer = None
            if self.duck_orig_volume is None:
                return
            if not immediate:
                self.duck_timer = threading.Timer(kc.duck_hold_seconds(), restore)
                self.duck_timer.daemon = True
                self.duck_timer.start()
                return
        restore()  # immediate: run outside the lock restore() itself acquires

    def _run(self):
        import numpy as np
        import soundfile as sf

        while True:
            gen, voice, speed, text = self.q.get()
            if self._stale(gen):
                continue
            try:
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
                with self.lock:
                    if gen != self.generation:
                        os.unlink(out)
                        continue
                    gain = self.duck_start()
                    self.current = subprocess.Popen(
                        ["afplay", "-v", f"{gain:.2f}", out]
                    )
                self.current.wait()
                self.duck_release()
                try:
                    os.unlink(out)
                except OSError:
                    pass
            except Exception:
                pass


def _reply(conn, msg):
    try:
        conn.sendall(msg)
    except Exception:
        pass


def main():
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0  # another instance already running

    from kokoro_onnx import Kokoro

    player = Player(Kokoro(MODEL, VOICES))
    atexit.register(player.duck_release, immediate=True)
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

            text = (req.get("text") or "").strip()
            voice = req.get("voice") or "af_heart"
            try:
                speed = float(req.get("speed") or 1.0)
            except (TypeError, ValueError):
                speed = 1.0

            if not text:
                _reply(conn, b"EMPTY\n")
                continue

            player.submit(voice, speed, text)
            _reply(conn, b"OK\n")

    try:
        os.unlink(SOCK)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    main()
