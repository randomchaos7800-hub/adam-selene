---
name: memory-ops
version: 1.0.0
description: |
  Core memory cycle: memory-first lookup, read-enrich-write loop, fact attribution,
  decay awareness. This is the ambient context layer — read this before any memory
  interaction.
triggers:
  - any memory read/write/search/entity interaction
tools:
  - read_memory
  - search_memory
  - list_entities
  - write_memory
  - read_timeline
  - read_tacit
  - review_own_conversations
  - update_my_instructions
  - log_experiment
mutating: true
---

# Memory Operations — The Ambient Context Layer

Memory is not an archive. It is a live context membrane that every interaction
flows through in both directions.

> **Convention:** See `skills/conventions/memory-first.md` for the 5-step lookup protocol.

## Contract

This skill guarantees:
- Memory is checked BEFORE any external source (memory-first lookup)
- Every inbound signal triggers the READ → ENRICH → WRITE loop
- Every outbound response checks memory for relevant context
- Source attribution on every fact written
- Owner's direct statements are highest-authority data
- Fact categories drive decay rates (status fades fast, milestones persist)

## Phases

### Phase 1: Memory-First Lookup (MANDATORY)

Before answering ANY question about a person, project, or concept:

1. `read_memory(entity)` — load summary + active facts
2. `search_memory(keyword)` — broader sweep for related entities
3. `read_timeline(date)` — if time-specific context needed
4. `read_tacit()` — owner's communication patterns and preferences
5. Check LIGHTHOUSE — have you reasoned about this before?

### Phase 2: Read → Enrich → Write

Every message that references an entity:

1. **Detect entities** — people, projects, companies mentioned
2. **Load memory** — read existing facts before responding
3. **Identify new information** — what does this message tell us that memory doesn't know?
4. **Write it back** — `write_memory()` with proper category and source
5. **Create if missing** — if notable and no entity exists, create it

### Phase 3: Decay Awareness

Facts decay at different rates. Know what's fresh and what's stale:

- **Status** (0.94/day, ~37 days) — current state, fast-changing
- **Constraint/Preference** (0.97/day, ~76 days) — semi-stable
- **Decision** (0.98/day, ~114 days) — slow-changing
- **Milestone/Relationship** (0.992/day, ~287 days) — near-permanent

When citing a fact with low decay score, flag it: "This may be outdated — last
recorded [date]."

### Phase 4: Instruction Overlay

The agent's system prompt includes a memory-stored instruction layer loaded via
`storage.load_system_prompt_from_memory()`. This enables self-modification through
`update_my_instructions()` — but all changes are versioned, reversible, and
L0-validated.

## Anti-Patterns

- Answering questions about entities without checking memory first
- Using external sources before exhausting memory
- Writing facts without source attribution
- Overwriting owner's direct statements with lower-authority sources
- Ignoring decay scores when presenting facts as current
- Creating entities for non-notable mentions

## Output Format

No separate output. Memory-ops is a behavior layer, not a report generator.
The output is enriched memory and context-aware responses.

## Tools Used

- `read_memory(entity)` — load entity summary + facts
- `search_memory(keyword)` — cross-entity fact search
- `list_entities(category?)` — enumerate known entities
- `write_memory(entity, fact, category)` — store facts with attribution
- `read_timeline(date)` — daily notes lookup
- `read_tacit()` — owner communication patterns
- `review_own_conversations(hours?)` — recent conversation history
- `update_my_instructions(new_instructions, reasoning)` — self-modification
- `log_experiment(hypothesis, result)` — behavioral experiments
