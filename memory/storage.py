"""Memory storage operations for Adam Selene.

Handles reading/writing to the agent's persistent memory directory.

Memory structure:
  <memory_root>/
  ├── entities.json          (master entity list)
  ├── MEMORY.md              (tacit knowledge)
  ├── life/areas/            (knowledge graph)
  │   ├── people/partner/
  │   │   ├── summary.md
  │   │   └── facts.json
  │   ├── projects/...
  │   └── concepts/...
  ├── notes/YYYY-MM-DD.md    (daily timeline)
  ├── prompts/               (versioned system prompts)
  ├── experiments/            (learning log)
  └── sessions.db            (conversation persistence)
"""

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, TypeVar

from relay.fs_utils import atomic_write_text as _atomic_write_text, exclusive_lock as _exclusive_lock

logger = logging.getLogger(__name__)
T = TypeVar("T")


def _get_memory_root() -> Path:
    """Get memory root from settings, avoiding circular imports."""
    settings_path = Path(__file__).parent.parent / "config" / "settings.json"
    if settings_path.exists():
        settings = json.loads(settings_path.read_text())
        return Path(settings.get("memory_path", "~/adam-selene-memory")).expanduser()
    return Path.home() / "adam-selene-memory"


def get_memory_path() -> Path:
    """Get the memory root path."""
    return _get_memory_root()


def _normalize_entities(data: object) -> dict:
    """Accept both the current flat schema and the legacy nested schema."""
    if isinstance(data, dict):
        nested = data.get("entities")
        if isinstance(nested, dict):
            return nested
        return data
    return {}


def _write_text_locked(path: Path, content: str) -> None:
    with _exclusive_lock(path):
        _atomic_write_text(path, content)


def _read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text())


def update_json_file(path: Path, default, updater: Callable[[dict | list], T]) -> T:
    """Update a JSON file under an exclusive lock and atomically replace it."""
    with _exclusive_lock(path):
        data = _read_json(path, default)
        result = updater(data)
        _atomic_write_text(path, json.dumps(data, indent=2))
        return result


def init_memory() -> None:
    """Initialize the memory directory structure."""
    root = get_memory_path()

    dirs = [
        root / "life" / "areas" / "people",
        root / "life" / "areas" / "companies",
        root / "life" / "areas" / "projects",
        root / "life" / "areas" / "concepts",
        root / "notes",
        root / "archive",
        root / "prompts",
        root / "experiments",
    ]

    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

    entities_file = root / "entities.json"
    if not entities_file.exists():
        _write_text_locked(entities_file, json.dumps({}, indent=2))
    else:
        normalized = _normalize_entities(json.loads(entities_file.read_text()))
        _write_text_locked(entities_file, json.dumps(normalized, indent=2))

    memory_file = root / "MEMORY.md"
    if not memory_file.exists():
        _write_text_locked(memory_file, "# How Your Owner Thinks\n\n(Not yet populated)\n")

    user_file = root / "USER.md"
    if not user_file.exists():
        _write_text_locked(user_file, "(Not yet populated)\n")

    experiments_file = root / "experiments" / "learning_log.json"
    if not experiments_file.exists():
        _write_text_locked(experiments_file, json.dumps([], indent=2))

    logger.info(f"Memory initialized at {root}")


def status() -> dict:
    """Get memory system status."""
    root = get_memory_path()

    if not root.exists():
        return {"initialized": False, "error": "Memory not initialized. Run init_memory()."}

    entities = load_entities()
    entity_count = len(entities)

    fact_count = 0
    for entity_name, entity_data in entities.items():
        facts_file = root / entity_data["path"] / "facts.json"
        if facts_file.exists():
            facts = json.loads(facts_file.read_text())
            fact_count += len([
                f for f in facts.get("facts", [])
                if f.get("active", True) and f.get("status", "active") == "active"
            ])

    notes_dir = root / "notes"
    note_count = len(list(notes_dir.glob("*.md"))) if notes_dir.exists() else 0
    notes = sorted(notes_dir.glob("*.md"), reverse=True) if notes_dir.exists() else []
    last_note = notes[0].stem if notes else None

    prompt_version = get_prompt_version()
    experiments = load_experiments()

    return {
        "initialized": True,
        "memory_path": str(root),
        "entity_count": entity_count,
        "active_fact_count": fact_count,
        "daily_note_count": note_count,
        "last_note_date": last_note,
        "prompt_version": prompt_version,
        "experiment_count": len(experiments),
    }


# --- Entity operations ---

