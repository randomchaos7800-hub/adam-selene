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

### 4. Tool System (tools.py + tool_registry.py)

63 tools dispatched via `execute_tool()`. Two coexisting registration paths:

- **Static (61)** — a `TOOL_DEFINITIONS` list + an if/elif dispatcher, both in `relay/tools.py`. (The README/ARCHITECTURE/GUIDE docs previously said "58" here — that was already stale before this pass; the static list had grown to 60 without anyone updating the hardcoded count in four different places. Worth treating any hardcoded tool count in prose as a snapshot, not a guarantee — `all_tool_definitions()` is the actual source of truth.)
- **Registry-based (new domains)** — a tool domain module (`relay/tool_domains/*.py`) calls `REGISTRY.register(ToolEntry(...))` at import time; `relay/tool_registry.py`'s `ToolRegistry` holds these separately. `tools.py`'s `all_tool_definitions()` combines both lists — `relay.py` uses this, not the static list directly — and `execute_tool()`'s dispatch falls through to `REGISTRY.dispatch()` for any name its own if/elif chain doesn't recognize. New tool domains register this way instead of requiring a hand-edit of the monolith; nothing existing needed migrating for this to work.

Categories:

- **Memory** — CRUD on knowledge graph (entities, facts, timeline, tacit), plus bi-temporal history (`read_memory_history`, registry-based)
- **Skills** — `skill_manage` (registry-based) — the agent authors, patches, and retires its own procedural-memory files
- **LIGHTHOUSE** — Read/write reasoning journal entries
- **Tasks** — Simple task tracking
- **Browser** — Firecrawl-powered page scraping + screenshots + interaction
- **GitHub** — Repo creation, file management, branch operations
- **IRC** — Channel messaging, log search, channel management
- **Shell** — Guarded command execution (regex blocklist for dangerous patterns)
- **Filesystem** — Read/write/search within agent directory
- **Config** — Runtime configuration changes
- **Self** — Self-modification with L0 constraint checking
- **Introspection** — `list_capabilities` — what's reachable on the current channel

### 5. Self-Learning Skills System

Beyond the hand-authored skills in `skills/`, the agent can author its own procedural memory at runtime:

