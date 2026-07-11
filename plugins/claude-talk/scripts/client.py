#!/usr/bin/env python3
"""Client for the claude-talk daemon.

Sends text (argv or stdin) to the daemon over its Unix socket, starting the
daemon if it isn't running. Control flags instead of text:
  --stop            barge-in: stop playback and drop the queue
  --warm            spin the daemon up (load the model) for a fast first line
  --replay          replay the line the history cursor points at (no re-synth)
  --back/--forward  step to an older/newer line in history and replay it
  --pause           pause or resume the line that's playing (toggle)
Set KOKORO_WAIT_DONE=1 to have a speak call block until the line has actually
finished playing. Exits non-zero on failure so the shell wrapper can fall back to
the one-shot path.
"""

import json
import os
import socket
import subprocess
import sys
import time

DATA = os.environ.get("CLAUDE_TALK_HOME") or os.path.expanduser("~/.claude/claude-talk")
HERE = os.path.dirname(os.path.abspath(__file__))
SOCK = os.path.join(DATA, "tts.sock")
VENV_PY = os.path.join(DATA, "venv", "bin", "python")
DAEMON = os.path.join(HERE, "daemon.py")


def settings():
    """Volume + ducking settings from the environment (set by common.sh from
    config.env), sent per-request so the long-lived daemon honors the latest."""
    return {
        "volume": os.environ.get("CLAUDE_TALK_VOLUME", "100"),
        "duck": os.environ.get("CLAUDE_TALK_DUCK", "on"),
        "duck_ratio": os.environ.get("CLAUDE_TALK_DUCK_RATIO", "0.25"),
        "duck_hold": os.environ.get("CLAUDE_TALK_DUCK_HOLD", "1.2"),
        "duck_apps": os.environ.get("CLAUDE_TALK_DUCK_APPS", ""),
    }


def connect():
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(SOCK)
    return s


def start_daemon():
    log = open(os.path.join(DATA, "daemon.log"), "ab")
    subprocess.Popen(
        [VENV_PY, DAEMON], stdout=log, stderr=log, start_new_session=True, cwd=HERE
    )


def send_stop():
    try:
        s = connect()
    except OSError:
        return 0  # no daemon -> nothing to stop
    try:
        s.sendall(json.dumps({"stop": True}).encode("utf-8"))
        s.shutdown(socket.SHUT_WR)
        s.recv(16)
    except OSError:
        pass
    finally:
        try:
            s.close()
        except Exception:
            pass
    return 0


def send_warm():
    """Spin the daemon up so the model is loaded before the first spoken line.
    If it isn't running, start it and return immediately — the model loads in the
    background, so this never blocks the caller."""
    try:
        s = connect()
    except OSError:
        start_daemon()
        return 0
    try:
        s.sendall(json.dumps({"warm": True}).encode("utf-8"))
        s.shutdown(socket.SHUT_WR)
        s.recv(16)
    except OSError:
        pass
    finally:
        try:
            s.close()
        except Exception:
            pass
    return 0


def send_replay(nav=None):
    """Ask the daemon to replay a cached line. With no nav, repeats the line the
    history cursor points at; nav="back"/"forward" steps the cursor first. Returns
    0 if it will replay, or 2 so the caller can fall back to re-synthesis (no
    daemon / no history)."""
    try:
        s = connect()
    except OSError:
        return 2  # no daemon -> fall back
    req = {"history": nav, **settings()} if nav else {"replay": True, **settings()}
    try:
        s.sendall(json.dumps(req).encode("utf-8"))
        s.shutdown(socket.SHUT_WR)
        ack = s.recv(16)
        return 0 if ack.strip() == b"OK" else 2
    except OSError:
        return 2
    finally:
        try:
            s.close()
        except Exception:
            pass


def send_pause():
    """Toggle pause/resume on the line that's playing. No-op if nothing plays."""
    try:
        s = connect()
    except OSError:
        return 0
    try:
        s.sendall(json.dumps({"toggle_pause": True}).encode("utf-8"))
        s.shutdown(socket.SHUT_WR)
        s.recv(16)
    except OSError:
        pass
    finally:
        try:
            s.close()
        except Exception:
            pass
    return 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--stop":
        return send_stop()
    if len(sys.argv) > 1 and sys.argv[1] == "--warm":
        return send_warm()
    if len(sys.argv) > 1 and sys.argv[1] == "--replay":
        return send_replay()
    if len(sys.argv) > 1 and sys.argv[1] == "--back":
        return send_replay("back")
    if len(sys.argv) > 1 and sys.argv[1] == "--forward":
        return send_replay("forward")
    if len(sys.argv) > 1 and sys.argv[1] == "--pause":
        return send_pause()

    text = " ".join(sys.argv[1:]).strip() or sys.stdin.read().strip()
    if not text:
        return 0
    payload = json.dumps(
        {
            "voice": os.environ.get("KOKORO_VOICE", "af_heart"),
            "speed": os.environ.get("KOKORO_SPEED", "1.0"),
            "text": text,
            # Cache this line's audio for instant replay, unless it's a
            # fire-and-forget interim line (KOKORO_NOWAIT).
            "remember": not os.environ.get("KOKORO_NOWAIT"),
            # Block until the line has finished playing when asked (used for the
            # spoken wrap-up so the turn ends when the audio does).
            "wait_done": bool(os.environ.get("KOKORO_WAIT_DONE"))
            and not os.environ.get("KOKORO_NOWAIT"),
            **settings(),
        }
    ).encode("utf-8")

    s = None
    try:
        s = connect()
    except OSError:
        start_daemon()
        for _ in range(60):  # up to ~30s for first model load
            time.sleep(0.5)
            try:
                s = connect()
                break
            except OSError:
                continue

    if s is None:
        return 1

    try:
        s.sendall(payload)
        s.shutdown(socket.SHUT_WR)
        if os.environ.get("KOKORO_NOWAIT"):
            return 0  # fire-and-forget (interim lines)
        # Blocks here until the daemon acks: "OK" once queued, or "DONE" once the
        # line has finished playing (wait_done). No socket timeout, so a long
        # wait_done line is waited out in full.
        ack = s.recv(16)
        return 0 if ack.strip() in (b"OK", b"DONE") else 1
    except OSError:
        return 1
    finally:
        try:
            s.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
