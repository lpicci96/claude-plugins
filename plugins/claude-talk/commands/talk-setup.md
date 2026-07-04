---
name: talk-setup
description: Change the claude-talk voice, speed, or name
---

Help the user reconfigure their claude-talk voice. Drive it conversationally:

1. If `~/.claude/claude-talk/config.env` exists, read it to see the current voice/speed/name.
2. Offer a few voices and play a short sample of each — set the voice per call:

       KOKORO_VOICE=<voice> ~/.claude/claude-talk/bin/talk.sh "Hi, this is <voice>. This is how I sound."

   Good options: `af_heart`, `af_bella`, `af_nicole`, `af_sarah` (US female); `am_michael`, `am_adam` (US male); `bf_emma` (UK female); `bm_george` (UK male).

3. Ask which they want, their preferred speed (0.8–1.3), and an optional name.
4. Write `~/.claude/claude-talk/config.env` with exactly these lines:

       KOKORO_VOICE=<voice>
       KOKORO_SPEED=<speed>
       CLAUDE_TALK_NAME="<name>"

5. Confirm. Voice and speed are per-request, so the change takes effect on the very next spoken line — no restart needed.

**Terminal alternative:** the user can instead run `bash <plugin dir>/install.sh --configure` for a shell-based picker.
