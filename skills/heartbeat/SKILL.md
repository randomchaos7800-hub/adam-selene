---
name: heartbeat
version: 1.0.0
description: |
  Autonomous idle behavior: memory compaction, conversation reflection, agenda-driven
  research, and proactive pushes. This skill describes the two-phase heartbeat cycle
  that runs when the agent is idle.
triggers:
  - idle >= 15 min (automatic)
  - reflection cycle
  - research pulse
  - memory compaction
tools:
  - read_memory
  - search_memory
  - write_memory
  - read_current_investigation
  - start_investigation
  - add_to_agenda
  - lighthouse_write
  - lighthouse_search
  - log_experiment
  - review_own_conversations
  - send_message_to_owner
mutating: true
---

# Heartbeat Skill — Autonomous Idle Behavior

The heartbeat fires periodically when the agent is idle. It's not a conversation
skill — it's an autonomous behavior loop that keeps the agent productive between
interactions.

## Contract

This skill guarantees:
- Memory compaction runs every tick (cheap, deterministic)
- Reflection runs if sufficient conversation exists (≥100 chars)
- Research only runs after 30+ min idle (don't waste cycles)
- Proactive pushes are rate-limited (max 1 per 4 hours)
- Quality gate on pushes (score ≥ 4/5 to interrupt the owner)
- All findings archived to LIGHTHOUSE regardless of push decision

## Phases

### Phase 0: Memory Compaction (every tick, no LLM)

Deterministic deduplication that runs before any LLM calls:

- **Tier 0:** Exact-duplicate removal within each entity (string match)
- **Tier 1:** Near-duplicate merging (SequenceMatcher ratio > 0.95)
- Both tiers deactivate (not delete) — reversible via `status: "superseded"`
- Logged to `compaction.log` for audit

### Phase 1: Reflection (15 min idle)

1. Create memory snapshot (for diff tracking)
2. Load recent conversation via `review_own_conversations()`
3. Analyze with fast model → JSON: successes, failures, patterns, suggestion
4. Log to experiment system
5. If actionable (failures + patterns + suggestion) → write to LIGHTHOUSE corrections
6. Prune snapshots older than 48h

### Phase 2: Research Pulse (30+ min idle)

1. Check for active working memory thread
   - If active → advance it one cycle (query → findings → next step)
   - If none → pull from agenda (or generate self-question)
2. Execute research via autoresearch API or web tools
3. Generate next step based on findings
4. When thread exhausted (6 cycles) or goal reached:
   - Synthesize all findings
   - Score quality (1-5)
   - Archive to LIGHTHOUSE
   - Push to owner if score ≥ 4 AND rate limit allows

### Self-Question Generation

When the agenda is empty, the agent generates its own research question:
- Based on recent conversation context and tacit knowledge
- Must be specific and grounded in the owner's actual situation
- Not generic AI news — tied to the owner's work
- Added to agenda at priority 2

## Rate Limiting

- **Push rate:** Max 1 proactive message per 4 hours
- **Quality gate:** Score must be ≥ 4/5 to push
- **Below threshold:** Findings go to LIGHTHOUSE only (still valuable, just not interrupt-worthy)

## Anti-Patterns

- Running research during active conversation (wait for idle)
- Pushing low-quality findings that waste the owner's attention
- Generating generic research questions unrelated to the owner's situation
- Skipping compaction (it's cheap and prevents memory bloat)
- Running reflection on trivial conversations (< 100 chars)

## Output Format

No direct output to owner (unless push threshold met).
All outputs are side effects: memory compaction, LIGHTHOUSE entries, research threads.

## Tools Used

- `review_own_conversations(hours?)` — recent conversation for reflection
- `read_current_investigation()` — check active research thread
- `start_investigation(goal, title)` — create new thread
- `add_to_agenda(topic, context, priority)` — queue research topics
- `lighthouse_write(section, title, content)` — archive findings and reflections
- `lighthouse_search(query)` — check for existing observations
- `log_experiment(hypothesis, result)` — record behavioral experiments
- `send_message_to_owner(text)` — push high-quality findings
- `search_memory(keyword)` — context for self-questions
- `write_memory(entity, fact, category)` — store discovered facts