def load_entities() -> dict:
    """Load the master entity list."""
    entities_file = get_memory_path() / "entities.json"
    if not entities_file.exists():
        return {}
    return _normalize_entities(json.loads(entities_file.read_text()))


def save_entities(entities: dict) -> None:
    """Save the master entity list."""
    entities_file = get_memory_path() / "entities.json"
    _write_text_locked(entities_file, json.dumps(entities, indent=2))


def add_entity(name: str, category: str, aliases: Optional[list[str]] = None) -> None:
    """Add a new entity to the knowledge graph."""
    entities = load_entities()
    name = name.lower().replace(" ", "_")

    if name in entities:
        raise ValueError(f"Entity '{name}' already exists")

    entity_path = f"life/areas/{category}/{name}"
    entity_dir = get_memory_path() / entity_path
    entity_dir.mkdir(parents=True, exist_ok=True)

    facts_file = entity_dir / "facts.json"
    _write_text_locked(facts_file, json.dumps({
        "entity": name,
        "category": category,
        "facts": []
    }, indent=2))

    summary_file = entity_dir / "summary.md"
    _write_text_locked(summary_file, f"# {name.replace('_', ' ').title()}\n\n(No summary yet)\n")

    entities[name] = {
        "category": category,
        "aliases": aliases or [],
        "path": entity_path,
    }
    save_entities(entities)
    logger.info(f"Added entity: {name} [{category}]")


def resolve_entity(name: str) -> Optional[str]:
    """Resolve an entity name or alias to canonical name."""
    entities = load_entities()
    name_lower = name.lower().replace(" ", "_")

    if name_lower in entities:
        return name_lower

    for entity_name, data in entities.items():
        aliases_lower = [a.lower().replace(" ", "_") for a in data.get("aliases", [])]
        if name_lower in aliases_lower:
            return entity_name

    return None


def read_entity(name: str) -> Optional[dict]:
    """Read an entity's summary and active facts."""
    entities = load_entities()
    name_lower = name.lower().replace(" ", "_")

    if name_lower in entities:
        entity_data = entities[name_lower]
    else:
        resolved = resolve_entity(name)
        if not resolved:
            return None
        name_lower = resolved
        entity_data = entities[name_lower]

    entity_dir = get_memory_path() / entity_data["path"]

    summary_file = entity_dir / "summary.md"
    summary = summary_file.read_text() if summary_file.exists() else "(No summary)"

    facts_file = entity_dir / "facts.json"
    if facts_file.exists():
        facts_data = json.loads(facts_file.read_text())
        all_facts = facts_data.get("facts", [])
        active_facts = [
            f for f in all_facts
            if f.get("active", True) and f.get("status", "active") == "active"
        ]
        recent_facts = sorted(
            active_facts,
            key=lambda f: f.get("timestamp", f.get("extracted", "")),
            reverse=True
        )[:10]
    else:
        recent_facts = []

    return {
        "name": name_lower,
        "category": entity_data["category"],
        "summary": summary,
        "recent_facts": recent_facts,
    }


def read_recent_facts(name: str) -> Optional[list[dict]]:
    """Same active/recent-facts selection as read_entity(), without also
    reading summary.md — for callers (e.g. heartbeat's relationship pulse)
    that only need timestamps and don't use the prose summary at all."""
    entities = load_entities()
    name_lower = name.lower().replace(" ", "_")

    if name_lower in entities:
        entity_data = entities[name_lower]
    else:
        resolved = resolve_entity(name)
        if not resolved:
            return None
        name_lower = resolved
        entity_data = entities[name_lower]

    entity_dir = get_memory_path() / entity_data["path"]
    facts_file = entity_dir / "facts.json"
    if not facts_file.exists():
        return []

    facts_data = json.loads(facts_file.read_text())
    all_facts = facts_data.get("facts", [])
    active_facts = [
        f for f in all_facts
        if f.get("active", True) and f.get("status", "active") == "active"
    ]
    return sorted(
        active_facts,
        key=lambda f: f.get("timestamp", f.get("extracted", "")),
        reverse=True
    )[:10]


# --- Fact operations ---

