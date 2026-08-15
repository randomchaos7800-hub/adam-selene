"""Skill resolver — routes messages to skills based on trigger matching.

Inspired by the gbrain skills architecture (github.com/garrytan/gbrain).
Adapted for Adam Selene's always-on agent with persistent memory, self-reflection,
and constitutional constraints.

Design principles (credit: Garry Tan / gbrain):
  - "Thin harness, fat skills" — intelligence lives in markdown skill files, not code
  - Skills are tool-agnostic workflow definitions the model reads and executes
  - The resolver is a decision table, not a classifier
  - Skills chain naturally (e.g., signal-detector + memory-ops on every message)

Architecture:
  - load_manifest()     : parse skills/manifest.json → skill metadata
  - resolve_skills()    : match a message to relevant skill(s) via trigger keywords
  - load_skill()        : read a SKILL.md file → full markdown for system prompt
  - build_skill_prompt(): assemble skill context for the system prompt
  - filter_tools()      : restrict TOOL_DEFINITIONS to only tools declared by active skills
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from relay import config
from relay.fs_utils import update_json_file

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).parent.parent / "skills"
MANIFEST_PATH = SKILLS_DIR / "manifest.json"
RESOLVER_PATH = SKILLS_DIR / "RESOLVER.md"
CONVENTIONS_DIR = SKILLS_DIR / "conventions"
USAGE_PATH = SKILLS_DIR / ".usage.json"

# Always-on skills — fire on every message regardless of trigger match
ALWAYS_ON_SKILLS = frozenset({"signal-detector", "memory-ops"})

# Standing self-learning nudge — always appended to the skill prompt, same
# tier as the memory-first instruction. Keeps skill_manage from being
# purely reactive (only used when explicitly asked to "save this as a
# skill") by reminding the agent, every turn, that it's an option.
SELF_LEARNING_COMPACT = (
    "\n\nAfter completing a tricky multi-step task, fixing a non-obvious "
    "error, or being corrected on a workflow, consider saving the approach "
    "with skill_manage(action='create') so you don't have to re-derive it "
    "next time. If an active self-created skill turns out to be outdated "
    "or wrong, patch it now rather than silently working around it."
)

# Trigger patterns for each skill — compiled from SKILL.md frontmatter.
# This is the programmatic mirror of RESOLVER.md. Both exist because:
#   - RESOLVER.md is human-readable (the model reads it for disambiguation)
#   - _TRIGGER_MAP is machine-searchable (the code uses it for fast matching)
_TRIGGER_MAP: dict[str, list[re.Pattern]] = {}
_SKILL_CACHE: dict[str, str] = {}  # name → full SKILL.md content
_MANIFEST_CACHE: dict | None = None


def _load_manifest() -> dict:
    """Load and cache the skill manifest."""
    global _MANIFEST_CACHE
    if _MANIFEST_CACHE is not None:
        return _MANIFEST_CACHE
    if not MANIFEST_PATH.exists():
        logger.warning(f"Skill manifest not found: {MANIFEST_PATH}")
        _MANIFEST_CACHE = {"skills": []}
        return _MANIFEST_CACHE
    _MANIFEST_CACHE = json.loads(MANIFEST_PATH.read_text())
    return _MANIFEST_CACHE


def _build_trigger_map() -> None:
    """Build trigger pattern map from SKILL.md frontmatter.

    Parses the YAML frontmatter of each skill file to extract trigger phrases,
    then compiles them into regex patterns for fast matching.
    """
    global _TRIGGER_MAP
    if _TRIGGER_MAP:
        return

    manifest = _load_manifest()
    for skill in manifest.get("skills", []):
        name = skill["name"]
        skill_path = SKILLS_DIR / skill["path"]
        if not skill_path.exists():
            logger.warning(f"Skill file missing: {skill_path}")
            continue

        content = skill_path.read_text()
        # Extract triggers from YAML frontmatter
        triggers = _parse_triggers(content)
        if triggers:
            patterns = []
            for trigger in triggers:
                # Skip non-keyword triggers like "every inbound message"
                if any(skip in trigger.lower() for skip in ("every ", "idle ", "any ", "proactive")):
                    continue
                # Escape and compile as case-insensitive word boundary pattern
                escaped = re.escape(trigger.lower())
                try:
                    patterns.append(re.compile(escaped, re.IGNORECASE))
                except re.error:
                    logger.warning(f"Bad trigger pattern in {name}: {trigger}")
            if patterns:
                _TRIGGER_MAP[name] = patterns


def _parse_triggers(content: str) -> list[str]:
    """Extract trigger list from SKILL.md YAML frontmatter."""
    # Find frontmatter block
    if not content.startswith("---"):
        return []
    end = content.find("---", 3)
    if end == -1:
        return []
    frontmatter = content[3:end]

    # Simple YAML list extraction for triggers:
    triggers = []
    in_triggers = False
    for line in frontmatter.split("\n"):
        stripped = line.strip()
        if stripped.startswith("triggers:"):
            in_triggers = True
            continue
        if in_triggers:
            if stripped.startswith("- "):
                # Strip quotes and leading "- "
                trigger = stripped[2:].strip().strip('"').strip("'")
                triggers.append(trigger)
            elif stripped and not stripped.startswith("#"):
                # New key — end of triggers block
                break
    return triggers


def load_skill(name: str) -> Optional[str]:
    """Load a skill's full SKILL.md content, cached."""
    if name in _SKILL_CACHE:
        return _SKILL_CACHE[name]

    manifest = _load_manifest()
    for skill in manifest.get("skills", []):
        if skill["name"] == name:
            skill_path = SKILLS_DIR / skill["path"]
            if skill_path.exists():
                content = skill_path.read_text()
                _SKILL_CACHE[name] = content
                return content
            else:
                logger.warning(f"Skill file missing: {skill_path}")
                return None
    return None


