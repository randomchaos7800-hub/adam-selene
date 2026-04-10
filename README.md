# Adam Selene

A personal AI agent framework with persistent memory, self-reasoning, and constitutional constraints. Built for people who want an AI that actually knows them.

Adam Selene isn't a chatbot. It's an always-on reasoning partner that remembers your life, reflects on its own thinking, and gets better over time — without losing what makes it yours.

## What Makes This Different

- **Persistent Memory** — Knowledge graph with entities, facts, timeline, and tacit knowledge. Your agent remembers across sessions.
- **LIGHTHOUSE** — A reasoning journal where the agent tracks its own blind spots, corrections, and evolving understanding of you. Not just what it knows — how it thinks.
- **Constitutional Constraints** — Six foundational values (L0) that can't be overridden. Hash-verified on every startup.
- **Heartbeat** — When idle, the agent reflects on recent conversations and researches topics from its agenda. Like REM sleep for AI.
- **Two-Stage Extraction** — Facts are extracted from conversations, then deduplicated against existing memory (Mem0-inspired). No duplicate noise.
- **Nightly Consolidation** — Exponential decay scoring, contradiction resolution, and pattern detection. Memory stays fresh without manual pruning.
- **Self-Modification** — The agent can update its own prompt and behavior, with full version control and L0 constraint checking.
- **Multi-Interface** — Telegram, Slack, and IRC out of the box. Pick one or run all three.
- **58 Tools** — Memory, LIGHTHOUSE, GitHub, browser (Firecrawl), shell, filesystem, IRC, tasks, research, vault, and more.

## Quickstart

```bash
# Clone
git clone https://github.com/randomchaos7800-hub/adam-selene.git
cd adam-selene

# Install dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run the setup wizard
python setup_wizard.py

# Start your agent (pick your interface)
python -m interfaces.telegram
python -m interfaces.slack_interface
python -m interfaces.irc_client
```

The setup wizard asks for your agent's name, personality, values, and API keys — then generates all config files automatically.

## Requirements