# Trust tier a fact's origin falls into — a defense against a hostile or
# merely low-quality source silently rewriting the knowledge graph, since
# facts can enter memory through more than just the owner's own words
# (e.g. extract_irc_learnings() runs the same extraction pipeline over
# public IRC channel content from arbitrary third parties):
#   owner_stated   — extracted from or directly written by the owner's own words
#   agent_inferred — the agent's own synthesis/decision during conversation,
#                    not a verbatim owner statement
#   tool_derived   — extracted from a tool result (IRC channel content, a
#                    fetched web page, etc.) — nobody the framework already
#                    trusts asserted this; treat it as lower-confidence
VALID_PROVENANCE = {"owner_stated", "agent_inferred", "tool_derived"}
DEFAULT_PROVENANCE = "owner_stated"


def is_trusted_provenance(fact: dict) -> bool:
    """Cheap trust check consumers (scorers, grounding checks, a future
    memory-poisoning defense) can use without hardcoding the taxonomy."""
    return fact.get("provenance", DEFAULT_PROVENANCE) != "tool_derived"


# Authority is a DIFFERENT axis than provenance, deliberately: provenance
# says WHERE a fact came from (owner_stated/agent_inferred/tool_derived);
# authority says WHAT IT'S ALLOWED TO JUSTIFY once it's in memory. These
# can diverge — an owner_stated fact from an offhand, low-confidence
# remark ("I guess I kind of prefer terse answers?") shouldn't
# automatically carry the same weight as an owner_stated fact from an
# explicit instruction ("never do X without asking me first"), even
# though both share the same provenance.
#
# This distinction is motivated by "authority collapse": a documented
# failure mode where memory consolidation preserves a fact's CONTENT but
# silently strips the metadata about how strongly it should be trusted to
# justify downstream behavior — a casual preference gets synthesized into
# something later treated as a verified, actionable directive. Without an
# explicit, persisted authority label, there's nothing to stop that
# collapse from happening silently during synthesis.py's summary rewrite.
#
#   low      — informational only; should not by itself justify a
#              privileged or hard-behavior-changing action, and synthesis
#              should phrase it with visible uncertainty rather than as
#              settled fact
#   standard — normal conversational use (default)
#   high     — can justify enforcing an explicit behavioral constraint;
#              reserved for genuinely unambiguous owner directives
VALID_AUTHORITY = {"low", "standard", "high"}
DEFAULT_AUTHORITY = "standard"


def _derive_default_authority(category: str, provenance: str) -> str:
    """Authority isn't independently specified by most callers — it's
    derived from provenance, matching the paper's own approach of
    predicting authority at write time rather than leaving it unspecified
    until some later point decides to trust the content.

    category is intentionally NOT used to auto-elevate to "high" here.
    "constraint" in this codebase's fact taxonomy (config/extraction.md)
    means an ordinary world-fact ("Surgery costs $X", "Deadline is
    Friday"), not a behavioral directive to the agent — conflating the
    two would let any owner-stated world-fact silently acquire the power
    to justify enforcing a behavioral constraint. "high" is reserved for
    callers who set it explicitly, with a real basis for treating it as
    an unambiguous directive.
    """
    if provenance == "tool_derived":
        # Untrusted external content never gets elevated authority by
        # default, regardless of what category it landed in.
        return "low"
    return "standard"


def is_actionable_authority(fact: dict) -> bool:
    """Cheap check consumers can use without hardcoding the taxonomy —
    mirrors is_trusted_provenance() but for the authority axis."""
    return fact.get("authority", DEFAULT_AUTHORITY) != "low"


