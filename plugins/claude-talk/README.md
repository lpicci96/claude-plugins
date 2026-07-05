# claude-talk

Voice-first **conversation mode** for [Claude Code](https://claude.com/claude-code). Run `/talk` and have a spoken back-and-forth with Claude — it talks through what it's doing as it works, using a natural local voice. No API key, no cloud, fully offline after setup.

Unlike most "read the response aloud" tools, claude-talk is **opt-in** and built for conversation: you turn it on with `/talk`, it narrates the work as it goes, and you turn it off with `/quiet`.

## Requirements

- **macOS** (Apple Silicon or Intel) — uses `afplay` and Homebrew
- [Homebrew](https://brew.sh) (for `espeak-ng`)
- Python 3.10+ (`uv` is used if present, otherwise `python3 -m venv`)
- ~340 MB disk for the [Kokoro](https://github.com/hexgrad/kokoro) voice model

## Install

```bash
# 1. Add my plugin marketplace, then install claude-talk
/plugin marketplace add lpicci96/claude-plugins
/plugin install claude-talk@lpicci96

# 2. Run the one-time installer (model download + pick your voice)
bash ~/.claude/plugins/cache/lpicci96/claude-talk/*/install.sh
```

The installer sets up everything under `~/.claude/claude-talk/` (venv, model, config) — separate from the plugin, so updates never wipe it. It ends by letting you **preview and pick a voice and speed**.

The first time you run `/talk`, Claude Code asks permission to run the claude-talk command — choose **"Yes, and don't ask again."**

## Usage

| Command       | What it does                                        |
| ------------- | --------------------------------------------------- |
| `/talk`       | Enter conversation mode — Claude speaks its replies |
| `/quiet`      | Exit — back to text only                            |
| `/talk-setup` | Change voice, speed, or name                        |

You can also say "stop talking" to exit. Interrupt anytime — sending a new message stops whatever is playing.

### Hotkeys (optional, opt-in): stop and repeat

**These are entirely optional — the plugin does not install them, and everything above works without them.** If you want them, you set them up yourself once.

Sending a message already stops playback, but you can't do that when Claude is idle at the end of a turn. For a true "shut up now" button — and a "say that again" button that costs **zero tokens** — bind two global hotkeys with [`skhd`](https://github.com/koekeishiya/skhd):

```bash
brew install koekeishiya/formulae/skhd
mkdir -p ~/.config/skhd
cat >> ~/.config/skhd/skhdrc <<'EOF'
alt - escape : ~/.claude/claude-talk/bin/stop.sh    # stop speaking now
alt - r      : ~/.claude/claude-talk/bin/repeat.sh  # replay the last line
EOF
skhd --start-service
```

Then grant **skhd** Accessibility permission (System Settings → Privacy & Security → Accessibility) and restart it with `skhd --restart-service`.

- **Stop** kills playback and drops the queue instantly, from anywhere — even mid-turn or when Claude is idle.
- **Repeat** replays the last full line Claude spoke, with no LLM turn. The daemon caches that line's rendered audio, so repeat replays the file directly (near-instant, no re-synthesis); if the cache isn't warm it falls back to re-synthesizing from the saved text. Interim narration is skipped, so repeat replays the real reply. The cache is shared across sessions, so repeat always replays the most recent thing said by any talk session.

These use the **Option** modifier (`alt`) on purpose: `skhd` captures whatever combo you bind _globally_, so a `cmd`-based one would shadow app shortcuts everywhere it runs (e.g. `cmd - r` would swallow browser/Finder reload). Pick any keys you like, but avoid ones you rely on elsewhere.

### Completion chime (optional): stay silent while Claude talks

If you have a `Stop` hook that plays a "done" chime, it will chime on top of the
voice. To avoid that, every spoken line touches a per-session marker file:

```
~/.claude/claude-talk/spoke-<session_id>
```

Make your chime hook skip the sound when the marker is fresh, e.g.:

```bash
SID=$(sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
M="$HOME/.claude/claude-talk/spoke-$SID"
if [ -n "$SID" ] && [ -f "$M" ] && [ $(( $(date +%s) - $(stat -f %m "$M") )) -lt 20 ]; then
  exit 0  # claude-talk just spoke — skip the chime
fi
afplay /System/Library/Sounds/Glass.aiff
```

Markers older than a day are cleaned up automatically. If you don't use a chime
hook, the marker is harmless and you can ignore all of this.

## How it works

- **Engine:** [Kokoro-82M](https://github.com/hexgrad/kokoro) via [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx), run locally. 28 voices, US & UK, no API key.
- **Low latency:** a small background daemon keeps the model loaded, so replies start in ~1–2 s. It idles out after 30 minutes to free memory.
- **Barge-in:** a `UserPromptSubmit` hook stops playback the moment you send your next message.
- **Never breaks:** if the daemon or model is unavailable, it falls back to one-shot synthesis, then to macOS `say`.

## Configuration

Settings live in `~/.claude/claude-talk/config.env`:

```sh
KOKORO_VOICE=af_heart    # e.g. af_bella, am_michael, bf_emma, bm_george
KOKORO_SPEED=1.0         # 0.8–1.3
CLAUDE_TALK_NAME=""      # optional; what Claude calls you
CLAUDE_TALK_VOLUME=100   # Claude's voice loudness; 100 = normal, up to ~190 louder
CLAUDE_TALK_DUCK=on      # dim other audio (music, video) while Claude speaks
```

Change these with `/talk-setup`, or re-run the picker: `install.sh --configure`.

### Volume and ducking

- **`CLAUDE_TALK_VOLUME`** sets Claude's loudness, applied as `afplay`'s
  per-instance gain. `100` is unity; the Kokoro voice sits a little below full
  scale, so values **above 100 (up to ~190)** make Claude louder by using that
  headroom (playback clamps so a boosted value never distorts).
- **`CLAUDE_TALK_DUCK`** dims other audio while Claude speaks, then restores it.
  macOS has no way to lower only _other_ apps, so this briefly lowers the
  **global** output volume and boosts Claude's own gain to compensate — the net
  effect is music/video drop while Claude stays about as loud as before.

The volume knob still controls everything the way you'd expect. If you change the
system volume **while Claude is speaking**, ducking _re-centers_ the whole mix on
your new level — Claude and the other audio move together, the other audio stays
tucked under Claude, and your change persists after Claude finishes. Change the
volume in a gap (Claude silent) and it's a plain global change, left untouched.
Ducking happens once per speaking burst and restores once after a short hold (no
per-line flicker), and a crash mid-speech is recovered on the next line, so
you're never left stuck at a lowered volume.

Advanced tuning (optional, add to `config.env`):

```sh
CLAUDE_TALK_DUCK_RATIO=0.5   # duck other audio to this fraction of current volume
CLAUDE_TALK_DUCK_HOLD=1.2    # seconds to stay ducked after a line before restoring
```

**The duck auto-limits so Claude never drops below `CLAUDE_TALK_VOLUME`.** The
Kokoro voice can only be boosted so far before it clips, which caps both how loud
Claude can be _and_ how far the gap to other audio can open. Those two limits
meet at a **sweet spot** — roughly `CLAUDE_TALK_VOLUME / 190` — where the gap is
already as wide as it physically gets while Claude stays right at its set volume.
Asking for a **lower** ratio than that wouldn't widen the gap (it's capped); it
would only pull Claude down, so it's floored at the sweet spot. So in practice
you just set `CLAUDE_TALK_VOLUME`; the duck picks the most aggressive setting that
keeps Claude there. Raise `CLAUDE_TALK_DUCK_RATIO` above the sweet spot if you
want _gentler_ ducking (other audio stays more present).

To duck other audio _lower_ than the sweet spot allows, you have to accept Claude
sitting below its set volume — lower `CLAUDE_TALK_VOLUME`. Ducking lowers the
global volume, so it dims **all** other audio — browser tabs, YouTube, games —
not just scriptable players. To turn ducking off entirely, set
`CLAUDE_TALK_DUCK=off`.

## Uninstall

```bash
bash ~/.claude/plugins/cache/lpicci96/claude-talk/*/uninstall.sh
/plugin uninstall claude-talk@lpicci96
```

## Credits & license

Built on [Kokoro](https://github.com/hexgrad/kokoro) (Apache-2.0) and [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) (MIT). Model files are downloaded from the kokoro-onnx releases at install time.

claude-talk is MIT licensed — see [LICENSE](LICENSE).
