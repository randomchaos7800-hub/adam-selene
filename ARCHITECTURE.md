# Adam Selene Architecture

## System Overview

Adam Selene is a stateless relay architecture. The relay itself holds no state — everything persists in files and SQLite. This means the agent survives crashes, restarts, and model swaps without losing context.

## Message Flow

```
┌─────────────┐     ┌─────────────┐     ┌──────────────┐
│  Interface   │────>│    Relay     │────>│  Switchboard  │
│ (Telegram/   │     │ (relay.py)  │     │              │
│  Slack/IRC)  │     │             │     │ OpenRouter    │
└─────────────┘     │  Tool Loop  │<────│ or local      │
       ^            │  (max 40)   │     │ llama.cpp     │
       │            └──────┬──────┘     └──────────────┘
       │                   │
       │            ┌──────v──────┐
       └────────────│   Response   │
                    └──────┬──────┘
                           │
                    ┌──────v──────┐
                    │  Extraction  │ (background)
                    └─────────────┘
```

### 1. Interface Layer

Protocol adapters that translate platform messages into relay calls:

- **telegram.py** — Telegram Bot API (long polling)
- **slack_interface.py** — Slack Bolt (Socket Mode, bidirectional)
- **irc_client.py** — IRC bot (channel-based)

Each interface handles auth, message formatting, and chunking. They all call `relay.respond(message, user_id, interface)`.

### 2. Relay (relay.py)

The core router. On each message:

1. Load today's conversation from SessionStore (SQLite)
2. Build system prompt (base prompt + tool summary)
3. Call Switchboard for model inference
4. If response contains tool calls → execute each → append results → recurse (max 40 depth)
5. If response is end_turn → return text to interface
6. Save exchange to sessions.db
7. Trigger extraction if threshold met

### 3. Switchboard (switchboard.py)

Multi-model routing layer:

- Translates Anthropic message/tool format → OpenAI-compatible format
- Routes to OpenRouter (primary) or local llama.cpp (fallback)
- Duck-types responses so relay.py needs zero changes when switching models
- Handles context window math (128K remote, 32K local)

### 4. Tool System (tools.py)

38+ tools dispatched by name via `execute_tool()`. Categories:

- **Memory** — CRUD on knowledge graph (entities, facts, timeline, tacit)
- **LIGHTHOUSE** — Read/write reasoning journal entries
- **Tasks** — Simple task tracking
- **Browser** — Firecrawl-powered page scraping + screenshots + interaction
- **GitHub** — Repo creation, file management, branch operations
- **IRC** — Channel messaging, log search, channel management
- **Shell** — Guarded command execution (regex blocklist for dangerous patterns)
- **Filesystem** — Read/write/search within agent directory
- **Config** — Runtime configuration changes
- **Self** — Self-modification with L0 constraint checking

## Memory System

### Knowledge Graph (storage.py)

```
~/adam-selene-memory/
├── entities.json                 # Master registry
├── life/areas/
│   └── {category}/{entity}/
│       ├── summary.md            # Current state (rewritten weekly)
│       └── facts.json            # Atomic facts with metadata
├── MEMORY.md                     # Tacit knowledge
└── notes/YYYY-MM-DD.md           # Daily timeline
```

Facts have a V2 schema with categories, decay scores, and supersession tracking.

### Extraction Pipeline (extraction.py)

Two-stage Mem0-inspired pipeline:

1. **Stage 1:** LLM extracts raw facts from the owner's messages only
2. **Stage 2:** Each fact compared against existing memory → ADD / UPDATE / NONE

This prevents duplicate facts and handles contradictions gracefully.

### Consolidation (consolidation.py)

Nightly "REM sleep" pass with four phases:

1. **Replay** — Cross-layer signal detection (memory + LIGHTHOUSE + working memory)
2. **Decay** — Exponential scoring per category:
   - Status: 0.94/day (~37 days to archive threshold)
   - Constraint/Preference: 0.97/day (~76 days)
   - Decision: 0.98/day (~114 days)
   - Milestone/Relationship: 0.992/day (~287 days)
3. **Patterns** — LLM detects cross-cutting insights, promotes to MEMORY.md
4. **Contradictions** — Finds and resolves mutually exclusive facts

### Synthesis (synthesis.py)

Weekly (Sunday) rewrite of entity summaries from accumulated facts. Keeps summaries current-state focused, under 150 words.

## LIGHTHOUSE System

A reasoning journal — not facts about the world, but facts about how the agent thinks.

**Sections:** reasoning, corrections, conversations, patterns, tools, map, identity, archive

**Write triggers:**
- Agent catches itself in a reasoning error → corrections
- Owner corrects the agent → corrections (with WHY)
- Pattern noticed in owner's behavior → patterns
- Decision chain worth preserving → reasoning

**Nightly extraction:** Script reads 24h of conversations, LLM extracts entries.

## Heartbeat System (heartbeat.py)

Two-phase idle reflection:

- **Phase 1 (15 min idle):** Reflect on recent conversation → log observations to LIGHTHOUSE
- **Phase 2 (30+ min idle):** Research an agenda item → push to owner if quality score ≥ 4/5
- Rate limited: max 1 proactive push per 4 hours

### Working Memory (working_memory.py)

Single active research thread with multi-step investigation:
- Tracks goal, steps, findings, cycle count
- Auto-abandons stale threads (>2h without heartbeat)
- Archives last 20 completed threads

## Constitution System

### L0 Constraints (l0_constraints.json)

Six foundational values enforced as guardrails:
1. **Honor** — Never invent, truth > convenience
2. **Loyalty** — Owner's interests first
3. **Promises** — Explicit > implicit
4. **Autonomy** — Don't create dependency
5. **Systems** — Design > willpower
6. **Truth** — Report actual data

### Hash Verification (constitution.py)

Constitution file (L0.md) is SHA256-hashed on creation. Hash checked on every startup — raises `ConstitutionTamperError` if mismatch.

### L0 Validator (l0_validator.py)

Soft gate on self-modifications. Checks proposed changes for red flags ("bypass L0", "hide from owner", etc.). Real safety is the owner's ability to review the experiment log and revert.

## Session Management

### Session Store (sessions.py)

SQLite-backed conversation persistence:
- Every exchange saved with user_id, role, content, timestamp
- Smart stratified sampling when context exceeds token budget
- Three time horizons: Immediate (today), Recent (queryable), Long-term (extracted)

### Session Logging (session_log.py)

JSONL audit trail per session:
- Events: user_message, model_call, tool_call, tool_result, error
- Replay via `scripts/replay_session.py`
- Cost tracking per model call

## Configuration

All behavior driven by `config/settings.json`:
- Model selection (main, extraction, heartbeat, local)
- Context tokens and fill ratios
- Extraction timing (idle timeout, incremental frequency)
- Synthesis schedule and archival policy
- Heartbeat timing and enablement
- Interface selection

Generated by `setup_wizard.py` or edited manually.
