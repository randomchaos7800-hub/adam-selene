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
    +--[tool_use]--> Tool Dispatcher (38+ tools)
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

Facts have categories (status, milestone, constraint, preference, relationship, decision) and decay scores. Milestones last ~285 days; status facts decay in ~37 days. Memory stays relevant without manual cleanup.

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

## License

MIT. See [LICENSE](LICENSE).

## Credits

Built by [Vitale Dynamics](https://dinovitale.com). Extracted from a production agent that's been running since early 2026.

If you build something cool with Adam Selene, tell us about it.