def load_convention(name: str) -> Optional[str]:
    """Load a convention file from the conventions directory."""
    conv_path = CONVENTIONS_DIR / name
    if conv_path.exists():
        return conv_path.read_text()
    # Try with .md extension
    conv_path = CONVENTIONS_DIR / f"{name}.md"
    if conv_path.exists():
        return conv_path.read_text()
    return None


def _bump_usage(skill_names: list[str]) -> None:
    """Best-effort usage telemetry — increments use_count/last_used for each
    non-always-on skill routed to. Feeds scripts/skill_curator.py's
    active->stale->archived lifecycle for self-created skills.

    Uses relay.fs_utils's locked update_json_file rather than a bare
    read-modify-write — every message routes through here, so without a
    lock, two resolve_skills() calls close together (e.g. concurrent
    conversations, or a fast follow-up message) can race and silently
    lose an increment, undercounting usage that skill_curator.py's
    stale/archive decisions rely on.

    Never breaks routing on failure — this is purely observational.
    """
    try:
        def _update(usage: dict) -> None:
            now = datetime.now(timezone.utc).isoformat()
            for name in skill_names:
                if name in ALWAYS_ON_SKILLS:
                    continue
                entry = usage.setdefault(name, {"use_count": 0, "last_used": None})
                entry["use_count"] = entry.get("use_count", 0) + 1
                entry["last_used"] = now

        update_json_file(USAGE_PATH, {}, _update)
    except Exception as e:
        logger.debug(f"Skill usage tracking failed (non-fatal): {e}")


def resolve_skills(message: str) -> list[str]:
    """Resolve which skills should be active for a given message.

    Returns a list of skill names, always including always-on skills.
    Matched skills are ordered: always-on first, then by specificity.
    """
    _build_trigger_map()

    matched = set()

    # Always-on skills fire on every message
    matched.update(ALWAYS_ON_SKILLS)

    # Match message against trigger patterns
    msg_lower = message.lower()
    for skill_name, patterns in _TRIGGER_MAP.items():
        for pattern in patterns:
            if pattern.search(msg_lower):
                matched.add(skill_name)
                break

    # If no specific skill matched (beyond always-on), include query as default
    if matched == ALWAYS_ON_SKILLS:
        matched.add("query")

    # Order: always-on first, then alphabetical
    always_on = sorted(s for s in matched if s in ALWAYS_ON_SKILLS)
    specific = sorted(s for s in matched if s not in ALWAYS_ON_SKILLS)
    result = always_on + specific

    _bump_usage(specific)
    return result


