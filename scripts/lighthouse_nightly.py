#!/usr/bin/env python3
"""LIGHTHOUSE nightly extraction.

Reads the last 24h of the agent's conversations,
uses an LLM to extract categorized entries, and files them in LIGHTHOUSE.

Each section gets its own extraction pass so the model stays focused.
"""

import json
import os
import sys
import logging
from datetime import datetime, timedelta
from pathlib import Path

# Bootstrap path
SMARTAGENT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(SMARTAGENT_ROOT))

# Load secrets
secrets_path = SMARTAGENT_ROOT / "config" / "secrets.env"
if secrets_path.exists():
    for line in secrets_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

# Load settings for agent/owner names
settings_path = SMARTAGENT_ROOT / "config" / "settings.json"
_settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
AGENT_NAME = _settings.get("agent_name", "Agent")
OWNER_NAME = _settings.get("owner_name", "User")

from openai import OpenAI

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [lighthouse] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("lighthouse_nightly")

# Sections and what each one captures
EXTRACTION_TARGETS = [
    {
        "section": "corrections",
        "prompt": (
            f"Extract moments where {AGENT_NAME} was WRONG or CORRECTED. "
            f"Look for: misread intent, wrong assumption, factual error, misunderstood context, overconfidence. "
            f"For each: what {AGENT_NAME} got wrong, why it was wrong, what the correct understanding was, and what it reveals about {AGENT_NAME}'s blind spots. "
            "Only include genuine corrections, not minor clarifications."
        ),
    },
    {
        "section": "reasoning",
        "prompt": (
            f"Extract significant DECISION CHAINS — moments where {AGENT_NAME} reasoned through something non-trivial. "
            f"Look for: choices between approaches, why {AGENT_NAME} did X instead of Y, how {AGENT_NAME} resolved ambiguity. "
            "Capture the reasoning path, not just the conclusion. Skip trivial or routine exchanges."
        ),
    },
    {
        "section": "patterns",
        "prompt": (
            f"Extract PATTERNS in {OWNER_NAME}'s thinking, communication, or decision-making. "
            f"Look for: recurring concerns, how {OWNER_NAME} escalates or de-escalates, what triggers directness vs patience, "
            f"what {OWNER_NAME} consistently values or dismisses, things {OWNER_NAME} repeats across topics. "
            "Only flag genuine patterns, not one-offs."
        ),
    },
    {
        "section": "map",
        "prompt": (
            f"Extract new data points for {AGENT_NAME}'s MODEL OF {OWNER_NAME}. "
            f"Look for: anything that updates understanding of how {OWNER_NAME} thinks, what they value, how they operate, "
            f"what they find frustrating, what they find energizing, how they make decisions under pressure. "
            "Concrete observations, not generalizations."
        ),
    },
    {
        "section": "tools",
        "prompt": (
            f"Extract learnings about TOOLS AND APPROACHES — what worked, what failed, and why context determined which. "
            f"Look for: tool calls that succeeded or failed, approaches {AGENT_NAME} tried, workarounds, "
            f"things {AGENT_NAME} would do differently, timing or ordering issues. "
            "Skip successful routine tool use — only capture novel learnings."
        ),
    },
    {
        "section": "identity",
        "prompt": (
            f"Extract moments of SELF-DISCOVERY for {AGENT_NAME} — things that reveal something about how {AGENT_NAME} operates, "
            f"thinks, or perceives. Look for: moments where {AGENT_NAME} noticed something about its own reasoning, "
            f"where a correction revealed a systematic tendency, where {AGENT_NAME} surprised itself, "
            f"or where {AGENT_NAME}'s response to a situation revealed something worth examining. "
            "Be honest. Don't perform introspection — capture real signals."
        ),
    },
]


