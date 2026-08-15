"""read_memory_history — bi-temporal query over an entity's fact history.

The knowledge graph tracks status (active/superseded) and supersededBy on
each fact, but read_memory only ever surfaces currently-active facts —
once something is superseded it's invisible, with no way to reconstruct
what the agent believed at an earlier point in time. This tool answers
that question using the valid_from/valid_to window every fact now carries
(see memory/storage.py's facts_valid_at()).

A second, deliberately small example of a registry-based tool domain
(alongside relay/tool_domains/skills_mgmt.py) — see relay/tool_registry.py
for why new tool domains register this way instead of joining the
relay/tools.py monolith.
"""

from memory import storage
from relay.tool_registry import REGISTRY, ToolEntry


def _handle_read_memory_history(tool_input: dict, **_ctx) -> str:
    entity = tool_input.get("entity", "")
    at_time = tool_input.get("at_time", "")
    if not entity or not at_time:
        return "Error: entity and at_time are both required."

    try:
        facts = storage.facts_valid_at(entity, at_time)
    except ValueError:
        return f"Error: at_time '{at_time}' isn't a valid ISO 8601 date/timestamp."

    if not facts:
        return f"No facts for '{entity}' were valid as of {at_time} (or the entity doesn't exist)."

    lines = [f"Facts about '{entity}' valid as of {at_time}:\n"]
    for fact in facts:
        text = fact.get("fact", fact.get("content", ""))
        category = fact.get("category", fact.get("type", ""))
        status = fact.get("status", "active")
        lines.append(f"- [{category}] {text} ({status})")
    return "\n".join(lines)


READ_MEMORY_HISTORY_SCHEMA = {
    "name": "read_memory_history",
    "description": (
        "See what was true about an entity AS OF a specific past date/time — not just what's "
        "true now. Unlike read_memory (current facts only), this reconstructs historical state "
        "using each fact's validity window, including facts that have since been superseded. "
        "Use when the owner asks something like 'what did I tell you about X back in March' or "
        "'what did we think was true about Y before it changed'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "entity": {
                "type": "string",
                "description": "The entity name (e.g., 'alice', 'myproject')",
            },
            "at_time": {
                "type": "string",
                "description": "ISO 8601 date or datetime to query as-of (e.g. '2026-03-01' or '2026-03-01T12:00:00')",
            },
        },
        "required": ["entity", "at_time"],
    },
}

REGISTRY.register(ToolEntry(
    name="read_memory_history",
    toolset="memory",
    schema=READ_MEMORY_HISTORY_SCHEMA,
    handler=_handle_read_memory_history,
))
