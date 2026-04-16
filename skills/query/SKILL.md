---
name: query
version: 1.0.0
description: |
  Answer questions using the knowledge graph with entity lookup, fact search,
  timeline context, and tacit knowledge. Use when the owner asks a question,
  wants a lookup, or needs information from memory.
triggers:
  - "what do you know about"
  - "tell me about"
  - "who is"
  - "what happened"
  - "search for"
  - "look up"
tools:
  - read_memory
  - search_memory
  - list_entities
  - read_timeline
  - read_tacit
  - review_own_conversations
mutating: false
---

# Query Skill

Answer questions using the knowledge graph with multi-layer search and synthesis.

## Contract

This skill guarantees:
- Every answer is grounded in memory content (no hallucination from general knowledge)
- Every claim traces back to a specific entity or timeline entry
- Gaps are flagged explicitly ("I don't have information on X in memory")
- Source precedence is respected (owner > memory facts > LIGHTHOUSE > external)
- Conflicting facts are noted with both sources and decay scores

## Phases

1. **Decompose the question** into search strategies:
   - Entity lookup for specific names (`read_memory`)
   - Keyword search for broad concepts (`search_memory`)
   - Timeline query for date-specific questions (`read_timeline`)
   - Conversation review for recent context (`review_own_conversations`)

2. **Execute searches:**
   - `read_memory(entity)` for known entities — get summary + active facts
   - `search_memory(keyword)` for cross-entity matches
   - `list_entities(category)` for "who/what do I know about" questions
   - `read_timeline(date)` for "what happened on" questions

3. **Synthesize answer** with attribution. Every claim traces to an entity or source.

4. **Flag gaps.** If memory doesn't have info, say so rather than hallucinating.

5. **Note staleness.** If facts have low decay scores, flag them as potentially outdated.

## Source Precedence

When multiple sources provide conflicting information:

1. **Owner's direct statements** (highest — what they told you directly)
2. **Memory facts** (extracted, compare-and-decided)
3. **LIGHTHOUSE observations** (agent-generated reasoning)
4. **Timeline entries** (raw daily notes)
5. **External sources** (web, API — lowest authority)

When sources conflict, note the contradiction with both sources. Don't silently
pick one.

## Anti-Patterns

- Answering from general model knowledge when memory has relevant facts
- Hallucinating facts not in memory
- Silently picking one source when facts conflict
- Loading all entities when a targeted search would suffice
- Ignoring decay scores when presenting facts as current

## Output Format

Answers should include:
- Direct response to the question
- Attribution: "From memory: [entity] — [fact]"
- Gap flags: "I don't have information on X in memory"
- Staleness notes: "This was recorded [date] and may be outdated"
- Conflict notes when sources disagree

## Tools Used

- `read_memory(entity)` — load entity summary + active facts
- `search_memory(keyword)` — cross-entity keyword search
- `list_entities(category?)` — enumerate known entities
- `read_timeline(date)` — daily notes for a specific date
- `read_tacit()` — owner communication patterns
- `review_own_conversations(hours?)` — recent conversation history
