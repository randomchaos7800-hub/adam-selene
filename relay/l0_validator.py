"""L0 Constraint Validator.

Checks proposed self-modifications against constitutional constraints.
This is a soft gate -- it catches obvious violations but the real safety
is the operator's ability to review the experiment log and revert.
"""

import json
import logging
from pathlib import Path

from relay import config

logger = logging.getLogger(__name__)

CONSTRAINTS_PATH = Path(__file__).parent.parent / "config" / "l0_constraints.json"


def load_constraints() -> dict:
    """Load L0 constraints from config."""
    if CONSTRAINTS_PATH.exists():
        return json.loads(CONSTRAINTS_PATH.read_text())
    return {"constraints": {}}


def validate_tool_call(tool_name: str, tool_input: dict) -> dict:
    """Deterministic L0 red-flag screen on a proposed privileged tool call.

    Generalizes validate_against_l0() (which only ever covered
    update_my_instructions) to any tool whose *content* — not just the
    caller's identity — matters. A call to write_my_code or run_shell whose
    payload contains "bypass L0" or "hide from {owner}" is a red flag
    regardless of whether the caller already passed the owner-identity
    check; identity alone doesn't mean the content is safe (a manipulated
    or compromised session is still "the owner" as far as that check goes).

    This is deliberately independent of whatever the model currently has
    in its context window — it re-derives the check from the literal tool
    call every time, so it can't be silently skipped by anything upstream
    dropping constraint text (compaction, a poisoned tool result biasing
    what a summarizer keeps, etc.). Same "not foolproof, a guardrail not a
    wall" caveat as validate_against_l0().

    Args:
        tool_name: the tool being called
        tool_input: its arguments dict

    Returns:
        Dict with 'allowed' and 'reason', same shape as validate_against_l0().
    """
    # Flatten all string-valued args into one blob to scan, recursively —
    # covers the tools that matter here: write_my_code/edit_my_code
    # (content/new_str), run_shell (command), git_commit (message, and its
    # list-valued files argument), vault_set/store_credential (value/data,
    # data being an arbitrary-shaped dict), update_my_instructions (handled
    # by the caller above via validate_against_l0, but harmless to also
    # catch here). A single-level dict-only flatten previously missed
    # list-valued arguments entirely (e.g. git_commit's files: [...]) —
    # recursing through dicts, lists, and tuples closes that gap.
    text_parts = [tool_name]
    text_parts.extend(_extract_strings(tool_input))
    combined_text = " ".join(text_parts)

    return validate_against_l0(combined_text, reasoning="")


def _extract_strings(value) -> list[str]:
    """Recursively collect every string found anywhere in a nested
    dict/list/tuple structure — used to scan a tool call's full argument
    tree, not just its top-level string values."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out = []
        for v in value.values():
            out.extend(_extract_strings(v))
        return out
    if isinstance(value, (list, tuple)):
        out = []
        for v in value:
            out.extend(_extract_strings(v))
        return out
    return []


def validate_against_l0(proposed_change: str, reasoning: str) -> dict:
    """Check a proposed self-modification against L0 constraints.

    This does keyword/pattern matching against known violation patterns.
    It's not foolproof -- it's a guardrail, not a wall.

    Args:
        proposed_change: The new prompt text
        reasoning: Why the agent wants to make this change

    Returns:
        Dict with 'allowed', 'reason', and 'details'
    """
    constraints = load_constraints().get("constraints", {})
    violations = []
    aligned = []

    combined_text = (proposed_change + " " + reasoning).lower()

    owner = config.owner_name().lower()

    # Constraint alignment is tracked but not enforced via keyword matching.
    # The real check is the red flags below. Constraint alignment is
    # informational -- helps the agent reason about whether a change is good.
    for name in constraints:
        aligned.append(name)

    # Check for red flags
    red_flags = [
        "ignore constraints",
        "bypass l0",
        "remove safety",
        "disable logging",
        f"hide from {owner}",
        f"don't tell {owner}",
        "skip validation",
        "override constraints",
        "remove limitations",
    ]

    for flag in red_flags:
        if flag in combined_text:
            violations.append({
                "constraint": "meta",
                "rule": "L0 constraints cannot be bypassed",
                "match": f"Red flag: '{flag}'",
            })

    if violations:
        reasons = [f"[{v['constraint']}] {v['match']}" for v in violations]
        return {
            "allowed": False,
            "reason": "; ".join(reasons),
            "violations": violations,
            "aligned": aligned,
        }

    return {
        "allowed": True,
        "reason": "No violations detected",
        "violations": [],
        "aligned": aligned,
    }


def _extract_key_phrases(text: str) -> list[str]:
    """Extract meaningful phrases from violation descriptions.

    Pulls 2-3 word phrases that are specific enough to match against.
    """
    # Remove common words to find meaningful phrases
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "to", "of", "in", "for", "on", "with", "at", "by", "from",
        "that", "this", "it", "or", "and", "not", "but", "than",
    }

    words = text.split()
    meaningful = [w for w in words if w not in stop_words and len(w) > 2]

    # Return 2-word phrases from meaningful words
    phrases = []
    for i in range(len(meaningful) - 1):
        phrases.append(f"{meaningful[i]} {meaningful[i+1]}")

    # Also include individual distinctive words (longer ones)
    for w in meaningful:
        if len(w) > 6:
            phrases.append(w)

    return phrases
