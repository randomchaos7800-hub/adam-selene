"""Send messages to the owner via Telegram from tools."""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from telegram import Bot

from relay import config
from relay.fact_check import check_claims
from relay.fs_utils import atomic_write_text as _atomic_write_text, exclusive_lock as _exclusive_lock

logger = logging.getLogger(__name__)

# Load environment
PROJECT_ROOT = config.project_root()
SECRETS_PATH = PROJECT_ROOT / "config" / "secrets.env"
load_dotenv(SECRETS_PATH)

CONVERSATION_TIMEOUT_MINUTES = 15


def _state_file() -> Path:
    return config.memory_root() / "conversation_state.json"


def _load_state_unlocked(path: Path) -> dict:
    if not path.exists():
        return {
            "state": "WAITING",
            "last_activity": None,
            "initiation_sent_at": None,
    }
    return json.loads(path.read_text())


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_to_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def get_conversation_state() -> dict:
    """Load conversation state."""
    state_file = _state_file()
    try:
        with _exclusive_lock(state_file):
            return _load_state_unlocked(state_file)
    except Exception as e:
        logger.error(f"Error loading state: {e}")
        return {"state": "WAITING", "last_activity": None, "initiation_sent_at": None}


def save_conversation_state(state: dict):
    """Save conversation state."""
    state_file = _state_file()
    with _exclusive_lock(state_file):
        _atomic_write_text(state_file, json.dumps(state, indent=2))
        os.chmod(state_file, 0o600)


def can_send_message() -> tuple[bool, str]:
    """Check if the agent can send a message (conversation-aware rate limit)."""
    state = get_conversation_state()
    current_state = state.get("state", "WAITING")

    # Check for timeout (conversation ended)
    if current_state == "IN_CONVERSATION" and state.get("last_activity"):
        last_activity = _iso_to_utc(state["last_activity"])
        if _utc_now() - last_activity > timedelta(minutes=CONVERSATION_TIMEOUT_MINUTES):
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
    now = _utc_now().isoformat()
    state["initiation_sent_at"] = now
    state["last_activity"] = now
    save_conversation_state(state)
    logger.info("Initiation sent - state: WAITING_FOR_RESPONSE")


def mark_owner_responded():
    """Mark that the owner responded - conversation is now open."""
    state = get_conversation_state()
    state["state"] = "IN_CONVERSATION"
    state["last_activity"] = _utc_now().isoformat()
    save_conversation_state(state)
    logger.info(f"{config.owner_name()} responded - state: IN_CONVERSATION")


def mark_agent_message_in_conversation():
    """Update activity timestamp during conversation."""
    state = get_conversation_state()
    if state.get("state") == "IN_CONVERSATION":
        state["last_activity"] = _utc_now().isoformat()
        save_conversation_state(state)


async def _send_telegram_message(text: str) -> dict:
    """Send message via Telegram (async)."""
    text = check_claims(text)
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
    state_file = _state_file()
    with _exclusive_lock(state_file):
        state = _load_state_unlocked(state_file)
        current_state = state.get("state", "WAITING")

        if current_state == "IN_CONVERSATION" and state.get("last_activity"):
            last_activity = _iso_to_utc(state["last_activity"])
            if _utc_now() - last_activity > timedelta(minutes=CONVERSATION_TIMEOUT_MINUTES):
                state["state"] = "WAITING"
                state["last_activity"] = None
                current_state = "WAITING"
                _atomic_write_text(state_file, json.dumps(state, indent=2))
                os.chmod(state_file, 0o600)
                logger.info("Conversation timed out - reset to WAITING")

        if current_state == "WAITING":
            state["state"] = "WAITING_FOR_RESPONSE"
            now = _utc_now().isoformat()
            state["initiation_sent_at"] = now
            state["last_activity"] = now
            _atomic_write_text(state_file, json.dumps(state, indent=2))
            os.chmod(state_file, 0o600)
        elif current_state == "WAITING_FOR_RESPONSE":
            return {"success": False, "error": f"Already sent initiation - waiting for {config.owner_name()} to respond"}

    # Send the message
    result = asyncio.run(_send_telegram_message(text))

    if result["success"]:
        if current_state == "WAITING":
            logger.info(f"{config.agent_name()} sent initiation message: {text[:50]}...")
        elif current_state == "IN_CONVERSATION":
            mark_agent_message_in_conversation()
            logger.info(f"{config.agent_name()} sent message in conversation: {text[:50]}...")
    elif current_state == "WAITING":
        # Release the reserved slot if delivery failed.
        with _exclusive_lock(state_file):
            state = _load_state_unlocked(state_file)
            if state.get("state") == "WAITING_FOR_RESPONSE":
                state["state"] = "WAITING"
                state["initiation_sent_at"] = None
                state["last_activity"] = None
                _atomic_write_text(state_file, json.dumps(state, indent=2))
                os.chmod(state_file, 0o600)

    return result
