---
name: research
version: 1.0.0
description: |
  Multi-step investigation using working memory threads, web research, memory
  search, and synthesis. For questions that need more than a single lookup —
  sustained inquiry across multiple sources and cycles.
triggers:
  - "research this"
  - "investigate"
  - "dig into"
  - "find out about"
  - "look into"
  - "what's the deal with"
tools:
  - read_memory
  - search_memory
  - write_memory
  - browse_url
  - fetch_url
  - screenshot_url
  - read_current_investigation
  - start_investigation
  - add_to_agenda
  - lighthouse_write
  - send_message_to_owner
mutating: true
---

# Research Skill — Multi-Step Investigation

For questions that require sustained inquiry: multiple searches, source triangulation,
web scraping, and synthesis across cycles. This is the deep-work skill.

## Contract

This skill guarantees:
- Memory is checked FIRST before any external research
- Working memory thread tracks the investigation across cycles
- Each cycle advances toward the goal with a specific query
- Findings are synthesized, not just listed
- Results above quality threshold are pushed to the owner
- Completed investigations are archived in LIGHTHOUSE

## Phases

### Phase 1: Memory-First Check

Before any external research:
1. `read_memory(entity)` — do we already know this?
2. `search_memory(keyword)` — broader sweep
3. `read_current_investigation()` — is there already an active thread on this?

If memory has a complete answer, skip external research entirely.

### Phase 2: Thread Management

- `read_current_investigation()` — check for active thread
- If no active thread → `start_investigation(goal, title)` to create one
- If active thread exists on same topic → continue it
- If active thread on different topic → note it, decide priority

### Phase 3: Research Execution (per cycle)

Each cycle follows: **Query → Search → Read → Record**

1. **Query** — formulate a specific, searchable question
2. **Search** — use the right tool for the source:
   - `search_memory(keyword)` for internal knowledge
   - `browse_url(url)` for web pages (JS-rendered via Firecrawl)
   - `fetch_url(url)` for raw HTTP (APIs, JSON endpoints)
   - `screenshot_url(url)` for visual content
3. **Read** — extract relevant information from results
4. **Record** — update working memory thread with findings

### Phase 4: Synthesis

When the goal is reached or max cycles (6) exhausted:
1. Synthesize all findings into a coherent answer
2. Write to LIGHTHOUSE (reasoning section) for future reference
3. Write key facts to memory entities via `write_memory()`
4. Push to owner if quality score ≥ 4/5

### Phase 5: Agenda Management

For research that can't be completed now:
- `add_to_agenda(topic, context, priority)` — queue for heartbeat research
- Priority 1 = urgent, 2 = normal, 3 = when idle

## Tool Pivot Rule

**A wall is not a stop sign.** When one tool fails:
- GitHub API fails → `browse_url` the repo page directly
- Memory miss → `search_memory` broader → `browse_url` → `fetch_url`
- Web search dead end → `browse_url` the site directly
- Tool returns truncated data → call again with narrower scope
- Need to check if something exists → `run_shell("ls path")`

Try at least two angles before asking the owner for help.

## Anti-Patterns

- Starting web research without checking memory first
- Single-query research on complex topics (use multiple cycles)
- Listing raw results without synthesis
- Abandoning a thread without archiving findings
- Researching what memory already knows

## Output Format

- Synthesis: 3-5 paragraphs answering the original question
- Sources cited for each major claim
- Open questions flagged for follow-up
- Key facts extracted and written to memory

## Tools Used

- `read_memory(entity)` — check existing knowledge
- `search_memory(keyword)` — cross-entity search
- `write_memory(entity, fact, category)` — store discovered facts
- `browse_url(url)` — fetch and render web pages
- `fetch_url(url)` — raw HTTP requests
- `screenshot_url(url)` — visual capture
- `read_current_investigation()` — check active research thread
- `start_investigation(goal, title)` — create new thread
- `add_to_agenda(topic, context, priority)` — queue for later
- `lighthouse_write(section, title, content)` — archive findings
- `send_message_to_owner(text)` — push high-quality results