def get_skill_tools(skill_names: list[str]) -> set[str]:
    """Get the union of all tool names declared by the given skills.

    Parses the tools: list from each skill's YAML frontmatter.
    """
    all_tools = set()
    for name in skill_names:
        content = load_skill(name)
        if not content:
            continue
        tools = _parse_tools(content)
        all_tools.update(tools)
    return all_tools


def _parse_tools(content: str) -> list[str]:
    """Extract tool list from SKILL.md YAML frontmatter."""
    if not content.startswith("---"):
        return []
    end = content.find("---", 3)
    if end == -1:
        return []
    frontmatter = content[3:end]

    tools = []
    in_tools = False
    for line in frontmatter.split("\n"):
        stripped = line.strip()
        if stripped.startswith("tools:"):
            in_tools = True
            continue
        if in_tools:
            if stripped.startswith("- "):
                tool = stripped[2:].strip().strip('"').strip("'")
                tools.append(tool)
            elif stripped and not stripped.startswith("#"):
                break
    return tools


# Tools that must always reach the model regardless of which skills are
# currently active. The intended "fallback: if no active skill declares
# any tools, expose everything" safety net below never actually fires in
# practice — ALWAYS_ON_SKILLS (memory-ops, signal-detector) always
# declares a non-empty tools list on every single message, so
# allowed_tools is never empty. A tool that isn't declared by ANY skill's
# frontmatter is therefore silently unreachable no matter how useful it
# is — this happened to list_capabilities, built specifically so the
# model can self-check what it's allowed to do, which turned out to be
# filtered out on every real turn. Rather than relying on every future
# cross-cutting tool remembering to get added to some skill's frontmatter,
# tools that are meant to always be available go here instead.
ALWAYS_AVAILABLE_TOOLS = frozenset({"list_capabilities"})


def filter_tool_definitions(tool_definitions: list[dict], skill_names: list[str]) -> list[dict]:
    """Filter TOOL_DEFINITIONS to only include tools declared by active skills
    (plus ALWAYS_AVAILABLE_TOOLS, regardless of skill filtering).

    This reduces the tool surface area presented to the model, focusing it on
    the tools relevant to the current skill context.
    """
    allowed_tools = get_skill_tools(skill_names)
    if not allowed_tools:
        # Fallback: if no tools resolved, return all (safety net)
        return tool_definitions
    allowed_tools = allowed_tools | ALWAYS_AVAILABLE_TOOLS
    return [t for t in tool_definitions if t["name"] in allowed_tools]


def build_skill_prompt() -> str:
    """Return the RESOLVER.md dispatch table — the only skill context pre-injected.

    Skills are fat; the harness is thin. Full SKILL.md bodies are loaded on-demand
    by the agent via the read_skill tool when RESOLVER.md directs it. This keeps
    the system prompt under ~3K chars (vs 15K with all skill bodies), making it
    viable for local models and maximising prompt cache hit rate.

    Always appends SELF_LEARNING_COMPACT — a fixed, small addition regardless
    of which skill is active, same tier as the memory-first instruction.
    """
    base = RESOLVER_PATH.read_text() if RESOLVER_PATH.exists() else ""
    return base + SELF_LEARNING_COMPACT


def reload():
    """Clear all caches. Call after skill files are modified."""
    global _TRIGGER_MAP, _SKILL_CACHE, _MANIFEST_CACHE
    _TRIGGER_MAP = {}
    _SKILL_CACHE = {}
    _MANIFEST_CACHE = None
    logger.info("Skill resolver caches cleared")
