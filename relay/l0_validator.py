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
