# Adam Selene Skill Resolver

This is the dispatcher. Skills are the implementation. **Read the skill file before acting.** If two skills could match, read both — they chain naturally.

## Always-on (every message)

| Trigger | Skill |
|---------|-------|
| Every inbound message (spawn parallel, don't block) | `skills/signal-detector/SKILL.md` |
| Any memory read/write/search/entity interaction | `skills/memory-ops/SKILL.md` |

## Knowledge retrieval

| Trigger | Skill |
|---------|-------|
| "What do you know about", "tell me about", "who is", "search for" | `skills/query/SKILL.md` |
| "Research this", "investigate", "dig into", "find out about" | `skills/research/SKILL.md` |
| Browse a URL, fetch a page, scrape content, screenshot | `skills/web-research/SKILL.md` |

## Self-reflection & reasoning

| Trigger | Skill |
|---------|-------|
| "Write to LIGHTHOUSE", reasoning observation, self-correction | `skills/lighthouse/SKILL.md` |
| Idle ≥15 min (automatic), reflection cycle, research pulse | `skills/heartbeat/SKILL.md` |

## Operational

| Trigger | Skill |
|---------|-------|
| Task add/complete/review, "what's on my plate" | `skills/task-manager/SKILL.md` |
| IRC message, channel management, log search, extract learnings | `skills/irc-ops/SKILL.md` |
| Send proactive message to owner, push findings | `skills/comms/SKILL.md` |

## Self-modification & code

| Trigger | Skill |
|---------|-------|
| Edit own code, write files, git commit, GitHub operations | `skills/code-ops/SKILL.md` |
| Change model, update settings, restart service, read config | `skills/config-ops/SKILL.md` |

## Disambiguation rules

When multiple skills could match:
1. Prefer the most specific skill (research over query for multi-step investigations)
2. memory-ops fires on ANY memory interaction — it's a behavior layer, not a standalone workflow
3. signal-detector is ambient — it never blocks the main response
4. If a URL is involved, route through web-research first, then let other skills consume the content
5. When in doubt, ask the owner

## Conventions (cross-cutting)

These apply to ALL skills:
- `skills/conventions/memory-first.md` — check memory before external sources
- `skills/conventions/l0-constraints.md` — constitutional guardrails on every action
- `skills/conventions/owner-auth.md` — privileged tools require owner identity