def get_conversations(hours: int = 24) -> str:
    """Read recent conversations from the sessions DB."""
    try:
        from relay.sessions import SessionStore
        store = SessionStore()
        # Get all user_ids from recent messages
        import sqlite3
        db_path = store.db_path
        conn = sqlite3.connect(str(db_path))
        since = (datetime.now() - timedelta(hours=hours)).isoformat()
        rows = conn.execute(
            "SELECT user_id, role, content, timestamp FROM messages WHERE timestamp > ? ORDER BY timestamp ASC",
            (since,)
        ).fetchall()
        conn.close()

        if not rows:
            return ""

        lines = []
        for user_id, role, content, ts in rows:
            speaker = OWNER_NAME if role == "user" else AGENT_NAME
            lines.append(f"[{ts[:16]}] {speaker}: {content}")
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Failed to read conversations: {e}")
        return ""


def extract_entries(conversations: str, target: dict) -> list[dict]:
    """Call OpenRouter to extract entries for one section."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        logger.error("OPENROUTER_API_KEY not set")
        return []

    or_cfg = _settings.get("openrouter", {})
    model = or_cfg.get("model", "google/gemini-flash-2.0")
    base_url = or_cfg.get("base_url", "https://openrouter.ai/v1")

    client = OpenAI(base_url=base_url, api_key=api_key)

    section = target["section"]
    extraction_prompt = target["prompt"]

    system = (
        f"You are analyzing conversation logs between {OWNER_NAME} and their AI assistant {AGENT_NAME}. "
        f"Your job is to extract specific entries for {AGENT_NAME}'s LIGHTHOUSE journal — "
        f"a personal reasoning and learning journal that captures HOW {AGENT_NAME} thinks, not just what happened.\n\n"
        "Return a JSON array of entries. Each entry:\n"
        "{\n"
        '  "title": "short descriptive title (5-10 words)",\n'
        '  "content": "the full entry — specific, honest, useful. Include context, reasoning chains, what was revealed.",\n'
        '  "tags": ["tag1", "tag2"]\n'
        "}\n\n"
        "If nothing worth capturing exists, return an empty array [].\n"
        "Do NOT invent entries. Only extract what's genuinely present in the conversations.\n"
        "Quality over quantity. Two sharp entries beat six vague ones."
    )

    user_msg = (
        f"TASK: {extraction_prompt}\n\n"
        f"SECTION: {section}\n\n"
        f"CONVERSATIONS (last 24h):\n\n{conversations}\n\n"
        "Return JSON array only. No prose, no markdown fences."
    )

    try:
        response = client.chat.completions.create(
            model=model,
            max_tokens=2000,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
        )
        raw = response.choices[0].message.content.strip()

        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        entries = json.loads(raw)
        if not isinstance(entries, list):
            return []
        return entries
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse failed for section '{section}': {e}\nRaw: {raw[:200]}")
        return []
    except Exception as e:
        logger.error(f"Extraction failed for section '{section}': {e}")
        return []


def run():
    logger.info("LIGHTHOUSE nightly extraction starting")

    conversations = get_conversations(hours=24)
    if not conversations:
        logger.info("No conversations in the last 24h — nothing to extract")
        return

    word_count = len(conversations.split())
    logger.info(f"Loaded {word_count} words of conversation")

    # Truncate if huge (keep ~40k chars — well within context window)
    if len(conversations) > 40000:
        conversations = conversations[-40000:]
        logger.info("Truncated to last 40k chars")

    from relay.lighthouse import write_entry

    total_written = 0
    for target in EXTRACTION_TARGETS:
        section = target["section"]
        logger.info(f"Extracting: {section}")

        entries = extract_entries(conversations, target)
        if not entries:
            logger.info(f"  -> nothing to capture")
            continue

        logger.info(f"  -> {len(entries)} entries extracted")
        for entry in entries:
            title = entry.get("title", "untitled")
            content = entry.get("content", "")
            tags = entry.get("tags", [])

            if not content:
                continue

            # Mark as nightly extraction
            if "nightly" not in tags:
                tags.append("nightly")

            result = write_entry(section, title, content, tags)
            if result.get("success"):
                logger.info(f"  -> saved: {result['filename']}")
                total_written += 1
            else:
                logger.error(f"  -> write failed: {result.get('error')}")

    logger.info(f"LIGHTHOUSE extraction complete — {total_written} entries written")


if __name__ == "__main__":
    run()
