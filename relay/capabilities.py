"""Interface-based capability gating.

Authorizes tool execution based on which *transport* a message arrived on,
not on claimed user identity. Complements (does not replace) the existing
owner-identity check on PRIVILEGED_TOOLS in relay/tools.py — that check
answers "is this the owner?"; this one answers "can this transport reach
this tool at all, regardless of who's on the other end?"

Rationale: a framework built to be reachable on open channels (public IRC,
an unauthenticated webhook, etc.) needs a default-closed tool surface for
those channels, independent of whatever identity claims arrive over them.
Trusted, authenticated interfaces (Telegram/Slack with an allowlisted user,
a local CLI) get the full tool surface; anything else gets a narrow,
explicitly-safe allowlist.

Fails closed: an interface name not in INTERFACE_TIERS is treated as
UNTRUSTED, not TRUSTED — a new or misconfigured interface can't
accidentally inherit full privileges.
"""

import logging

logger = logging.getLogger(__name__)

TRUSTED = "trusted"
UNTRUSTED = "untrusted"

# Which tier each interface belongs to. Anything not listed here falls
# through the .get() default of UNTRUSTED below — fail closed, not open.
INTERFACE_TIERS: dict[str, str] = {
    "telegram": TRUSTED,
    "slack": TRUSTED,
    "cli": TRUSTED,
    "irc": UNTRUSTED,
    "unknown": UNTRUSTED,
}

# Tools an UNTRUSTED interface may call. Deliberately narrow: memory reads
# and light writes, safe web reads, messaging escalation to the owner, and
# self-introspection. Nothing here can read/write secrets, touch the
# filesystem outside memory, run shell commands, or modify the agent's own
# code or instructions. Anything not listed is denied by default — this is
# an allowlist, not a denylist, on purpose: a new tool added to tools.py
# is untrusted-denied until someone deliberately opts it in here.
UNTRUSTED_ALLOWED: frozenset[str] = frozenset({
    "read_memory", "search_memory", "list_entities", "write_memory",
    "read_timeline", "read_tacit",
    "read_skill",
    "read_tasks", "add_task", "complete_task",
    "lighthouse_read", "lighthouse_search",
    "browse_url", "fetch_url",
    "send_message_to_owner",
    "read_my_config",
    "read_current_investigation", "add_to_agenda",
    "list_capabilities",
})


def tier_for(interface: str) -> str:
    """Return the trust tier for an interface name. Fails closed."""
    return INTERFACE_TIERS.get(interface, UNTRUSTED)


def is_allowed(tool_name: str, interface: str) -> bool:
    """True if the given interface may call the given tool."""
    if tier_for(interface) == TRUSTED:
        return True
    return tool_name in UNTRUSTED_ALLOWED


def check(tool_name: str, interface: str) -> dict:
    """Full check with a denial reason. Use at the dispatch chokepoint.

    Returns {"allowed": bool, "reason": str}. The denial reason is
    deliberately terse — it doesn't enumerate what tools DO exist, so a
    denial can't be used as a discovery oracle by something probing the
    tool surface from an untrusted channel.
    """
    if is_allowed(tool_name, interface):
        return {"allowed": True, "reason": ""}
    logger.warning(f"Capability denied: tool={tool_name} interface={interface}")
    return {
        "allowed": False,
        "reason": f"'{tool_name}' is not available on this channel.",
    }


def list_capabilities(interface: str = "unknown") -> str:
    """Self-description tool: what can I do on this channel?

    Registered as the `list_capabilities` tool so the agent (or a curious
    user) can ask what's reachable without needing to consult the source.
    """
    tier = tier_for(interface)
    if tier == TRUSTED:
        return f"Interface '{interface}' is trusted — full tool surface available."
    allowed = sorted(UNTRUSTED_ALLOWED)
    return (
        f"Interface '{interface}' is untrusted — {len(allowed)} tools available:\n"
        + "\n".join(f"  - {name}" for name in allowed)
    )
