"""Send messages to the owner via Telegram from tools."""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from telegram import Bot
from dotenv import load_dotenv

from relay import config

logger = logging.getLogger(__name__)

# Load environment
PROJECT_ROOT = config.project_root()
SECRETS_PATH = PROJECT_ROOT / "config" / "secrets.env"
load_dotenv(SECRETS_PATH)

STATE_FILE = config.memory_root() / "conversation_state.json"
CONVERSATION_TIMEOUT_MINUTES = 15


def get_conversation_state() -> dict:
    """Load conversation state."""
    if not STATE_FILE.exists():
        return {
            "state": "WAITING",
            "last_activity": None,
            "initiation_sent_at": None
        }

    try:
        return json.loads(STATE_FILE.read_text())
    except Exception as e:
        logger.error(f"Error loading state: {e}")
        return {"state": "WAITING", "last_activity": None, "initiation_sent_at": None}


def save_conversation_state(state: dict):
    """Save conversation state."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))
    os.chmod(STATE_FILE, 0o600)


def can_send_message() -> tuple[bool, str]:
    """Check if the agent can send a message (conversation-aware rate limit)."""
    state = get_conversation_state()
    current_state = state.get("state", "WAITING")

    # Check for timeout (conversation ended)
    if current_state == "IN_CONVERSATION" and state.get("last_activity"):
        last_activity = datetime.fromisoformat(state["last_activity"])
        if datetime.utcnow() - last_activity > timedelta(minutes=CONVERSATION_TIMEOUT_MINUTES):
            # Conversation timed out - reset to WAITING
            state["state"] = "WAITING"
            state["last_activity"] = None
            save_conversation_state(state)
            current_state = "WAITING"
            logger.info("Conversation timed out - reset to WAITING")

    if current_state == "WAITING":
        return True, "Can send initiation message"
    elif current_state == "IN_CONVERSATION":
        return True, "In active conversation"
    elif current_state == "WAITING_FOR_RESPONSE":
        return False, f"Already sent initiation - waiting for {config.owner_name()} to respond"

    return False, f"Unknown state: {current_state}"


def mark_initiation_sent():
    """Mark that the agent sent an initiation message."""
    state = get_conversation_state()
    state["state"] = "WAITING_FOR_RESPONSE"
    state["initiation_sent_at"] = datetime.utcnow().isoformat()
    state["last_activity"] = datetime.utcnow().isoformat()
    save_conversation_state(state)
    logger.info("Initiation sent - state: WAITING_FOR_RESPONSE")


def mark_owner_responded():
    """Mark that the owner responded - conversation is now open."""
    state = get_conversation_state()
    state["state"] = "IN_CONVERSATION"
    state["last_activity"] = datetime.utcnow().isoformat()
    save_conversation_state(state)
    logger.info(f"{config.owner_name()} responded - state: IN_CONVERSATION")


def mark_agent_message_in_conversation():
    """Update activity timestamp during conversation."""
    state = get_conversation_state()
    if state.get("state") == "IN_CONVERSATION":
        state["last_activity"] = datetime.utcnow().isoformat()
        save_conversation_state(state)


async def _send_telegram_message(text: str) -> dict:
    """Send message via Telegram (async)."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        return {"success": False, "error": "No Telegram token configured"}

    # Get owner's user ID from settings
    settings_path = PROJECT_ROOT / "config" / "settings.json"
    if settings_path.exists():
        settings = json.loads(settings_path.read_text())
        owner_telegram_user_id = settings.get("owner_telegram_user_id")
        if not owner_telegram_user_id:
            return {"success": False, "error": f"{config.owner_name()}'s Telegram user ID not configured"}
    else:
        return {"success": False, "error": "Settings file not found"}

    try:
        bot = Bot(token=token)
        await bot.send_message(chat_id=owner_telegram_user_id, text=text)
        return {"success": True, "message": f"Message sent to {config.owner_name()}"}
    except Exception as e:
        logger.error(f"Failed to send Telegram message: {e}")
        return {"success": False, "error": str(e)}


def send_message_to_owner(text: str) -> dict:
    """Send a message to the owner via Telegram. Conversation-aware rate limiting."""
    # Check rate limit
    can_send, reason = can_send_message()
    if not can_send:
        return {"success": False, "error": reason}

    state = get_conversation_state()
    current_state = state.get("state", "WAITING")

    # Send the message
    result = asyncio.run(_send_telegram_message(text))

    if result["success"]:
        # Update state based on current conversation state
        if current_state == "WAITING":
            mark_initiation_sent()
            logger.info(f"{config.agent_name()} sent initiation message: {text[:50]}...")
        elif current_state == "IN_CONVERSATION":
            mark_agent_message_in_conversation()
            logger.info(f"{config.agent_name()} sent message in conversation: {text[:50]}...")

    return result
