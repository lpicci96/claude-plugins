---
name: talk-setup
description: Change the claude-talk voice, speed, or name
---

Help the user reconfigure their claude-talk voice. Drive it conversationally:

1. If `~/.claude/claude-talk/config.env` exists, read it to see the current voice/speed/name.
2. Offer a few voices and play a short sample of each — set the voice per call:

       KOKORO_VOICE=<voice> ~/.claude/claude-talk/bin/talk.sh "Hi, this is <voice>. This is how I sound."

   Good options: `af_heart`, `af_bella`, `af_nicole`, `af_sarah` (US female); `am_michael`, `am_adam` (US male); `bf_emma` (UK female); `bm_george` (UK male).

3. Ask which they want, their preferred speed (0.8–1.3), an optional name, how loud Claude should be (`CLAUDE_TALK_VOLUME`, 100 = normal, up to 190 louder), whether to dim other audio while speaking (`CLAUDE_TALK_DUCK`, on/off), and whether concurrent talk sessions should each get their own voice (`CLAUDE_TALK_SESSION_VOICE`, `distinct`/`same`).
4. Write `~/.claude/claude-talk/config.env` with these lines, preserving any existing values you're not changing and keeping any other keys (e.g. the advanced `CLAUDE_TALK_DUCK_RATIO` / `CLAUDE_TALK_DUCK_HOLD`) untouched:

       KOKORO_VOICE=<voice>
       KOKORO_SPEED=<speed>
       CLAUDE_TALK_NAME="<name>"
       CLAUDE_TALK_VOLUME=<0-190>
       CLAUDE_TALK_DUCK=<on|off>
       CLAUDE_TALK_SESSION_VOICE=<distinct|same>

5. Confirm. All of these are sent per-request, so the change takes effect on the very next spoken line — no restart needed. (`CLAUDE_TALK_VOLUME` is Claude's own loudness, independent of system volume; `CLAUDE_TALK_DUCK` dims other audio while Claude speaks and restores it after; `CLAUDE_TALK_SESSION_VOICE=distinct` gives each concurrent `/talk` session its own voice, and a `/talk same|different` argument overrides it for one session.)

**Terminal alternative:** the user can instead run `bash <plugin dir>/install.sh --configure` for a shell-based picker.