- Python 3.10+
- [OpenRouter](https://openrouter.ai/) API key (required for inference)
- Telegram Bot Token, Slack App, or IRC — at least one interface
- Optional: [Firecrawl](https://firecrawl.dev/) API key (for browser tools), GitHub token

## Architecture

```
User Message (Telegram / Slack / IRC)
    |
    v
Interface Handler (auth, session start)
    |
    v
Relay (relay.py) — core message router
    |
    v
Switchboard — routes to OpenRouter or local llama.cpp
    |
    v
Model Response
    |
    +--[tool_use]--> Tool Dispatcher (58 tools)
    |                    |
    |                    v
    |               Execute & recurse (max 40 depth)
    |
    +--[end_turn]--> Response to user
    |
    v
Extraction Pipeline (background)
    |
    v
Memory (knowledge graph, timeline, tacit)
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full system design.

## Memory System

```
~/adam-selene-memory/
├── entities.json          # Master entity registry
├── MEMORY.md              # Tacit knowledge (how your owner thinks)
├── life/areas/            # Knowledge graph
│   ├── people/            #   Each entity: summary.md + facts.json
│   ├── projects/
│   ├── companies/
│   └── concepts/
├── notes/                 # Daily timeline (YYYY-MM-DD.md)
├── sessions.db            # SQLite conversation persistence
├── working_memory.json    # Active research threads
├── agenda.json            # Research topic queue
├── consolidation/         # Nightly pass reports
├── snapshots/             # Conversation snapshots
└── sessions/              # JSONL audit trails
```

Facts have categories (status, milestone, constraint, preference, relationship, decision) and decay scores. Milestones last ~287 days; status facts decay in ~37 days. Memory stays relevant without manual cleanup.

## Performance

Benchmarked on [LongMemEval](https://github.com/xiaowu0162/LongMemEval) with Gemma 4 26B via OpenRouter. Context-window mode only — no extraction pipeline active. This is the floor, not the ceiling.

| Task | Score |
|------|-------|
| Single-session recall | 88-89% |
| Temporal reasoning | 73% |
| Knowledge update | 70% |
| Multi-session aggregation | 54% |
| **Overall** | **~75%** |

For comparison: Supermemory (commercial, $99/month) scores 85.4%. Mem0 (GPT-4o) scores 67.6%. Full analysis in [GUIDE.md Chapter 9](GUIDE.md#chapter-9).

Hardware: $500 mini PC. Benchmark cost: $5 on OpenRouter.

## Configuration

All behavior is driven by `config/settings.json`:

| Setting | What It Controls |
|---------|-----------------|
| `models.main` | Primary inference model |
| `heartbeat.idle_minutes` | How long before idle reflection kicks in |
| `extraction.incremental_every_n_messages` | Extract facts every N messages |
| `synthesis.day_of_week` | Weekly summary rewrite day |
| `synthesis.archive_after_days` | Archive old facts after N days |

## Tools

See [TOOLS.md](TOOLS.md) for the complete tool reference. Categories:

- **Memory** — read, search, write, timeline, tacit knowledge
- **LIGHTHOUSE** — read, write, search reasoning journal
- **Tasks** — read, add, complete
- **Browser** — browse URLs, screenshots, interactive browsing
- **GitHub** — create repos, push files, read content
- **IRC** — send messages, search logs, manage channels
- **Shell** — guarded command execution with security blocklist
- **Filesystem** — read, write, search files within the agent directory
- **Config** — read settings, change models, restart service

## Running as a Service

Create a systemd user service:

```ini
# ~/.config/systemd/user/adam-selene.service
[Unit]
Description=Adam Selene
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/path/to/adam-selene
ExecStart=/path/to/adam-selene/venv/bin/python -m interfaces.telegram
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now adam-selene.service
```

## Nightly Jobs

Set up cron for memory maintenance:

```bash
# Memory consolidation (3 AM)
0 3 * * * cd /path/to/adam-selene && venv/bin/python scripts/consolidation_nightly.py

# LIGHTHOUSE extraction (2 AM)
0 2 * * * cd /path/to/adam-selene && venv/bin/python scripts/lighthouse_nightly.py
```

## Known Limitations

**Vault secrets in public channels.** ~~The vault tools (`vault_get`, `vault_set`) return plaintext secrets.~~ **Partially fixed:** `vault_get` and `read_credential` now return masked values (e.g., `sk-a...xxxx`) instead of raw secrets. `vault_set` passes secrets via stdin (not CLI args) to prevent `/proc/cmdline` exposure. Session logs redact sensitive tool inputs. Shell blocklist hardened against base64/eval/command-substitution bypasses. The tool dispatcher now enforces owner identity checks on all privileged tools (vault, credentials, shell, self-modification, git). Non-owner users receive "Permission denied" and the attempt is logged with user_id and interface.

**File locking on concurrent writes.** The architecture is designed so only one brain writes at a time (day brain or night brain). However, the extraction pipeline runs as a background daemon thread during conversation, meaning the relay and extraction could theoretically write to the same `facts.json` simultaneously. In practice this hasn't caused corruption because writes are small and infrequent, but a proper `fcntl` lock on write operations would close the gap.

**Stale working memory threshold.** Working memory auto-abandons threads after 2 hours without a heartbeat. If your heartbeat interval is set high (e.g., 45 minutes), that's only ~2.5 cycles — potentially too aggressive. The threshold should be configurable or derived from the heartbeat interval.

**No human-in-the-loop permission prompts.** This is a design choice, not a gap. The architecture trusts the operator and uses the L0 constitution as the guardrail instead of interactive permission prompts. The tradeoff: lower friction for the owner, but the agent acts autonomously within its constitutional constraints. If you need approval gates, add them in the tool dispatcher.

## License

MIT. See [LICENSE](LICENSE).

## Credits

Built by [Vitale Dynamics](https://dinovitale.com). Extracted from a production agent that's been running daily since January 2026. Benchmarked against the field.

If you build something cool with Adam Selene, tell us about it.
