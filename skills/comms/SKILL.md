---
name: comms
version: 1.0.0
description: |
  Proactive messaging to owner via primary interface. Rate-limited and
  quality-gated. Used by heartbeat for research findings and by other
  skills when they have something worth interrupting for.
triggers:
  - proactive push needed
  - research finding worth sharing
  - alert or urgent notification
tools:
  - send_message_to_owner
mutating: false
---

# Comms Skill — Proactive Owner Messaging

Send messages to the owner without being asked. This is an interrupt — use it
only when the information is worth the owner's attention right now.

## Contract

This skill guarantees:
- Rate limited: max 1 proactive push per 4 hours
- Quality gated: only push if score ≥ 4/5
- Messages are concise and actionable
- The owner can always ask for more detail

## Quality Gate

Before sending, score the message (1-5):
1. Generic, could google this — **DON'T SEND**
2. Mildly interesting but not urgent — **DON'T SEND**
3. Decent, worth filing but not interrupting — **DON'T SEND** (log to LIGHTHOUSE)
4. Genuinely useful, owner would want to know — **SEND**
5. Directly actionable for their situation — **SEND**

## Message Format

Keep it tight:
```
[emoji] *Title*
_Context line_

Key finding in 2-3 sentences.

_(source: where this came from — want to dig in?)_
```

## Anti-Patterns

- Sending trivial updates ("I'm still running")
- Bypassing the rate limit
- Long messages that bury the point
- Sending without scoring first
- Using this for conversation replies (this is for proactive pushes only)

## Tools Used

- `send_message_to_owner(text)` — send via primary interface (Telegram)