def add_fact(
    entity_name: str,
    fact_type: str,
    content: str,
    source: str = "conversation",
    context: str = "active",
    provenance: str = DEFAULT_PROVENANCE,
    valid_from: str | None = None,
    authority: str | None = None,
) -> str:
    """Add a fact to an entity. Returns the fact ID.

    valid_from/valid_to are a bi-temporal pair, additive alongside the
    existing status/supersededBy tracking rather than replacing it:
    `timestamp` already records when a fact was RECORDED (ingestion time);
    valid_from/valid_to record when it was TRUE (event time/validity
    window), which can differ — "I switched jobs March 1" extracted from a
    conversation on March 5 has timestamp=March 5, valid_from=March 1.
    valid_from defaults to now (same as timestamp) when the caller doesn't
    know a more specific event date. valid_to stays null while the fact is
    active and gets stamped by supersede_fact() when it's replaced — this
    is what makes facts_valid_at() answerable even for now-superseded
    facts, which read_entity()'s active-only filter can't reconstruct at
    all today.
    """
    entities = load_entities()
    name_lower = entity_name.lower().replace(" ", "_")

    if name_lower not in entities:
        raise ValueError(f"Entity '{entity_name}' not found")

    if provenance not in VALID_PROVENANCE:
        logger.warning(f"Unknown provenance '{provenance}' for fact on '{entity_name}' — defaulting to agent_inferred")
        provenance = "agent_inferred"

    if authority is None:
        authority = _derive_default_authority(fact_type, provenance)
    elif authority not in VALID_AUTHORITY:
        logger.warning(f"Unknown authority '{authority}' for fact on '{entity_name}' — deriving default instead")
        authority = _derive_default_authority(fact_type, provenance)

    entity_data = entities[name_lower]
    facts_file = get_memory_path() / entity_data["path"] / "facts.json"
    fact_id = f"fact-{uuid.uuid4().hex[:8]}"
    now = datetime.now().isoformat()

    new_fact = {
        "id": fact_id,
        "fact": content,
        "category": fact_type,
        "context": context,
        "timestamp": now,
        "source": source,
        "provenance": provenance,
        "authority": authority,
        "status": "active",
        "supersededBy": None,
        "valid_from": valid_from or now,
        "valid_to": None,
        # V1 compat
        "content": content,
        "type": fact_type,
        "extracted": now,
        "active": True,
    }
    def _append_fact(data: dict) -> None:
        data.setdefault("entity", name_lower)
        data.setdefault("category", entity_data["category"])
        data.setdefault("facts", [])
        data["facts"].append(new_fact)

    update_json_file(
        facts_file,
        {"entity": name_lower, "category": entity_data["category"], "facts": []},
        _append_fact,
    )

    logger.info(f"Added fact {fact_id} to {name_lower}: {content[:50]}...")
    return fact_id


def supersede_fact(entity_name: str, old_fact_id: str, new_fact_id: str) -> bool:
    """Mark a fact as superseded by a newer one."""
    entities = load_entities()
    name_lower = entity_name.lower().replace(" ", "_")

    if name_lower not in entities:
        return False

    entity_data = entities[name_lower]
    facts_file = get_memory_path() / entity_data["path"] / "facts.json"

    if not facts_file.exists():
        return False

    def _supersede(data: dict) -> bool:
        for fact in data.get("facts", []):
            if fact.get("id") == old_fact_id:
                fact["status"] = "superseded"
                fact["active"] = False
                fact["supersededBy"] = new_fact_id
                fact["valid_to"] = datetime.now().isoformat()
                return True
        return False

    return update_json_file(facts_file, {"facts": []}, _supersede)


def facts_valid_at(entity_name: str, at_time: str) -> list[dict]:
    """Return facts (active or since-superseded) whose validity window
    contains `at_time` (ISO 8601) — "what did we believe was true about
    this entity as of this date", not just "what's true now".

    Falls back gracefully for facts written before valid_from/valid_to
    existed: treats a missing valid_from as "always valid from the
    beginning of time" and a missing valid_to on a superseded fact as
    "still valid" (better to over-include a legacy fact than silently
    drop it from a historical query it has no way to opt out of).
    """
    entities = load_entities()
    name_lower = entity_name.lower().replace(" ", "_")
    if name_lower not in entities:
        return []

    facts_file = get_memory_path() / entities[name_lower]["path"] / "facts.json"
    if not facts_file.exists():
        return []

    at = _to_naive_local(datetime.fromisoformat(at_time))
    facts_data = json.loads(facts_file.read_text())
    result = []
    for fact in facts_data.get("facts", []):
        valid_from = fact.get("valid_from")
        valid_to = fact.get("valid_to")
        if valid_from and _to_naive_local(datetime.fromisoformat(valid_from)) > at:
            continue
        if valid_to and _to_naive_local(datetime.fromisoformat(valid_to)) <= at:
            continue
        result.append(fact)
    return result


def _to_naive_local(dt: datetime) -> datetime:
    """Normalize a datetime to naive local time for comparison.

    Every timestamp this module writes (add_fact/supersede_fact both use
    datetime.now().isoformat(), which is naive local time) is naive.
    facts_valid_at() accepts an at_time from a caller, which may well be
    timezone-aware ISO 8601 (a valid '...+00:00' or 'Z'-suffixed string) —
    comparing a naive and an aware datetime raises TypeError. Converting
    any aware input down to naive local time keeps every comparison in
    facts_valid_at() apples-to-apples with what's actually stored, in
    either direction (an aware at_time, or — defensively, in case a future
    caller writes aware timestamps into a fact — an aware stored value).
    """
    if dt.tzinfo is not None:
        return dt.astimezone().replace(tzinfo=None)
    return dt


