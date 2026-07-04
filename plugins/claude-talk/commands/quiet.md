---
name: quiet
description: Exit conversation mode — stop speaking aloud, back to text only
---

Exit `/talk` conversation mode. From now on in this session:

- **Stop calling `~/.claude/claude-talk/bin/talk.sh`** — no more spoken output.
- Respond normally in text (your usual style, not the terse voice-first notes).
- Confirm briefly, in text, that voice is off.

Nothing needs cleaning up — the local TTS daemon idles out on its own. Re-enter anytime with `/talk`.