- **`skill_manage`** (`relay/tool_domains/skills_mgmt.py`) — `create`/`patch`/`archive` actions. `create` validates name format, description/content length, 2-12 triggers (rejects generic, too-short, or already-claimed-elsewhere triggers — routing is substring match, so a bad trigger degrades every message, not just this skill), and declared tools against a denylist (shell/code-edit/config/vault/service-restart tools can never be self-granted). Content is also screened for suspicious persistence/authority-claim phrasing (`_find_persistence_markers()`) — a fake `SYSTEM:`/`ADMIN:` prefix, "must survive", "cannot be removed", "ignore previous instructions" — the same content-poisoning pattern a newly-documented attack class targets specifically toward content designed to persist and get re-injected across turns, which a self-created skill file inherently is; `patch` gets the same screen on `new_str`. A hard cap on self-created skills (`skills.max_self_created` in settings, default 15) is enforced before every create. `patch`/`archive` only ever touch skills this tool itself created (tracked via a `created_by` frontmatter field) — hand-authored skills are read-only to it. `archive` moves to `skills/.archive/`, never deletes.
- **Standing nudge** (`relay/skill_resolver.py`'s `SELF_LEARNING_COMPACT`) — a fixed, always-appended prompt block reminding the agent `skill_manage` exists, so self-learning isn't purely reactive to being asked.
- **Usage telemetry** (`relay/skill_resolver.py`'s `_bump_usage()`) — best-effort sidecar (`skills/.usage.json`, gitignored) tracking `use_count`/`last_used` per non-always-on skill every time it's routed to.
- **Nightly curator** (`scripts/skill_curator.py`) — deterministic, no LLM call, imports nothing from the app package (runs independent of whether the main service is even up). `active → stale` after 30 days unused, `stale → archived` after 90 (both configurable via CLI flags). A skill with `pinned: true` is exempt. `--restart-service-on-change` is opt-in — the running process caches `manifest.json` in memory, so an external archive needs a restart to take effect, but auto-restarting a production agent from cron isn't a safe default.
- **`learn` skill** (`skills/learn/SKILL.md`) — the user-triggered half ("learn this", "save this as a skill"): distill what actually happened into a fixed section template (When to Use / Procedure / Pitfalls / Verification) and call `skill_manage(action='create', ...)`.

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

Facts have a V2 schema with categories, decay scores, and supersession tracking (`status`/`supersededBy`), plus two additive fields:

- **`provenance`** (`owner_stated` / `agent_inferred` / `tool_derived`) — trust tier of a fact's origin. Matters concretely: `relay/irc_memory.py`'s `extract_irc_learnings()` runs the exact same extraction pipeline as owner-conversation extraction, over public IRC channel content from arbitrary third parties. Before this field existed, nothing distinguished a fact the owner actually said from one synthesized out of an untrusted stranger's IRC message. `storage.is_trusted_provenance()` gives consumers a cheap check without hardcoding the taxonomy. Missing on facts written before this field existed — treated as trusted (no retroactive downgrade).
- **`valid_from`/`valid_to`** — a bi-temporal pair alongside `timestamp` (which records when a fact was *recorded*, not necessarily when it became *true* — these can differ). `valid_to` starts null and gets stamped by `supersede_fact()` when a fact is replaced. `storage.facts_valid_at(entity, at_time)` reconstructs what was believed true about an entity as of a past date, including facts that have since been superseded — something `read_entity()`'s active-only filter can't do at all, since a superseded fact just disappears from it. Exposed to the model as `read_memory_history` (registry-based tool).

### Extraction Pipeline (extraction.py)

Two-stage Mem0-inspired pipeline:

1. **Stage 1:** LLM extracts raw facts from the owner's messages only
2. **Stage 2:** Each fact compared against existing memory → ADD / UPDATE / NONE
3. **Verification backstop** (`_verify_decision()`): a deterministic, non-LLM sanity check on Stage 2's output before it's trusted. Mem0 — the pattern this pipeline is explicitly modeled on — reports only 18% accuracy on FactConsolidation despite having this exact LLM-resolver mechanism; the specific weak point is LLM-judged dedup/conflict-resolution, so the parts that don't need judgment get re-derived in plain code instead. `NONE` verdicts are checked against the entity's existing facts via the same `SequenceMatcher` similarity `heartbeat.py`'s own compaction already uses — if nothing existing is actually close, downgrades to `ADD` rather than silently losing a fact that isn't really a duplicate. `UPDATE` verdicts are checked that the referenced `supersedes_id` actually exists — previously a hallucinated fact_id failed silently inside `supersede_fact()`; now it's logged and downgrades to `ADD`. Both failure modes downgrade to `ADD`, never `NONE` — the safer direction when uncertain is an extra fact, not a lost one.

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

**Correction dedup:** `heartbeat.py`'s `_is_duplicate_correction()` checks corrections modified in the last 4 hours before writing a new `[Pending]` one — either a >60% word-overlap with the candidate's filename slug, or the new suggestion appearing verbatim in the candidate's first 200 chars, skips the write. Addresses a real observed problem: periodic reflection cycles re-writing essentially the same observation every tick, filling the journal with near-duplicates. Deterministic, no LLM call.

## Heartbeat System (heartbeat.py)

Two-phase idle reflection:

- **Phase 1 (15 min idle):** Reflect on recent conversation → log observations to LIGHTHOUSE
- **Phase 2 (30+ min idle):** Research an agenda item → push to owner if quality score ≥ 4/5
- Rate limited: max 1 proactive push per 4 hours
- **Phase 3 (once per calendar day, not idle-gated):** relationship pulse — notices a `people`-category entity with no new facts in `relationship_pulse.stale_after_days` (default 21), logs a LIGHTHOUSE `patterns` entry, and — same rate-limited push mechanism as Phase 2 — surfaces a gentle nudge. Won't re-flag the same person more often than `cooldown_days` (default 14). Inspired by Inflection AI's Pi Journeys (memory that "grows with you," built around specific relationships, proactively resurfacing them) — adapted as an extension of this framework's own existing idle-reflection frame, not an attempt to reproduce Pi's (largely undisclosed) actual architecture. Disable via `relationship_pulse.enabled: false`.
- Tier 0/1 memory compaction (exact + near-duplicate dedup, `SequenceMatcher` ratio > 0.95) runs every tick before reflection. The near-dup pass is O(n²); capped at 200 active facts per entity — an entity past the cap skips just that pass (logged), Tier 0 exact-dup dedup still runs and applies regardless.

### Working Memory (working_memory.py)

Single active research thread with multi-step investigation:
- Tracks goal, steps, findings, cycle count
- Auto-abandons stale threads (>2h without heartbeat)
- Archives last 20 completed threads

## Goal Loop (goal_loop.py)

**Off by default** (`settings.json` → `goals.enabled`). The framework's most autonomous mode — read the module's docstring and the README's Known Limitations section before enabling it.

Single active goal at a time (same single-active-thread pattern as Working Memory above). `/goal start <text>` begins it; each turn re-injects a continuation prompt through the exact same `relay.respond()` path an owner message takes, so every step lands in ordinary conversation history and every existing per-turn tool gate (capability gating, `PRIVILEGED_TOOLS`, `L0_GUARDED_TOOLS`) still applies exactly as it would in a normal conversation — nothing about this mechanism bypasses those checks. A separately-prompted, cheap switchboard call judges done-vs-continue after each turn; it **fails open** (keeps going) on any error or unparseable response — `max_turns` (default 10, configurable via `goals.max_turns`) is the actual hard backstop, not the judge. `/goal status|pause|resume|stop` controls it live.

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

## Security

### Vault & Credentials

Secrets are stored in an age-encrypted vault (`~/.vault/secrets.age`). The agent accesses them via `vault_get` and `vault_set` tools.

**Hardening measures:**
- `vault_set` passes secret values via **stdin** (not CLI args) to prevent exposure in `/proc/*/cmdline`
- `vault_get` returns **masked values** (e.g., `sk-a...xxxx`) to the model context — the full value is never in conversation history or session logs
- `read_credential` returns **key names only** with masked previews, not raw credential values
- Credential directories are locked to `0o700`; credential files to `0o600`
- Vault key names are validated (alphanumeric + underscore/hyphen, max 128 chars)

### Session Log Redaction

`log_tool_call` redacts sensitive fields (`value`, `data`, `credentials`, `api_key`, `token`, `secret`, `password`) for vault and credential tools. The JSONL audit trail records `[REDACTED]` instead of actual secret values.

### Shell Sandbox (shell_tool.py)

`run_shell` executes inside a **bubblewrap (bwrap)** sandbox — OS-level isolation, not just pattern matching. The regex blocklist below still runs first (cheap, catches the lazy cases before a subprocess even spawns), but it was never a real security boundary on its own — `rm -fr` vs `rm -rf`, `find . -delete`, or any tool that doesn't route through this blocklist at all trivially gets past pattern matching. The sandbox is the actual control:

- **`--ro-bind / /`** — the whole host filesystem is read-only. The agent can still read/execute anything on the system, but can't tamper with it.
- **`--tmpfs $HOME` + restore project_root/memory_root read-write** — home is blanked, then only the agent's own project and memory directories are bound back writable. This is an **allowlist** of what's writable, not a denylist of what's hidden — a denylist approach (hide known-secret paths, leave everything else visible) was tried first in a sibling deployment of this framework and an adversarial review found it missed `~/.netrc`, `~/.config/rclone`, other credential files, and browser profiles. Don't repeat that mistake if you extend this.
- **Explicit secret-path masks** (`--ro-bind /dev/null <path>`) — `config/secrets.env` (this framework's own live API keys/tokens) and common credential locations (`~/.ssh`, `~/.aws`, `~/.netrc`, `~/.gnupg`, `~/.docker`, `~/.npmrc`, `~/.pypirc`, `~/.config/gh`, `~/.vault`, ...) stay unreadable even though they'd otherwise fall under a broader bind.
- **`--clearenv` + a small allowlist** (`PATH`, `HOME`, `USER`, `LANG`, `LC_ALL`, `TERM`, `SHELL`) — dotenv-loaded secrets (`OPENROUTER_API_KEY`, `TELEGRAM_BOT_TOKEN`, ...) live in `os.environ` by the time `run_shell` executes; bwrap inherits the parent environment by default unless cleared, so without this, `echo $SOME_KEY` or reading `/proc/self/environ` inside the sandbox would leak everything regardless of file-level masking.
- **`--tmpfs /tmp`** — private, isolated scratch space, wiped on exit.
- **Namespace unshares** (`--unshare-pid --unshare-uts --unshare-ipc --new-session --die-with-parent`) — blocks TIOCSTI terminal injection, no orphaned processes, nothing outlives the parent.

**Fails closed if bubblewrap isn't installed** — refuses to execute rather than silently falling back to unsandboxed `shell=True`. This is deliberate: a sibling deployment's first sandbox attempt *did* fall back silently on a missing dependency, and that was itself found and fixed as a real vulnerability. `settings.json`'s `shell.require_sandbox` (default `true`) is the explicit, logged opt-out for a non-Linux dev environment — bubblewrap is Linux-only, so without an opt-out `run_shell` is simply unavailable on macOS.

Regex blocklist categories (first-pass filter only):
- Destructive ops (`rm -rf`, `dd`, `mkfs`)
- Critical service disruption (`systemctl stop nginx`, `kill 1`)
- Vault/secret access (`.vault`, `vault.sh`, `secrets.age`)
- Code injection (`curl | sh`, `wget | sh`, `eval`, `base64 -d | sh`)
- Command substitution vault access (`$(... vault)`, backtick vault access)
- SSH config modification
- Force push

All shell executions (blocked and allowed) are logged to the session audit trail.

### Auth-Gating (Privileged Tools)

`execute_tool()` enforces owner identity checks on privileged tools before execution. The `PRIVILEGED_TOOLS` set includes: `vault_get`, `vault_set`, `store_credential`, `read_credential`, `write_my_code`, `edit_my_code`, `git_commit`, `run_shell`, `update_my_instructions`.

Non-owner users (e.g., IRC channel participants) receive `"Permission denied"` and the attempt is logged. Owner identity is determined by comparing `user_id` against `config.owner_user_id()`.

All interfaces pass their `interface` name (`"slack"`, `"telegram"`, `"irc"`) through the relay to the tool dispatcher for audit logging.

### Capability Gating (capabilities.py)

A second, independent gate ahead of the identity check above — answers "can this **interface** reach this tool at all", not "is this really the owner". `INTERFACE_TIERS` maps each interface to `trusted` (telegram/slack/cli — full tool surface) or `untrusted` (irc, or anything unrecognized — fails closed, not open: a new/misconfigured interface can't accidentally inherit full privileges). Untrusted interfaces get `UNTRUSTED_ALLOWED`, an explicit, narrow allowlist (memory read/write, light web reads, messaging escalation, introspection) — an allowlist, not a denylist, so a new tool added to `TOOL_DEFINITIONS` is untrusted-denied by default until someone deliberately opts it in. Denial messages are deliberately terse — they don't enumerate what exists, so a denial can't become a discovery oracle for something probing the tool surface from an untrusted channel. `execute_tool()` checks this before the identity gate.

### Tool-Call L0 Screen (l0_validator.py)

`validate_against_l0()` was originally scoped to `update_my_instructions` only. `validate_tool_call()` generalizes the same deterministic keyword red-flag screen to the other highest-risk privileged tools (`write_my_code`, `edit_my_code`, `git_commit`, `run_shell`, `vault_set`, `store_credential` — `L0_GUARDED_TOOLS` in `tools.py`) — a call whose content contains "bypass L0" or "hide from {owner}" is a red flag regardless of whether the caller already passed the identity check (a manipulated or compromised session is still "the owner" as far as that check alone goes).

This — plus capability gating above — is deliberately independent of whatever the model currently has in its context window. Current (2026) research on agentic guardrails converges on exactly this pattern: enforcement should live outside the LLM's context/reasoning, not inside a prompt instruction it might fail to apply. The specific failure mode this literature documents ("Governance Decay" — safety constraints living only in-context get silently dropped by summarization/compaction, with violation rates measured going from 0% to as high as 59% once dropped) **doesn't directly apply to this framework as built** — the system prompt is reloaded from disk fresh on every single turn (`relay.py`'s `_build_system_prompt()`) rather than compacted or summarized, so there's no compaction pass to drop constraint text from. The tool-call screen above is additional defense-in-depth on top of that existing structural mitigation, not a fix for a bug that existed here — worth knowing precisely which is which if you're evaluating this design against that research.

### SSRF / DNS-Rebinding Guard (net_guard.py)

`fetch_url` is the one tool that lets the model fetch an arbitrary caller-supplied URL — a Server-Side Request Forgery hole if unguarded (an attacker names an internal address like the cloud metadata endpoint `169.254.169.254` and the agent fetches it from inside the network on their behalf). `resolve_public()` rejects any hostname resolving to a private/loopback/link-local/reserved/multicast address before any network call happens (catches IPv4-mapped IPv6 too). `pin_host()` closes the DNS-rebinding gap where a hostname could resolve public at validation time and private by connection time, by pinning the hostname to its already-validated IP for the request's duration. `fetch_url` also passes `allow_redirects=False` — a validated public URL could still 302 into internal space.

### Fact-Check Gate (fact_check.py)

Runs on outbound text, outside the model's own reasoning — scans for file-creation claims ("I created X", "I've written Y") and verifies the claimed path exists on disk before the message reaches the owner, catching a confident-sounding fabrication that a system-prompt instruction alone can't reliably stop. Wired into both outbound paths: `telegram_sender.py`'s proactive-push path and `interfaces/telegram.py`'s ordinary reply path.

### Remaining Gaps

- **Shell regex blocklist is still just a first-pass filter** — real encoding can bypass pattern matching, same as before. The bubblewrap sandbox is the actual boundary now (see Shell Sandbox above), not the regex.
- **Bubblewrap is Linux-only.** `run_shell` fails closed (refuses to run) on macOS/other platforms without bwrap unless `shell.require_sandbox` is explicitly set to `false` — a real, deliberate capability loss on non-Linux dev machines, not an oversight.
- **The secret-mask path list (`_SECRET_MASK_PATHS` in shell_tool.py) is common-sense defaults, not exhaustive.** A credential file in an unlisted location is still readable inside the sandbox if it falls under project_root/memory_root's read-write bind. Extend the list for your own deployment's actual secret locations.
- **`/tmp` is a real, fully-writable tmpfs for the duration of the call** — anything written there doesn't touch host state and is wiped on exit, but it means a command can freely create files anywhere under `/tmp` (not just where the caller's cwd happens to be), which is expected tmpfs behavior, not a boundary violation.
- **Self-modification autonomy is a real anti-pattern to watch for, not just a hypothetical.** The `write_my_code`/`edit_my_code`/`git_commit` tools here are gated behind owner identity, the L0 tool-call screen above, and require the model to actively choose to call them mid-conversation — there's no standing background loop doing this today, and that's deliberate. A self-modification *loop* built on an earlier version of this framework was found to have produced hundreds of no-op commits and progressively corrupted its own docstrings over time, with a revert-on-failure path that didn't actually work and a whitelist bug that let it widen its own scope. It was shut off. If you build an autonomous self-mod loop on top of this framework, put a human-reviewed diff step in front of it before it commits anything — don't let an LLM-authored diff to its own source land with no gate at all.

## Configuration

All behavior driven by `config/settings.json`:
- Model selection (main, extraction, heartbeat, local)
- Context tokens and fill ratios
- Extraction timing (idle timeout, incremental frequency)
- Synthesis schedule and archival policy
- Heartbeat timing and enablement
- Interface selection

Generated by `setup_wizard.py` or edited manually.
