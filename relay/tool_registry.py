"""Modular tool registry — lets a new tool domain self-register instead of
requiring a hand-edit of the TOOL_DEFINITIONS list and the execute_tool()
if/elif chain in relay/tools.py.

relay/tools.py owns the original 58 built-in tools as a monolith (a big
static list + a big dispatcher). That's fine for the tools that shipped
with the framework, but it means every new tool domain — like the
self-learning skills system in relay/tool_domains/skills_mgmt.py — has to
either bolt onto that monolith or live somewhere with its own dispatch
path. This registry gives new domains the second option: import the
domain module once (relay/tools.py does this at the bottom of the file),
its tools self-register at import time, and both the schema list sent to
the model and the dispatch chokepoint in execute_tool() pick them up
automatically.

Old-style (TOOL_DEFINITIONS + if/elif) and registry-style tools coexist —
this is additive, not a replacement, and existing tools don't need to be
migrated for this to work.
"""

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class ToolEntry:
    """One registered tool.

    Args:
        name: tool name, must match the schema's "name" field
        toolset: logical grouping (e.g. "skills", "capabilities") — lets
            callers pull just one domain's tools via get_schemas(toolsets=...)
        schema: Anthropic tool-definition dict (name/description/input_schema)
        handler: callable(tool_input: dict, **ctx) -> str. ctx carries
            whatever execute_tool() was called with (session_store, user_id,
            interface) — handlers take **kwargs and use what they need.
        check_fn: optional callable() -> bool. If provided and it returns
            False, the tool is left out of get_schemas() entirely (e.g. a
            tool that needs an API key that isn't configured) — the model
            never sees a tool it can't actually use.
    """
    name: str
    toolset: str
    schema: dict
    handler: Callable[..., str]
    check_fn: Optional[Callable[[], bool]] = field(default=None)


class ToolRegistry:
    def __init__(self):
        self._entries: dict[str, ToolEntry] = {}

    def register(self, entry: ToolEntry) -> None:
        if entry.name in self._entries:
            logger.warning(f"Tool '{entry.name}' registered more than once — overwriting")
        self._entries[entry.name] = entry
        logger.debug(f"Registered tool: {entry.name} [{entry.toolset}]")

    def has(self, name: str) -> bool:
        return name in self._entries

    def get_schemas(self, toolsets: Optional[list[str]] = None) -> list[dict]:
        """Anthropic-format schemas for registered tools.

        Filters by toolset if given, and always filters out any tool whose
        check_fn() currently returns False.
        """
        schemas = []
        for entry in self._entries.values():
            if toolsets is not None and entry.toolset not in toolsets:
                continue
            if entry.check_fn is not None and not entry.check_fn():
                continue
            schemas.append(entry.schema)
        return schemas

    def list_toolsets(self) -> dict[str, list[str]]:
        """toolset -> [tool names], for introspection/docs."""
        out: dict[str, list[str]] = {}
        for entry in self._entries.values():
            out.setdefault(entry.toolset, []).append(entry.name)
        for names in out.values():
            names.sort()
        return out

    def dispatch(self, name: str, tool_input: dict, **ctx) -> str:
        """Invoke a registered tool's handler.

        Exceptions are caught and returned as a tool-error string rather
        than raised — a bug in one registered tool shouldn't take down the
        whole agentic loop, same failure-handling contract as the rest of
        execute_tool().
        """
        entry = self._entries.get(name)
        if entry is None:
            return f"Unknown tool: {name}"
        try:
            return entry.handler(tool_input, **ctx)
        except Exception as e:
            logger.error(f"Registered tool '{name}' failed: {e}")
            return f"Error: {name} failed — {e}"


REGISTRY = ToolRegistry()
