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

    def submit(self, voice, speed, text, remember=False):
        with self.lock:
            gen = self.generation
        self.q.put((gen, "speak", voice, speed, text, remember))

    def replay(self):
        """Queue a replay of the cached wav (no synthesis)."""
        with self.lock:
            gen = self.generation
        self.q.put((gen, "replay", None, None, None, False))

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
                kc.save_duck_state(vol)
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
                    if kc.should_restore_volume(self.duck_orig_volume):
                        kc.set_system_volume(self.duck_orig_volume)
                    kc.clear_duck_state()
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
            gen, kind, voice, speed, text, remember = self.q.get()
            if self._stale(gen):
                continue
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
                with self.lock:
                    if gen != self.generation:
                        if delete_after:
                            os.unlink(out)
                        continue
                    gain = self.duck_start()
                    self.current = subprocess.Popen(
                        ["afplay", "-v", f"{gain:.2f}", out]
                    )
                # Cache this line's audio so a later replay skips synthesis. Safe
                # to copy while afplay reads `out` (read-only source).
                if remember:
                    try:
                        shutil.copyfile(out, LAST_WAV)
                    except OSError:
                        pass
                self.current.wait()
                self.duck_release()
                if delete_after:
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

    # A previous daemon killed mid-speech (SIGKILL skips atexit) would have left
    # the system volume stuck at duck_level(); put it back before we start.
    kc.recover_duck_state()

    from kokoro_onnx import Kokoro

    player = Player(Kokoro(MODEL, VOICES))
    # stop() (not just duck_release) so a shutdown mid-line kills the boosted
    # afplay before restoring volume, instead of leaving it playing at a
    # boosted gain against the now-restored (louder) system volume.
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
                    player.replay()
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

            player.submit(voice, speed, text, remember=bool(req.get("remember")))
            _reply(conn, b"OK\n")

    try:
        os.unlink(SOCK)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    main()
