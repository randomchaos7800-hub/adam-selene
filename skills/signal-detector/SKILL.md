---
name: signal-detector
version: 1.0.0
description: |
  Always-on ambient capture. Fires on every inbound message to detect entities,
  facts, preferences, and original thinking from the owner. Spawn in parallel,
  never block the main response.
triggers:
  - every inbound message (always-on)
tools:
  - read_memory
  - search_memory
  - write_memory
  - log_experiment
mutating: true
---

# Signal Detector — Ambient Memory Capture

Lightweight process that fires on every inbound owner message to capture:

1. **Entity mentions** — people, projects, companies, concepts
2. **New facts** — status changes, decisions, preferences, constraints
3. **Original thinking** — the owner's ideas, observations, frameworks

The owner's words are the highest-authority data source. Capture them.

## Contract

This skill guarantees:
- Fires on every owner message (skip purely operational: "ok", "thanks", "do it")
- Runs in parallel — never blocks the main response
- Captures facts with the owner's exact phrasing where possible
- Detects entity mentions and ensures they exist in the knowledge graph
- Every fact written includes source attribution

## Phases

### Phase 1: Entity Detection

1. Scan the message for entity mentions (people, projects, companies, concepts)
2. For each entity:
   - `search_memory(name)` — does it exist?
   - If NO → is it notable enough to create? If yes, `write_memory()` with initial fact
   - If YES → is there new information? If yes, `write_memory()` with new fact

### Phase 2: Fact Extraction

For each substantive statement the owner makes:
1. Classify: status | milestone | preference | constraint | decision | relationship
2. Check for contradictions with existing facts (search first)
3. Write with proper category and source attribution

### Phase 3: Insight Capture

When the owner expresses original thinking (thesis, framework, observation):
- Capture exact phrasing — the owner's language IS the insight
- Write to the relevant entity or create a new concept entity
- Tag as source: "owner-direct"

## Anti-Patterns

- Blocking the main response to wait for signal detection
- Paraphrasing the owner's words when exact phrasing matters
- Creating entities for throwaway mentions ("the guy at the store")
- Writing facts without checking for existing contradictions first
- Running on trivial messages that carry no information

## Output Format

No visible output to the owner. This skill runs silently.
The output is facts written to memory and the extraction pipeline triggered.

## Tools Used

- `read_memory(entity)` — check if entity page exists
- `search_memory(keyword)` — find related existing facts
- `write_memory(entity, fact, category)` — store new facts
- `log_experiment(hypothesis, result)` — log behavioral observations
