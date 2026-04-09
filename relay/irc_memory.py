"""Extract learnings from IRC conversations into memory."""

import logging
import re
from pathlib import Path
from typing import Optional

from relay import config
from memory import extraction
from relay.sessions import SessionStore

logger = logging.getLogger(__name__)


def _memory_path() -> Path:
    return config.memory_root()


def clean_irc_conversation(raw_text: str) -> str:
    """Extract actual IRC messages from conversation log.

    Removes instruction prompts and keeps only the IRC content.
    """
    lines = []
    owner = config.owner_name().upper()
    agent = config.agent_name().upper()

    for line in raw_text.split('\n'):
        # Extract IRC messages (format: OWNER: [username in #channel]: message)
        if line.startswith(f'{owner}: [') and ' in #' in line:
            match = re.match(rf'{owner}: \[(.+?) in (#\w+)\]: (.+)', line)
            if match:
                username, channel, message = match.groups()
                lines.append(f"{username} in {channel}: {message}")

        # Keep agent's actual responses (not SKIP)
        elif line.startswith(f'{agent}: ') and not line.startswith(f'{agent}: SKIP'):
            response = line.replace(f'{agent}: ', '')
            lines.append(f"{config.agent_name()}: {response}")

    return '\n'.join(lines)


def extract_irc_learnings(hours: int = 24, channel: Optional[str] = None) -> dict:
    """Review recent IRC conversations and extract learnings to memory.

    Args:
        hours: How many hours back to review (default: 24)
        channel: Specific channel to review, or None for all channels

    Returns:
        Dictionary with extraction results
    """
    try:
        session_store = SessionStore(_memory_path() / "sessions.db")

        # Get IRC conversation text
        if channel:
            user_id = f"irc:{channel}" if not channel.startswith("irc:") else channel
            conversation = session_store.get_conversation_text(user_id, hours=hours)
            channels_reviewed = [channel]
        else:
            # Get all IRC sessions
            all_sessions = session_store.list_sessions()
            irc_sessions = [s for s in all_sessions if s.startswith("irc:")]

            # Combine all IRC conversations
            conversations = []
            channels_reviewed = []
            for session in irc_sessions:
                text = session_store.get_conversation_text(session, hours=hours)
                if text and len(text.strip()) > 50:
                    channel_name = session.replace("irc:", "")
                    conversations.append(f"\n--- {channel_name} ---\n{text}")
                    channels_reviewed.append(channel_name)

            conversation = "\n".join(conversations)

        if not conversation or len(conversation.strip()) < 50:
            return {
                "success": True,
                "message": "No substantial IRC conversations to extract from",
                "channels_reviewed": channels_reviewed,
                "facts_added": 0,
                "entities_added": 0
            }

        # Clean the conversation to extract just IRC messages
        cleaned_conversation = clean_irc_conversation(conversation)

        if not cleaned_conversation or len(cleaned_conversation.strip()) < 50:
            return {
                "success": True,
                "message": "No substantial IRC content after cleaning",
                "channels_reviewed": channels_reviewed,
                "facts_added": 0,
                "entities_added": 0
            }

        logger.info(f"Extracting learnings from {len(channels_reviewed)} IRC channels")
        logger.info(f"Cleaned conversation length: {len(cleaned_conversation)} chars")

        # Add IRC context prefix for extraction
        irc_context = f"""This is IRC conversation data from channels: {', '.join(channels_reviewed)}

Extract interesting topics, concepts, technical discussions, and notable information. Focus on:
- Technical topics and tools discussed
- Interesting concepts or ideas
- Notable events or news mentioned
- Recurring themes or discussions

IRC Conversation:
{cleaned_conversation}
"""

        # Run extraction on cleaned conversation with IRC context
        result = extraction.run(irc_context)

        facts_added = len(result.get("processing", {}).get("added_facts", []))
        entities_added = len(result.get("processing", {}).get("added_entities", []))

        logger.info(f"IRC extraction complete: {facts_added} facts, {entities_added} entities")

        return {
            "success": True,
            "message": f"Extracted {facts_added} facts and {entities_added} entities from IRC",
            "channels_reviewed": channels_reviewed,
            "facts_added": facts_added,
            "entities_added": entities_added,
            "details": result.get("processing", {})
        }

    except Exception as e:
        logger.error(f"Failed to extract IRC learnings: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def search_irc_logs(keyword: str, hours: int = 168) -> dict:
    """Search IRC conversation logs for a keyword.

    Args:
        keyword: Keyword to search for (case-insensitive)
        hours: How many hours back to search (default: 168 = 1 week)

    Returns:
        Dictionary with search results
    """
    try:
        session_store = SessionStore(_memory_path() / "sessions.db")

        # Get all IRC sessions
        all_sessions = session_store.list_sessions()
        irc_sessions = [s for s in all_sessions if s.startswith("irc:")]

        matches = []
        keyword_lower = keyword.lower()

        for session in irc_sessions:
            text = session_store.get_conversation_text(session, hours=hours)
            cleaned = clean_irc_conversation(text)

            # Search for keyword in cleaned text
            lines = cleaned.split('\n')
            for line in lines:
                if keyword_lower in line.lower():
                    channel = session.replace("irc:", "")
                    matches.append({
                        "channel": channel,
                        "line": line.strip()
                    })

        logger.info(f"Found {len(matches)} matches for '{keyword}' in IRC logs")

        if not matches:
            return {
                "success": True,
                "message": f"No matches found for '{keyword}' in IRC logs",
                "matches": []
            }

        # Limit to most recent 50 matches
        matches = matches[-50:]

        return {
            "success": True,
            "message": f"Found {len(matches)} matches for '{keyword}'",
            "matches": matches
        }

    except Exception as e:
        logger.error(f"Failed to search IRC logs: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def read_irc_channel(channel: str, hours: int = 24) -> dict:
    """Read recent conversation from a specific IRC channel.

    Args:
        channel: Channel name (e.g., '#philosophy' or 'philosophy')
        hours: How many hours back to read (default: 24)

    Returns:
        Dictionary with channel conversation
    """
    try:
        if not channel.startswith('#'):
            channel = f"#{channel}"

        session_store = SessionStore(_memory_path() / "sessions.db")
        user_id = f"irc:{channel}"

        text = session_store.get_conversation_text(user_id, hours=hours)
        cleaned = clean_irc_conversation(text)

        if not cleaned:
            return {
                "success": True,
                "message": f"No recent activity in {channel}",
                "conversation": ""
            }

        logger.info(f"Read {len(cleaned)} chars from {channel}")

        return {
            "success": True,
            "message": f"Retrieved {len(cleaned)} characters from {channel}",
            "conversation": cleaned,
            "channel": channel
        }

    except Exception as e:
        logger.error(f"Failed to read IRC channel: {e}")
        return {
            "success": False,
            "error": str(e)
        }