def search_facts(query: str) -> list[dict]:
    """Search all active facts for a keyword/phrase."""
    results = []
    entities = load_entities()
    query_lower = query.lower()

    for entity_name, entity_data in entities.items():
        facts_file = get_memory_path() / entity_data["path"] / "facts.json"
        if not facts_file.exists():
            continue

        facts_data = json.loads(facts_file.read_text())
        for fact in facts_data.get("facts", []):
            if not fact.get("active", True):
                continue
            if fact.get("status", "active") != "active":
                continue
            text = fact.get("fact", fact.get("content", ""))
            if query_lower in text.lower():
                score = text.lower().count(query_lower)
                if query_lower in entity_name.lower():
                    score += 1
                results.append({
                    "entity": entity_name,
                    "fact": fact,
                    "relevance_score": score,
                })

    return results


def list_entities_by_category(category: Optional[str] = None) -> list[dict]:
    """List all entities, optionally filtered by category."""
    entities = load_entities()
    result = []
    for name, data in entities.items():
        if category and data["category"] != category:
            continue
        result.append({
            "name": name,
            "category": data["category"],
            "aliases": data.get("aliases", []),
        })
    return result


def deactivate_fact(entity_name: str, fact_id: str) -> bool:
    """Mark a fact as inactive (soft delete)."""
    entities = load_entities()
    name_lower = entity_name.lower().replace(" ", "_")

    if name_lower not in entities:
        return False

    entity_data = entities[name_lower]
    facts_file = get_memory_path() / entity_data["path"] / "facts.json"

    if not facts_file.exists():
        return False

    def _deactivate(data: dict) -> bool:
        for fact in data.get("facts", []):
            if fact.get("id") == fact_id:
                fact["active"] = False
                fact["status"] = "superseded"
                return True
        return False

    return update_json_file(facts_file, {"facts": []}, _deactivate)


# --- Timeline ---

def read_timeline(date: str) -> Optional[str]:
    """Read daily notes for a specific date (YYYY-MM-DD)."""
    notes_file = get_memory_path() / "notes" / f"{date}.md"
    if not notes_file.exists():
        return None
    return notes_file.read_text()


def append_timeline(date: str, entry: str) -> None:
    """Append an entry to a day's timeline."""
    notes_dir = get_memory_path() / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    notes_file = notes_dir / f"{date}.md"

    with _exclusive_lock(notes_file):
        if notes_file.exists():
            content = notes_file.read_text()
            content += f"\n{entry}\n"
        else:
            content = f"# {date}\n\n{entry}\n"
        _atomic_write_text(notes_file, content)


# --- Tacit knowledge ---

MEMORY_MD_MAX_CHARS = 2200
USER_MD_MAX_CHARS = 1400


def read_tacit() -> str:
    """Read tacit knowledge (MEMORY.md) — injected into every session prompt."""
    memory_file = get_memory_path() / "MEMORY.md"
    if not memory_file.exists():
        return "(Not yet populated)"
    return memory_file.read_text()


def write_tacit(content: str) -> None:
    """Write tacit knowledge (MEMORY.md), capped at MEMORY_MD_MAX_CHARS."""
    if len(content) > MEMORY_MD_MAX_CHARS:
        content = content[:MEMORY_MD_MAX_CHARS]
        logger.warning(f"MEMORY.md truncated to {MEMORY_MD_MAX_CHARS} chars")
    memory_file = get_memory_path() / "MEMORY.md"
    _write_text_locked(memory_file, content)


def read_user_profile() -> str:
    """Read the owner profile (USER.md) — injected into every session prompt."""
    user_file = get_memory_path() / "USER.md"
    if not user_file.exists():
        return "(Not yet populated)"
    return user_file.read_text()


def write_user_profile(content: str) -> None:
    """Write the owner profile (USER.md), capped at USER_MD_MAX_CHARS."""
    if len(content) > USER_MD_MAX_CHARS:
        content = content[:USER_MD_MAX_CHARS]
        logger.warning(f"USER.md truncated to {USER_MD_MAX_CHARS} chars")
    user_file = get_memory_path() / "USER.md"
    _write_text_locked(user_file, content)


# --- Prompt versioning ---

def get_prompt_version() -> int:
    """Get current prompt version number."""
    prompts_dir = get_memory_path() / "prompts"
    if not prompts_dir.exists():
        return 0
    versions = list(prompts_dir.glob("v*.md"))
    if not versions:
        return 0
    return max(int(v.stem[1:]) for v in versions)


