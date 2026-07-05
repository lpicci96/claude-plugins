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
Speak and replay requests also carry the volume/duck settings ("volume", "duck",
"duck_ratio", "duck_hold") so config changes apply on the next line without a
daemon restart.

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
    """Per-app ducking for a speaking burst — never touches the system volume.

    Claude's voice plays at its own afplay gain. To make it stand out we reach
    into each media app that's actually playing (Spotify, Apple Music) and lower
    THAT app's own volume, then restore it after a short hold. The global output
    volume and Claude's voice are left alone — so your volume dial always does
    exactly what you expect and your setting always sticks, and there's nothing
    to fight. Apps with no scriptable volume (browsers, etc.) simply aren't
    ducked; Claude just plays over them at its set loudness.

    We duck once at the start of a burst and restore once at the end, so
    back-to-back lines don't flicker an app's volume. We remember each app's true
    pre-duck volume across the whole burst-chain and only recapture it when the
    app has clearly moved on its own — so we never clobber a change you make
    mid-speech, and repeated duck/restore cycles don't slowly drift the volume
    down (app volumes quantize by a point per set). All per-request settings are
    passed in so the long-lived daemon honors the latest config.
    """

    SLOP = kc.APP_SLOP

    def __init__(self):
        self.lock = threading.Lock()
        self.orig = {}     # app -> true pre-duck volume (persists across turns)
        self.duck = {}     # app -> volume we ducked it to
        self.active = set()  # apps currently ducked (restore targets)
        self.timer = None  # pending restore / burst-end

    def begin(self, s):
        """Duck the playing apps for the line about to start (idempotent within a
        burst)."""
        with self.lock:
            self._cancel_timer()
            if not s["duck"]:
                self._restore_locked()
                return
            ratio = s["ratio"]
            for app in s["apps"]:
                st = kc.app_duck_state(app)
                if st is None or st[0] != "playing" or st[1] <= 0:
                    # Not running / not playing / already silent: don't newly
                    # duck. If we already ducked it (e.g. paused mid-burst), it
                    # stays in `active` so the burst-end restore still fires.
                    continue
                vol = st[1]
                if app in self.active and abs(vol - self.duck.get(app, -1)) <= self.SLOP:
                    continue  # already ducked and still there — keep going
                # Recover the true original: if the app is sitting where we last
                # left it (at our duck level or the orig we recorded), reuse that
                # exact orig so restores don't drift; otherwise it moved on its
                # own, so adopt the current level as the new original.
                known = self.orig.get(app)
                if known is not None and (
                    abs(vol - known) <= self.SLOP
                    or abs(vol - self.duck.get(app, -1)) <= self.SLOP
                ):
                    orig = known
                else:
                    orig = vol
                duck = round(orig * ratio)
                if duck >= orig:
                    continue
                kc.set_app_volume(app, duck)
                self.orig[app], self.duck[app] = orig, duck
                self.active.add(app)
            if self.active:
                kc.save_app_duck_marker(
                    {a: {"orig": self.orig[a], "duck": self.duck[a]} for a in self.active}
                )
            else:
                kc.clear_app_duck_marker()

    def end(self, s):
        """A line finished: arm the restore so the apps come back once the burst
        has been quiet for the hold window."""
        with self.lock:
            if not self.active:
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

    def _on_hold(self):
        with self.lock:
            self.timer = None
            self._restore_locked()

    def _restore_locked(self):
        # Restore each ducked app to its remembered original, but only if it's
        # still where we ducked it — if it moved, that's the user's level now.
        for app in self.active:
            st = kc.app_duck_state(app)
            if st is None or abs(st[1] - self.duck[app]) <= self.SLOP:
                kc.set_app_volume(app, self.orig[app])
        self.active = set()
        kc.clear_app_duck_marker()

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
                # Claude's own loudness (independent of the system volume, never
                # ducked); duck the other apps in parallel.
                gain = min(kc.gain_from_volume(settings["volume"]), kc.clip_ceiling(audio))
                self.ducker.begin(settings)
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
    raw_apps = str(req.get("duck_apps", "")).strip()
    apps = [a.strip() for a in raw_apps.split(",") if a.strip()] or list(
        kc.DEFAULT_DUCK_APPS
    )
    return {
        "volume": min(190, max(0, num("volume", 100, lambda x: int(float(x))))),
        "duck": duck,
        "ratio": min(0.95, max(0.0, num("duck_ratio", 0.25, float))),
        "hold": max(0.0, num("duck_hold", 1.2, float)),
        "apps": apps,
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
