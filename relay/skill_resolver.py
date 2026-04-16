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
from pathlib import Path
from typing import Optional

from relay import config

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).parent.parent / "skills"
MANIFEST_PATH = SKILLS_DIR / "manifest.json"
RESOLVER_PATH = SKILLS_DIR / "RESOLVER.md"
CONVENTIONS_DIR = SKILLS_DIR / "conventions"

# Always-on skills — fire on every message regardless of trigger match
ALWAYS_ON_SKILLS = frozenset({"signal-detector", "memory-ops"})

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
    return always_on + specific


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


def filter_tool_definitions(tool_definitions: list[dict], skill_names: list[str]) -> list[dict]:
    """Filter TOOL_DEFINITIONS to only include tools declared by active skills.

    This reduces the tool surface area presented to the model, focusing it on
    the tools relevant to the current skill context.
    """
    allowed_tools = get_skill_tools(skill_names)
    if not allowed_tools:
        # Fallback: if no tools resolved, return all (safety net)
        return tool_definitions
    return [t for t in tool_definitions if t["name"] in allowed_tools]


def build_skill_prompt(skill_names: list[str]) -> str:
    """Build the skill context section for the system prompt.

    Assembles:
    1. Resolver overview (so the model understands skill routing)
    2. Active conventions (cross-cutting rules)
    3. Full SKILL.md for each resolved skill

    This replaces the flat tool summary with rich workflow context.
    """
    sections = []

    # Header with credit
    sections.append(
        "## Active Skills\n\n"
        "_Skills architecture inspired by [gbrain](https://github.com/garrytan/gbrain) by Garry Tan._\n"
    )

    # Load conventions (always included)
    for conv_name in ("memory-first.md", "l0-constraints.md", "owner-auth.md"):
        conv = load_convention(conv_name)
        if conv:
            sections.append(f"### Convention: {conv_name.replace('.md', '')}\n\n{conv}\n")

    # Load each active skill
    for name in skill_names:
        content = load_skill(name)
        if content:
            sections.append(f"### Skill: {name}\n\n{content}\n")

    # Append resolver summary for disambiguation
    if RESOLVER_PATH.exists():
        resolver = RESOLVER_PATH.read_text()
        sections.append(f"### Skill Resolver (reference)\n\n{resolver}\n")

    return "\n---\n\n".join(sections)


def reload():
    """Clear all caches. Call after skill files are modified."""
    global _TRIGGER_MAP, _SKILL_CACHE, _MANIFEST_CACHE
    _TRIGGER_MAP = {}
    _SKILL_CACHE = {}
    _MANIFEST_CACHE = None
    logger.info("Skill resolver caches cleared")