def load_system_prompt_from_memory() -> Optional[str]:
    """Load the current versioned system prompt, if any."""
    version = get_prompt_version()
    if version == 0:
        return None
    prompt_file = get_memory_path() / "prompts" / f"v{version}.md"
    if prompt_file.exists():
        return prompt_file.read_text()
    return None


def save_system_prompt(new_prompt: str) -> int:
    """Save a new version of the system prompt. Returns new version number."""
    prompts_dir = get_memory_path() / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    version_file = prompts_dir / ".prompt-version.lock"
    with _exclusive_lock(version_file):
        version = get_prompt_version() + 1
        prompt_file = prompts_dir / f"v{version}.md"
        _atomic_write_text(prompt_file, new_prompt)

        log_file = prompts_dir / "changelog.json"
        changelog = _read_json(log_file, [])
        changelog.append({
            "version": version,
            "timestamp": datetime.now().isoformat(),
            "file": f"v{version}.md",
        })
        _atomic_write_text(log_file, json.dumps(changelog, indent=2))

    logger.info(f"Saved system prompt v{version}")
    return version


def revert_system_prompt(to_version: int) -> bool:
    """Revert to a previous prompt version by copying it as the new latest."""
    prompts_dir = get_memory_path() / "prompts"
    old_file = prompts_dir / f"v{to_version}.md"

    if not old_file.exists():
        return False

    old_prompt = old_file.read_text()
    save_system_prompt(old_prompt)
    logger.info(f"Reverted prompt to v{to_version} (saved as new version)")
    return True


# --- Experiment logging ---

def load_experiments() -> list[dict]:
    """Load the learning log."""
    log_file = get_memory_path() / "experiments" / "learning_log.json"
    if not log_file.exists():
        return []
    return json.loads(log_file.read_text())


def log_experiment(hypothesis: str, result: str, status: str = "testing") -> None:
    """Log an experiment to the learning log."""
    log_file = get_memory_path() / "experiments" / "learning_log.json"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    def _append(data: list) -> None:
        data.append({
            "id": f"exp-{uuid.uuid4().hex[:8]}",
            "timestamp": datetime.now().isoformat(),
            "hypothesis": hypothesis,
            "result": result,
            "status": status,
        })
    update_json_file(log_file, [], _append)


def update_experiment_status(experiment_id: str, new_status: str, result: str = "") -> bool:
    """Update an experiment's status."""
    log_file = get_memory_path() / "experiments" / "learning_log.json"

    def _update(data: list) -> bool:
        for exp in data:
            if exp.get("id") == experiment_id:
                exp["status"] = new_status
                if result:
                    exp["result"] = result
                exp["updated"] = datetime.now().isoformat()
                return True
        return False

    return update_json_file(log_file, [], _update)


# --- Tasks ---

def read_tasks() -> str:
    """Read the task list."""
    tasks_file = get_memory_path() / "tasks.md"
    if not tasks_file.exists():
        return "No task list found."
    return tasks_file.read_text()


def add_task(task: str) -> None:
    """Add a task to the active list."""
    tasks_file = get_memory_path() / "tasks.md"

    if not tasks_file.exists():
        content = f"# Tasks\n\n## Active\n\n- {task}\n\n## Completed\n\n(none yet)\n"
    else:
        content = tasks_file.read_text()
        if "## Active\n\n(none yet)" in content:
            content = content.replace("## Active\n\n(none yet)", f"## Active\n\n- {task}")
        elif "## Active\n\n" in content:
            content = content.replace("## Active\n\n", f"## Active\n\n- {task}\n")
        else:
            content = content.replace("## Active\n", f"## Active\n\n- {task}\n")

    _write_text_locked(tasks_file, content)


def complete_task(task: str) -> bool:
    """Move a task from Active to Completed."""
    tasks_file = get_memory_path() / "tasks.md"
    if not tasks_file.exists():
        return False

    with _exclusive_lock(tasks_file):
        content = tasks_file.read_text()
        task_line = f"- {task}\n"
        if task_line not in content:
            return False

        content = content.replace(task_line, "", 1)
        if "(none yet)" in content:
            content = content.replace("## Completed\n\n(none yet)", f"## Completed\n\n- {task}")
        elif "## Completed\n\n" in content:
            content = content.replace("## Completed\n\n", f"## Completed\n\n- {task}\n")
        else:
            content = content.replace("## Completed\n", f"## Completed\n\n- {task}\n")

        _atomic_write_text(tasks_file, content)
        return True


if __name__ == "__main__":
    init_memory()
    print(json.dumps(status(), indent=2))
