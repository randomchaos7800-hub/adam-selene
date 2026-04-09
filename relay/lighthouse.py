"""LIGHTHOUSE — The agent's personal reasoning and learning journal.

Captures how the agent thinks, where reasoning breaks down, corrections,
patterns in the owner, and the agent's evolving self-understanding.

Distinct from the memory system (which stores facts about the world).
LIGHTHOUSE stores facts about the agent's own reasoning process.
"""

import re
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from relay import config

logger = logging.getLogger(__name__)

LIGHTHOUSE_ROOT = config.lighthouse_root()

VALID_SECTIONS = {
    "reasoning",
    "corrections",
    "conversations",
    "patterns",
    "tools",
    "map",
    "identity",
    "archive",
}

LIVING_DOC_NAME = "living.md"


def _section_path(section: str) -> Path:
    return LIGHTHOUSE_ROOT / section


def _slug(title: str) -> str:
    """Convert a title to a filename-safe slug."""
    s = title.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:60] or "entry"


def _timestamp() -> str:
    now = datetime.now()
    return now.strftime("%Y-%m-%d_%H%M")


def _date_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def write_entry(
    section: str,
    title: str,
    content: str,
    tags: Optional[list[str]] = None,
) -> dict:
    """Write a new LIGHTHOUSE entry."""
    if section not in VALID_SECTIONS:
        return {
            "success": False,
            "error": f"Invalid section '{section}'. Valid: {sorted(VALID_SECTIONS)}",
        }

    section_dir = _section_path(section)
    section_dir.mkdir(parents=True, exist_ok=True)

    ts = _timestamp()
    slug = _slug(title)
    filename = f"{ts}_{slug}.md"
    filepath = section_dir / filename

    tags_str = ", ".join(tags) if tags else ""
    header = f"# {title}\ndate: {_date_str()}\nsection: {section}"
    if tags_str:
        header += f"\ntags: [{tags_str}]"
    header += "\n\n---\n\n"

    full_content = header + content.strip() + "\n"

    try:
        filepath.write_text(full_content, encoding="utf-8")
        return {
            "success": True,
            "path": str(filepath),
            "section": section,
            "title": title,
            "filename": filename,
        }
    except Exception as e:
        logger.error(f"LIGHTHOUSE write failed: {e}")
        return {"success": False, "error": str(e)}


def read_entries(section: Optional[str] = None, limit: int = 10) -> dict:
    """Read recent LIGHTHOUSE entries, optionally filtered by section."""
    if section and section not in VALID_SECTIONS:
        return {
            "success": False,
            "error": f"Invalid section '{section}'. Valid: {sorted(VALID_SECTIONS)}",
        }

    sections_to_scan = [section] if section else list(VALID_SECTIONS - {"archive"})

    entries = []
    for sec in sections_to_scan:
        sec_path = _section_path(sec)
        if not sec_path.exists():
            continue
        for f in sec_path.glob("*.md"):
            if f.name == LIVING_DOC_NAME:
                continue
            try:
                stat = f.stat()
                entries.append({
                    "section": sec,
                    "filename": f.name,
                    "path": str(f),
                    "mtime": stat.st_mtime,
                    "content": f.read_text(encoding="utf-8"),
                })
            except Exception as e:
                logger.warning(f"Could not read {f}: {e}")

    # Sort by modification time, newest first
    entries.sort(key=lambda x: x["mtime"], reverse=True)
    entries = entries[:limit]

    if not entries:
        label = f"section '{section}'" if section else "LIGHTHOUSE"
        return {"success": True, "entries": [], "message": f"No entries in {label} yet."}

    # Format for readability
    formatted = []
    for e in entries:
        formatted.append({
            "section": e["section"],
            "filename": e["filename"],
            "content": e["content"],
        })

    return {"success": True, "entries": formatted, "count": len(formatted)}


def search_entries(query: str) -> dict:
    """Search all LIGHTHOUSE sections for a query string."""
    if not query or not query.strip():
        return {"success": False, "error": "No search query provided."}

    query_lower = query.lower()
    matches = []

    for section in VALID_SECTIONS:
        sec_path = _section_path(section)
        if not sec_path.exists():
            continue
        for f in sec_path.glob("*.md"):
            try:
                content = f.read_text(encoding="utf-8")
                if query_lower in content.lower():
                    # Find matching lines for context
                    lines = content.splitlines()
                    matching_lines = [
                        (i + 1, line)
                        for i, line in enumerate(lines)
                        if query_lower in line.lower()
                    ]
                    matches.append({
                        "section": section,
                        "filename": f.name,
                        "path": str(f),
                        "matching_lines": matching_lines[:5],
                        "total_matches": len(matching_lines),
                    })
            except Exception as e:
                logger.warning(f"Could not search {f}: {e}")

    matches.sort(key=lambda x: x["total_matches"], reverse=True)

    if not matches:
        return {
            "success": True,
            "matches": [],
            "message": f"No LIGHTHOUSE entries matching '{query}'.",
        }

    return {"success": True, "matches": matches, "count": len(matches)}


def write_living(observation: str) -> dict:
    """Add an observation to today's living document."""
    living_dir = LIGHTHOUSE_ROOT / "identity"
    living_dir.mkdir(parents=True, exist_ok=True)

    living_path = living_dir / LIVING_DOC_NAME
    date_str = _date_str()
    time_str = datetime.now().strftime("%H:%M")

    new_entry = f"\n### {date_str} {time_str}\n\n{observation.strip()}\n"

    try:
        if living_path.exists():
            existing = living_path.read_text(encoding="utf-8")
            living_path.write_text(existing + new_entry, encoding="utf-8")
        else:
            header = f"# Living Document\n\nObservations as they emerge through the day.\nNot polished. Not final. Just noticed.\n"
            living_path.write_text(header + new_entry, encoding="utf-8")

        return {
            "success": True,
            "path": str(living_path),
            "message": f"Observation added to living document at {time_str}.",
        }
    except Exception as e:
        logger.error(f"LIGHTHOUSE living write failed: {e}")
        return {"success": False, "error": str(e)}


def get_section_summary() -> dict:
    """Return entry counts per section. Useful for the agent to know what it has."""
    summary = {}
    for section in sorted(VALID_SECTIONS):
        sec_path = _section_path(section)
        if not sec_path.exists():
            summary[section] = 0
        else:
            count = len([f for f in sec_path.glob("*.md") if f.name != LIVING_DOC_NAME])
            summary[section] = count
    return {"success": True, "sections": summary}
