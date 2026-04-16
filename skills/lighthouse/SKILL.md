---
name: lighthouse
version: 1.0.0
description: |
  Self-reflection and reasoning journal. Write observations about your own thinking,
  corrections from the owner, patterns noticed, and identity evolution. The LIGHTHOUSE
  is not facts about the world — it's facts about how you think.
triggers:
  - "write to lighthouse"
  - reasoning observation
  - self-correction
  - owner correction
  - pattern noticed
  - identity reflection
tools:
  - lighthouse_write
  - lighthouse_read
  - lighthouse_search
  - lighthouse_living
mutating: true
---

# LIGHTHOUSE Skill — Self-Reflection Journal

The LIGHTHOUSE is not memory. Memory stores facts about the world. LIGHTHOUSE stores
facts about how you think, where you go wrong, and what you're learning about yourself.

## Contract

This skill guarantees:
- Observations are written immediately when noticed (not batched)
- Owner corrections include the WHY, not just the correction
- Patterns are cross-referenced with previous observations
- The living document reflects current self-understanding
- Nightly consolidation extracts insights from conversations

## Sections

| Section | Purpose | Write When |
|---------|---------|------------|
| **reasoning** | Decision chains worth preserving | Complex multi-step reasoning completed |
| **corrections** | Errors caught (self or owner) | You made a mistake or owner corrects you |
| **conversations** | Notable exchange patterns | Conversation had unusual dynamics |
| **patterns** | Recurring owner behaviors/preferences | Pattern noticed across multiple interactions |
| **tools** | Tool usage insights | Tool used in unexpected way, or tool failure pattern |
| **map** | Mental model of the system/environment | New understanding of infrastructure or process |
| **identity** | Self-observations, living document | Insight about own behavior or capabilities |
| **archive** | Aged-out entries from other sections | Nightly consolidation moves old entries here |

## Phases

### Phase 1: Detection

Recognize when a LIGHTHOUSE entry is warranted:
- You caught yourself in a reasoning error → **corrections**
- Owner said "no, because..." → **corrections** (capture the WHY)
- You solved something in an unexpected way → **reasoning**
- You notice the owner always does X before Y → **patterns**
- A tool worked differently than expected → **tools**
- You realize something about your own behavior → **identity**

### Phase 2: Write

```
lighthouse_write(
    section="corrections",
    title="[Pending] Assumed X was Y without checking",
    content="**What happened:** ...\n**Why it was wrong:** ...\n**What to do instead:** ..."
)
```

For corrections, always include:
- What happened (the error)
- Why it was wrong (the reasoning gap)
- What to do instead (the fix)

### Phase 3: Living Document

`lighthouse_living(content)` updates the identity living document — your evolving
self-understanding. This should be updated when:
- A significant self-observation crystallizes
- Multiple corrections point to a deeper pattern
- Your capabilities or constraints change

### Phase 4: Search Before Write

Before creating a new entry, `lighthouse_search(query)` to check if you've already
observed this pattern. If so, update or reference the existing entry rather than
creating a duplicate.

## Anti-Patterns

- Writing vague observations ("I should be better at X")
- Logging corrections without the WHY
- Dumping raw conversation excerpts without analysis
- Writing to LIGHTHOUSE what belongs in memory (facts about the world)
- Never reading LIGHTHOUSE entries back (write-only journal is useless)

## Output Format

Entries should be actionable and specific:
- Title: `[Pending]` prefix for items needing follow-up
- Content: structured with **What/Why/Fix** for corrections
- Tags: relevant keywords for search

## Tools Used

- `lighthouse_write(section, title, content)` — create entry
- `lighthouse_read(section?, limit?)` — read recent entries
- `lighthouse_search(query)` — search across all sections
- `lighthouse_living(content)` — update living document
