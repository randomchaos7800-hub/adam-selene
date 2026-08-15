"""Fact-check gate on outbound messages.

Scans text the agent is about to send for claims of file creation/
completion ("created X", "wrote Y", "saved Z") and verifies each claimed
path actually exists on disk before the message reaches the owner. Catches
a confident-sounding fabrication ("I've created the deploy script at
scripts/deploy.sh") that a system-prompt instruction alone can't reliably
stop — this runs outside the model's own reasoning entirely, at the point
the text is about to leave the process, so it can't be talked past.

Pure function, no side effects, silent (returns the text unchanged) when
nothing is claimed. Deliberately narrow in scope — it only checks
file-creation-style claims, not general factual accuracy, because that's
the one class of claim that's both common in agent output and cheaply,
deterministically verifiable (does the file exist, yes or no).
"""

import re
from pathlib import Path

from relay import config

# Claim verbs in their common conjugations — "I created", "I've written",
# "just saved", "I'll generate", etc.
_CLAIM_VERBS = r"(?:creat|writ|built?|sav|generat|deploy|updat)(?:e|es|ed|ing)?"

# Path-like tokens: backtick-quoted, or a bare ~/- or /-rooted path with a
# file extension. Deliberately requires an extension to avoid false-
# positiving on ordinary prose paths ("check /etc for config").
_PATH_PATTERN = re.compile(
    r"`([^`]+\.\w+)`"
    r"|(?<![`\w])((?:~/|/)[\w./-]+\.\w+)(?![`\w])"
)
_CLAIM_VERB_PATTERN = re.compile(_CLAIM_VERBS, re.IGNORECASE)

_CONTEXT_WINDOW = 80  # chars on each side of a path to look for a claim verb


def _extract_claimed_paths(text: str) -> list[str]:
    """Return path-like strings that appear near a claim verb."""
    claimed = []
    for match in _PATH_PATTERN.finditer(text):
        path_str = match.group(1) or match.group(2)
        start = max(0, match.start() - _CONTEXT_WINDOW)
        end = min(len(text), match.end() + _CONTEXT_WINDOW)
        window = text[start:end]
        if _CLAIM_VERB_PATTERN.search(window):
            claimed.append(path_str)
    return claimed


def _resolve_candidates(path_str: str) -> list[Path]:
    """Candidate real-filesystem locations for a claimed path string."""
    candidates = []
    if path_str.startswith("~/"):
        candidates.append(Path(path_str).expanduser())
    elif path_str.startswith("/"):
        candidates.append(Path(path_str))
    else:
        candidates.append(config.project_root() / path_str)
    # Also try relative-to-project-root even for absolute-looking paths,
    # in case the model wrote a project-relative path with a leading slash
    # by mistake (common enough to be worth the extra, cheap check).
    candidates.append(config.project_root() / path_str.lstrip("/"))
    return candidates


def check_claims(text: str) -> str:
    """Verify file-creation claims in `text` against the real filesystem.

    Returns the text unchanged if nothing is claimed or all claims check
    out. Returns text + an appended warning block listing any claimed
    paths that don't exist.
    """
    if not text or not text.strip():
        return text

    claimed = _extract_claimed_paths(text)
    if not claimed:
        return text

    missing = []
    seen = set()
    for path_str in claimed:
        if path_str in seen:
            continue
        seen.add(path_str)
        if not any(c.exists() for c in _resolve_candidates(path_str)):
            missing.append(path_str)

    if not missing:
        return text

    warning = (
        "\n\n⚠️ [FACT-CHECK FAILED] The following claimed path(s) don't exist on disk:\n"
        + "\n".join(f"  - {p}" for p in missing)
    )
    return text + warning
