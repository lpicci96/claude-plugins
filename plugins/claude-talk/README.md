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
CLAUDE_TALK_DUCK=on      # dim playing media apps (Spotify, Music) while speaking
```

Change these with `/talk-setup`, or re-run the picker: `install.sh --configure`.

### Volume and ducking

- **`CLAUDE_TALK_VOLUME`** sets Claude's loudness, applied as `afplay`'s
  per-instance gain. `100` is unity; the Kokoro voice sits a little below full
  scale, so values **above 100 (up to ~190)** make Claude louder by using that
  headroom (playback clamps so a boosted value never distorts).
- **`CLAUDE_TALK_DUCK`** dims other audio while Claude speaks, then restores it.
  It does this **without ever touching the system volume**: it reaches into each
  media app that's currently playing (Spotify, Apple Music) via AppleScript and
  lowers _that app's own_ volume, then puts it back after a short hold.

Because the global output volume — and Claude's voice — are left alone, your
volume dial always does exactly what you expect and your setting always sticks;
there's nothing to fight. Ducking happens once per speaking burst and restores
once after a short hold (no per-line flicker); it never clobbers a change you
make to the app yourself, and a crash mid-speech is recovered on the next line so
an app is never left stuck quiet.

The trade-off: only apps with a scriptable volume can be ducked. **Browser and
YouTube audio can't be dimmed this way** — Claude still plays over them at its
set loudness, just without lowering them. (Pause the tab, or nudge Claude up with
`CLAUDE_TALK_VOLUME`.)

Advanced tuning (optional, add to `config.env`):

```sh
CLAUDE_TALK_DUCK_RATIO=0.25       # duck a playing app to this fraction of its volume
CLAUDE_TALK_DUCK_HOLD=1.2         # seconds to stay ducked after a line before restoring
CLAUDE_TALK_DUCK_APPS=Spotify,Music  # which apps to duck (AppleScript app names)
```

Lower `CLAUDE_TALK_DUCK_RATIO` for a stronger duck (e.g. `0.1` = drop to 10%),
raise it toward `1.0` to keep the music more present. Add apps that expose an
AppleScript `sound volume` to `CLAUDE_TALK_DUCK_APPS`. To turn ducking off
entirely, set `CLAUDE_TALK_DUCK=off`.

## Uninstall

```bash
bash ~/.claude/plugins/cache/lpicci96/claude-talk/*/uninstall.sh
/plugin uninstall claude-talk@lpicci96
```

## Credits & license

Built on [Kokoro](https://github.com/hexgrad/kokoro) (Apache-2.0) and [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) (MIT). Model files are downloaded from the kokoro-onnx releases at install time.

claude-talk is MIT licensed — see [LICENSE](LICENSE).
