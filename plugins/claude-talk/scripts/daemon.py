#!/usr/bin/env python3
"""Persistent Kokoro TTS daemon for claude-talk.

Loads the model once and stays resident on a Unix socket. Requests are JSON:
  {"voice": "...", "speed": 1.0, "text": "...", "remember": true,
   "wait_done": false}                            -> enqueue speech; if remember,
                                                     cache the rendered wav so it
                                                     can be replayed instantly. If
                                                     wait_done, the ack is held
                                                     back until the line has
                                                     actually finished playing
                                                     (not merely queued), so the
                                                     caller learns when speech ends
  {"stop": true}                                  -> stop NOW: kill current
                                                     playback and drop the queue
  {"replay": true}                                -> replay the line the history
                                                     cursor points at (no re-synth)
  {"history": "back"|"forward"}                   -> step the history cursor to an
                                                     older/newer line and replay it
  {"toggle_pause": true}                          -> pause or resume the line that
                                                     is currently playing
  {"warm": true}                                  -> no-op ack; used to spin the
                                                     daemon up (and load the model)
                                                     before the first spoken line

A finished "proper" line (remember) is cached and pushed onto a small on-disk
history ring, so replay and step-back survive a daemon idle-restart.
Speak and replay requests also carry the volume/duck settings ("volume", "duck",
"duck_ratio", "duck_hold") so config changes apply on the next line without a
daemon restart.

Each connection is handled on its own thread, so a wait_done ack that is parked
waiting for playback never blocks a barge-in stop from being accepted.

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
SPEAKING = os.path.join(DATA, "speaking")  # present while audio is playing/queued
HIST_DIR = os.path.join(DATA, "history")  # ring of recent lines, for repeat/back
HIST_MAX = 12  # how many recent lines to keep replayable
IDLE_TIMEOUT = 1800  # exit after 30 min with no requests


def set_speaking(on):
    """Maintain a marker file that exists while audio is playing or queued, so a
    status line can show a 'speaking' indicator and a completion chime can hold
    off (opt-in; see the README). Best-effort — never raises."""
    try:
        if on:
            with open(SPEAKING, "w"):
                pass
        else:
            os.unlink(SPEAKING)
    except OSError:
        pass


def load_history():
    """Recent-line wavs already on disk, oldest -> newest, so history (repeat /
    step-back) survives a daemon idle-restart."""
    try:
        files = sorted(f for f in os.listdir(HIST_DIR) if f.endswith(".wav"))
    except OSError:
        return []
    return [os.path.join(HIST_DIR, f) for f in files]


def max_seq(paths):
    """Highest numeric filename stem in the history dir, so new lines keep
    counting up (monotonic, collision-free across restarts)."""
    m = 0
    for p in paths:
        try:
            m = max(m, int(os.path.splitext(os.path.basename(p))[0]))
        except ValueError:
            pass
    return m


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
    """Serial speech player on a background thread, interruptible via stop().

    A spoken line is streamed: the first sentence is synthesized and starts
    playing while the rest synthesizes behind it, so time-to-first-audio is just
    the first sentence — not the whole line. Playback can be paused/resumed
    (SIGSTOP/SIGCONT on the live afplay), and every finished "proper" line is
    cached to a small on-disk ring so it can be replayed or stepped back to."""

    def __init__(self, kokoro):
        self.kokoro = kokoro
        self.q = queue.Queue()
        self.lock = threading.Lock()
        self.current = None      # currently-playing afplay Popen
        self.paused = False      # is `current` SIGSTOPped?
        self.generation = 0      # bumped on stop() to invalidate in-flight work
        self.ducker = Ducker()
        self.history = load_history()      # recent line wavs, oldest -> newest
        self.hist_seq = max_seq(self.history)
        self.cursor = len(self.history) - 1  # which line repeat/step points at
        threading.Thread(target=self._run, daemon=True).start()

    def submit(self, voice, speed, text, settings, remember=False, done=None):
        with self.lock:
            gen = self.generation
        self.q.put((gen, "speak", voice, speed, text, remember, settings, done))

    def replay(self, path, settings, done=None):
        """Queue a replay of a specific cached wav (no synthesis)."""
        with self.lock:
            gen = self.generation
        self.q.put((gen, "replay", None, None, path, False, settings, done))

    def replay_step(self, step, settings, done=None):
        """Move the history cursor (step: 0 = current, -1 = older, +1 = newer)
        and queue that line for replay. Returns False if there's no history."""
        with self.lock:
            if not self.history:
                if done is not None:
                    done.set()
                return False
            self.cursor = max(0, min(len(self.history) - 1, self.cursor + step))
            path = self.history[self.cursor]
        self.replay(path, settings, done)
        return True

    def toggle_pause(self):
        """Pause or resume the line that's playing. Returns the new paused state
        (False if nothing is playing to act on)."""
        with self.lock:
            if not (self.current and self.current.poll() is None):
                return False
            try:
                if self.paused:
                    os.kill(self.current.pid, signal.SIGCONT)
                    self.paused = False
                else:
                    os.kill(self.current.pid, signal.SIGSTOP)
                    self.paused = True
            except OSError:
                return False
            return self.paused

    def stop(self):
        with self.lock:
            self.generation += 1
            while True:
                try:
                    item = self.q.get_nowait()
                except queue.Empty:
                    break
                # Unblock any wait-until-spoken caller whose line we just dropped.
                if item[7] is not None:
                    item[7].set()
            if self.current and self.current.poll() is None:
                # A SIGSTOPped process ignores SIGTERM until continued — wake it
                # first so the terminate actually lands.
                if self.paused:
                    try:
                        os.kill(self.current.pid, signal.SIGCONT)
                    except OSError:
                        pass
                self.current.terminate()
            self.paused = False
        # Barge-in / shutdown: give the user their volume back right away.
        self.ducker.restore_now()
        set_speaking(False)

    def _stale(self, gen):
        with self.lock:
            return gen != self.generation

    def _remember(self, audio, sr):
        """Cache a finished line: write the newest-line wav and push a copy into
        the on-disk history ring, pruning the oldest past HIST_MAX. Resets the
        cursor to the newest line."""
        import soundfile as sf

        with self.lock:
            self.hist_seq += 1
            seq = self.hist_seq
        path = os.path.join(HIST_DIR, f"{seq:06d}.wav")
        try:
            os.makedirs(HIST_DIR, exist_ok=True)
            sf.write(path, audio, sr)
            shutil.copyfile(path, LAST_WAV)
        except OSError:
            return
        with self.lock:
            self.history.append(path)
            while len(self.history) > HIST_MAX:
                old = self.history.pop(0)
                try:
                    os.unlink(old)
                except OSError:
                    pass
            self.cursor = len(self.history) - 1

    def _play(self, gen, wav_path, audio, settings):
        """Play one wav via afplay, registered as `current` so stop/pause can
        reach it. Returns False if superseded before it could start."""
        gain = min(kc.gain_from_volume(settings["volume"]), kc.clip_ceiling(audio))
        with self.lock:
            if gen != self.generation:
                return False
            set_speaking(True)
            self.current = subprocess.Popen(["afplay", "-v", f"{gain:.3f}", wav_path])
        self.current.wait()
        return True

    def _run(self):
        while True:
            gen, kind, voice, speed, text, remember, settings, done = self.q.get()
            try:
                if self._stale(gen):
                    continue
                try:
                    if kind == "replay":
                        self._play_replay(gen, text, settings)
                    else:
                        self._play_streaming(gen, voice, speed, text, remember, settings)
                except Exception:
                    pass
                finally:
                    self.ducker.end(settings)
                    if self.q.empty():
                        set_speaking(False)
            finally:
                # Always release a wait-until-spoken caller, however this line
                # ended — played, dropped as stale, or errored.
                if done is not None:
                    done.set()

    def _play_replay(self, gen, path, settings):
        out = path or LAST_WAV
        if not out or not os.path.exists(out):
            return
        self.ducker.begin(settings)
        self._play(gen, out, None, settings)  # audio=None -> conservative gain cap

    def _play_streaming(self, gen, voice, speed, text, remember, settings):
        """Synthesize sentence-by-sentence and play each as soon as it's ready,
        synthesizing the NEXT sentence while the current one plays. First audio
        lands after just the first sentence; there's no full-line wait."""
        import numpy as np
        import soundfile as sf

        pieces = kc.stream_pieces(text)
        if self._stale(gen) or not pieces:
            return

        def synth(piece):
            samples, sr = self.kokoro.create(
                piece, voice=voice, speed=speed, lang="en-us"
            )
            seg = np.concatenate(
                [samples, np.zeros(int(sr * 0.1), dtype=samples.dtype)]
            )
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                out = f.name
            sf.write(out, seg, sr)
            return out, seg, sr

        collected, sr = [], 24000
        began = False
        nxt = synth(pieces[0])  # the only pre-audio wait
        for i in range(len(pieces)):
            if nxt is None:
                break
            path, seg, sr = nxt
            gain = min(kc.gain_from_volume(settings["volume"]), kc.clip_ceiling(seg))
            if not began:
                self.ducker.begin(settings)
                began = True
            with self.lock:
                if gen != self.generation:
                    self._unlink(path)
                    break
                collected.append(seg)
                set_speaking(True)
                self.current = subprocess.Popen(["afplay", "-v", f"{gain:.3f}", path])
                proc = self.current
            # Synthesize the NEXT sentence while THIS one is playing, so there's
            # no gap between sentences and only the first sentence costs latency.
            nxt = None
            if i + 1 < len(pieces) and not self._stale(gen):
                try:
                    nxt = synth(pieces[i + 1])
                except Exception:
                    nxt = None
            proc.wait()
            self._unlink(path)
            if self._stale(gen):
                if nxt is not None:
                    self._unlink(nxt[0])
                    nxt = None
                break

        # Cache the full line for repeat / history, but only if it played in full.
        if remember and len(collected) == len(pieces) and not self._stale(gen):
            self._remember(np.concatenate(collected), sr)

    @staticmethod
    def _unlink(path):
        try:
            os.unlink(path)
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

    # A prior daemon killed mid-duck may have left the volume low — put it back,
    # and clear any stale "speaking" marker a hard kill left behind.
    kc.recover_duck()
    set_speaking(False)

    player = Player(Kokoro(MODEL, VOICES))
    # Prime the phonemizer + ONNX graph with one throwaway synth so the FIRST
    # real spoken line pays synthesis time, not one-time init on top of it.
    try:
        player.kokoro.create("Ready.", voice="af_heart", speed=1.0, lang="en-us")
    except Exception:
        pass
    # Kill any in-flight playback (and restore the volume) on shutdown so a line
    # doesn't outlive the daemon and the user gets their volume back.
    atexit.register(player.stop)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    try:
        os.unlink(SOCK)
    except FileNotFoundError:
        pass

    def handle(conn):
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
                return

            # Warm-up: reaching this handler means the daemon is up and the model
            # is already loaded, so just ack — the first spoken line will be fast.
            if req.get("warm"):
                _reply(conn, b"OK\n")
                return

            # Pause / resume the line that's playing (toggle).
            if req.get("toggle_pause"):
                paused = player.toggle_pause()
                _reply(conn, b"PAUSED\n" if paused else b"OK\n")
                return

            # Replay: "history" steps the cursor (back/forward) through recent
            # lines; a plain replay repeats the line the cursor points at. NONE
            # lets the client fall back to re-synthesis from saved text.
            nav = req.get("history")
            if req.get("replay") or nav:
                step = {"back": -1, "forward": 1}.get(nav, 0)
                if player.replay_step(step, _settings(req)):
                    _reply(conn, b"OK\n")
                else:
                    _reply(conn, b"NONE\n")
                return

            text = (req.get("text") or "").strip()
            voice = req.get("voice") or "af_heart"
            try:
                speed = float(req.get("speed") or 1.0)
            except (TypeError, ValueError):
                speed = 1.0

            if not text:
                _reply(conn, b"EMPTY\n")
                return

            # wait_done: hold the connection open and ack only after this line has
            # actually finished playing (not merely queued), so the caller's turn
            # ends when the audio does. On its own thread, so a barge-in stop is
            # still accepted and unblocks us.
            done = threading.Event() if req.get("wait_done") else None
            player.submit(
                voice,
                speed,
                text,
                _settings(req),
                remember=bool(req.get("remember")),
                done=done,
            )
            if done is not None:
                conn.settimeout(None)
                done.wait(timeout=300)
                _reply(conn, b"DONE\n")
            else:
                _reply(conn, b"OK\n")

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK)
    srv.listen(8)
    srv.settimeout(IDLE_TIMEOUT)

    while True:
        try:
            conn, _ = srv.accept()
        except socket.timeout:
            break  # idle -> exit and free RAM
        threading.Thread(target=handle, args=(conn,), daemon=True).start()

    try:
        os.unlink(SOCK)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    main()
