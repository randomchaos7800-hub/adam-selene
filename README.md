# Adam Selene

A personal AI agent framework with persistent memory, self-reasoning, and constitutional constraints. Built for people who want an AI that actually knows them.

Adam Selene isn't a chatbot. It's an always-on reasoning partner that remembers your life, reflects on its own thinking, and gets better over time — without losing what makes it yours.

## What Makes This Different

- **Persistent Memory** — Knowledge graph with entities, facts, timeline, and tacit knowledge. Your agent remembers across sessions.
- **Bi-Temporal Memory** — Facts carry a validity window (`valid_from`/`valid_to`), not just current/superseded — the agent can answer "what did I tell you about X before it changed", not just "what's true now".
- **Self-Learning Skills** — The agent can write, patch, and retire its own procedural-memory files (`skill_manage`) after a corrected mistake or a hard-won multi-step fix, so it doesn't re-derive the same thing twice. A nightly curator retires ones that stop getting used.
- **LIGHTHOUSE** — A reasoning journal where the agent tracks its own blind spots, corrections, and evolving understanding of you. Not just what it knows — how it thinks.
- **Constitutional Constraints** — Six foundational values (L0) that can't be overridden. Hash-verified on every startup, and enforced as deterministic code-level checks on privileged tool calls — not just prompt text the model has to remember to apply.
- **Interface-Based Capability Gating** — Trusted interfaces (Telegram/Slack) get the full tool surface; untrusted ones (public IRC, or anything unrecognized) get a narrow, explicit allowlist — independent of what identity the message claims.
- **Heartbeat** — When idle, the agent reflects on recent conversations and researches topics from its agenda. Like REM sleep for AI.
- **Two-Stage Extraction, Verified** — Facts are extracted from conversations, then deduplicated against existing memory (Mem0-inspired) — with a deterministic backstop that double-checks the LLM's dedup/supersession calls rather than trusting them blindly.
- **Fact-Check Gate** — Before a message reaches you, claimed file-creation ("I've created X") gets verified against the real filesystem — outside the model's own reasoning, so a confident fabrication can't slip through.
- **Nightly Consolidation** — Exponential decay scoring, contradiction resolution, and pattern detection. Memory stays fresh without manual pruning.
- **Self-Modification** — The agent can update its own prompt and behavior, with full version control and L0 constraint checking.
- **Optional Goal Loop** — Off by default. Bounded, interruptible autonomous multi-turn task execution (`/goal start ...`), for when you deliberately want unattended multi-step work — see [Known Limitations](#known-limitations) before turning it on.
- **Multi-Interface** — Telegram, Slack, and IRC out of the box. Pick one or run all three.
- **63 Tools** — Memory, LIGHTHOUSE, GitHub, browser (Firecrawl), shell, filesystem, IRC, tasks, research, vault, skills, and more.

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

The setup wizard asks for your agent's name, personality, values, and API keys — then generates all config files automatically. Secrets are written to `config/secrets.env`.

## Requirements

- Python 3.10+
- [OpenRouter](https://openrouter.ai/) API key (required for inference)
- Telegram Bot Token, Slack App, or IRC — at least one interface
- **Linux + [bubblewrap](https://github.com/containers/bubblewrap) (`bwrap`)** — required for the `run_shell` tool to work at all. It fails closed (refuses to run, doesn't silently degrade) if `bwrap` is missing, since the sandbox — not a regex blocklist — is the actual security boundary on shell execution. Most distros package it (`apt install bubblewrap` / `dnf install bubblewrap` / etc.). On macOS or another non-Linux dev machine, either run inside a Linux VM/container, or set `shell.require_sandbox: false` in `settings.json` as an explicit, understood opt-out (`run_shell` then runs unsandboxed — every such run is logged loudly).
- Optional: [Firecrawl](https://firecrawl.dev/) API key (for browser tools), GitHub token

## Model Requirements

**Commercial models via OpenRouter remain the reliable default.** The architecture — a thin, resolver-driven skill prompt plus 63 concurrent tool definitions in a multi-step agentic loop — is tuned for Claude or Gemini-class instruction following. `z-ai/glm-4.7-flash` or `anthropic/claude-haiku-4-5` via OpenRouter are the recommended backends — both cheap and reliable at this task. (The previously recommended `google/gemini-2.0-flash-001` was retired from OpenRouter and now returns 404; verified 2026-07-08.)

**Local inference is more viable than it used to be — worth re-testing.** The earlier problem here (smaller local models narrating tool calls instead of actually making them) was traced in the last 6-12 months to the *inference stack*, not model scale: llama.cpp and Ollama have since shipped native function-calling handlers with model-specific tool-call parsers, rather than relying on prompt-engineered JSON. Qwen3.6 27B in particular ships a dedicated tool-call parser and is reported competitive with frontier models on single-tool-call reliability (Terminal-Bench 2.0) while running on ~18GB VRAM. The honest caveat: this improvement is concentrated in *single-tool-call* reliability, not necessarily sustained multi-step agentic loops with state tracking across many turns — this framework's relay loop is exactly that, so treat a local backend as worth a real trial rather than an assumed drop-in, and fall back to OpenRouter if tool-call fidelity degrades under sustained multi-step use.

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
    +--[tool_use]--> Tool Dispatcher (63 tools)
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

Facts have categories (status, milestone, constraint, preference, relationship, decision), decay scores, a **provenance** tag (`owner_stated` / `agent_inferred` / `tool_derived` — trust tier of the fact's origin; extraction from public IRC channels, for instance, tags `tool_derived` rather than being trusted the same as the owner's own words), and a **bi-temporal validity window** (`valid_from`/`valid_to`) so the agent can reconstruct what it believed was true as of a past date, not just what's current. Milestones last ~287 days; status facts decay in ~37 days. Memory stays relevant without manual cleanup.

## Self-Learning Skills

```
skills/
├── manifest.json           # Registered skills (name, path, description)
├── RESOLVER.md              # Trigger → skill dispatch table
├── learn/SKILL.md           # "learn this" workflow → skill_manage(action='create')
├── .usage.json               # Runtime telemetry (gitignored) — use_count/last_used per skill
├── .archive/                 # Retired skills, never deleted (gitignored)
└── {skill-name}/SKILL.md    # Hand-authored or agent-created skill files
```

Skills are markdown workflow definitions the model reads and follows — "thin harness, fat skills" (architecture inspired by [gbrain](https://github.com/garrytan/gbrain) by Garry Tan). Beyond the hand-authored skills that ship with the framework, the agent can author its own via `skill_manage`: after a corrected mistake or a hard-won multi-step procedure, `skill_manage(action='create', ...)` saves it, so next time the same situation comes up the resolver routes straight to the procedure instead of the agent re-deriving it. `skill_manage(action='patch'|'archive', ...)` only ever touches skills the tool itself created — hand-authored skills are read-only to it — and a self-created skill can never declare shell/code-edit/config/vault/service-restart tools, no matter what its content says.

`scripts/skill_curator.py` (deterministic, no LLM call) retires self-created skills that stop getting used: `active → stale` after 30 days unused, `stale → archived` after 90 (both configurable). A skill with `pinned: true` in its frontmatter is exempt. See [TOOLS.md](TOOLS.md#skills-tools) and `relay/tool_domains/skills_mgmt.py`.

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
| `local.base_url` | Local OpenAI-compatible inference endpoint |
| `autoresearch.base_url` | Autoresearch API endpoint used by heartbeat |
| `goals.enabled` | Autonomous multi-turn `/goal` loop — off by default, see [Known Limitations](#known-limitations) |
| `goals.max_turns` | Hard turn cap for the goal loop (default 10) |
| `skills.max_self_created` | Cap on agent-authored skills via `skill_manage` (default 15) |
| `shell.require_sandbox` | `run_shell` fails closed without bubblewrap unless set to `false` (default `true`, Linux-only dep) |

## Tools

See [TOOLS.md](TOOLS.md) for the complete tool reference. Categories:

- **Memory** — read, search, write, timeline, tacit knowledge, bi-temporal history (`read_memory_history`)
- **Skills** — `skill_manage` — the agent authors, patches, and retires its own procedural-memory files
- **LIGHTHOUSE** — read, write, search reasoning journal
- **Tasks** — read, add, complete
- **Browser** — browse URLs, screenshots, interactive browsing
- **GitHub** — create repos, push files, read content
- **IRC** — send messages, search logs, manage channels
- **Shell** — guarded command execution with security blocklist
- **Filesystem** — read, write, search files within the agent directory
- **Config** — read settings, change models, restart service
- **Introspection** — `list_capabilities` — see what's reachable on the current channel

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

# Self-learning skill curation (4 AM) — deterministic, no LLM call, imports
# nothing from the app package. --restart-service-on-change is opt-in;
# without it, a running process's in-memory manifest cache won't see an
# archived skill until its next restart.
0 4 * * * cd /path/to/adam-selene && venv/bin/python scripts/skill_curator.py
```

## Known Limitations

**Vault secrets in public channels.** ~~The vault tools (`vault_get`, `vault_set`) return plaintext secrets.~~ **Partially fixed:** `vault_get` and `read_credential` now return masked values (e.g., `sk-a...xxxx`) instead of raw secrets. `vault_set` passes secrets via stdin (not CLI args) to prevent `/proc/cmdline` exposure. Session logs redact sensitive tool inputs. Shell blocklist hardened against base64/eval/command-substitution bypasses. The tool dispatcher now enforces owner identity checks on all privileged tools (vault, credentials, shell, self-modification, git) — non-owner users receive "Permission denied" and the attempt is logged with user_id and interface — **plus** an interface-level capability gate (`relay/capabilities.py`, independent of identity: an untrusted interface like public IRC can't reach these tools at all regardless of who's asking) **plus** a deterministic L0 keyword screen on the highest-risk tools' call content (`relay/l0_validator.py`'s `validate_tool_call()`), not just their identity gate.

**Shell blocklist is regex-based, still.** Sufficiently creative encoding can bypass pattern matching (`relay/shell_tool.py`). Defense-in-depth, not a security boundary — this is unchanged from before and worth knowing rather than assuming solved.

**Stale working memory threshold.** Working memory auto-abandons threads after 2 hours without a heartbeat. If your heartbeat interval is set high (e.g., 45 minutes), that's only ~2.5 cycles — potentially too aggressive. The threshold should be configurable or derived from the heartbeat interval.

**No human-in-the-loop permission prompts.** This is a design choice, not a gap. The architecture trusts the operator and uses the L0 constitution as the guardrail instead of interactive permission prompts. The tradeoff: lower friction for the owner, but the agent acts autonomously within its constitutional constraints. If you need approval gates, add them in the tool dispatcher.

**The goal loop (`/goal`) is the framework's most autonomous mode — read `relay/goal_loop.py`'s docstring before enabling it.** Off by default (`settings.json` → `goals.enabled`). Once started, the agent re-prompts itself through the normal conversation path — with full tool access — for up to `max_turns` (default 10, hard cap regardless of what the loop's own done/continue judge decides) without you approving each individual step. It's bounded and interruptible (`/goal pause|stop`), and every step lands in ordinary conversation history rather than a hidden side channel, but it is still real unattended multi-step execution. If you don't have a specific use case in mind for that, leave it off — the rest of the framework already covers most of what a personal agent needs.

**Don't let an autonomous loop edit its own source with an LLM-authored diff and no human-in-the-loop gate.** This isn't hypothetical caution — a self-modification loop built on an earlier version of this framework was found to have produced hundreds of no-op commits and progressively corrupted its own docstrings over time, with a revert-on-failure path that didn't actually work and a self-mod tool whitelist that (bug) included the ability to widen its own scope. It was shut off. The `write_my_code`/`edit_my_code`/`git_commit` tools here are gated behind owner identity, an L0 keyword screen (`relay/l0_validator.py`), and require the model to actively choose to call them mid-conversation — there's no standing background loop doing this today, and that's deliberate. If you build one, put a human-reviewed diff step in front of it before it commits anything.

## License

MIT. See [LICENSE](LICENSE).

## Credits

Built by [Boundary Labs](https://dinovitale.com). Extracted from a production agent that's been running daily since January 2026. Benchmarked against the field.

If you build something cool with Adam Selene, tell us about it.
