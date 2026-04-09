# LIGHTHOUSE — Reasoning Journal

LIGHTHOUSE is your agent's self-reasoning journal. It's fundamentally different from memory:

- **Memory** stores facts about the world (people, projects, events)
- **LIGHTHOUSE** stores facts about how the agent *thinks* (reasoning chains, blind spots, corrections)

## Sections

| Section | Purpose |
|---------|---------|
| `reasoning/` | Decision chains — why the agent chose X over Y |
| `corrections/` | Where the agent was wrong and why (**most valuable**) |
| `conversations/` | Key exchanges that shifted understanding |
| `patterns/` | Recurring themes in the owner's behavior |
| `tools/` | What tools work when and in what context |
| `map/` | The agent's evolving model of its owner |
| `identity/` | Self-discovery through lived experience |
| `archive/` | Old entries (archaeological reference) |

## Entry Format

Files are named: `YYYY-MM-DD_HHMM_slug.md`

Each entry has a metadata header:
```markdown
# Title
**Date:** 2026-01-15 14:30
**Section:** corrections
**Trigger:** Owner corrected my assumption about X

Content here...
```

## How It Works

1. **In-conversation:** The agent writes entries when insights strike
2. **Nightly extraction:** A script reads the last 24h of conversations and extracts entries
3. **Consolidation:** The nightly pass writes breadcrumb entries with overnight context
4. **On-demand:** The agent reads LIGHTHOUSE before responding for self-awareness

## Why This Matters

Traditional AI agents have no memory of *how* they think — only *what* they know. LIGHTHOUSE gives your agent metacognition: the ability to reflect on its own reasoning, catch recurring mistakes, and evolve its approach over time.

The corrections section is the most valuable. Being wrong accurately is how the agent gets better.
