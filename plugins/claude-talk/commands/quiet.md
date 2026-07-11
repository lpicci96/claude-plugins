---
name: quiet
description: Exit conversation mode — stop speaking aloud, back to text only
---

Exit `/talk` conversation mode. From now on in this session:

- **Clear the voice-mode mark** so the per-turn "keep speaking" reminder stops:

      ~/.claude/claude-talk/bin/talk.sh --off

- **Stop calling `~/.claude/claude-talk/bin/talk.sh`** — no more spoken output.
- Respond normally in text (your usual style, not the terse voice-first notes).
- Confirm briefly, in text, that voice is off.

Nothing else needs cleaning up — the local TTS daemon idles out on its own. Re-enter anytime with `/talk`.
