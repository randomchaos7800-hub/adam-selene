"""Slack interface for Adam Selene.

Handles incoming messages via Socket Mode and posts responses back.
Place at: interfaces/slack_interface.py (alongside discord_interface.py, telegram.py)
Run with: python -m interfaces.slack_interface
"""

import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

# Set up paths and load environment
PROJECT_ROOT = Path(__file__).parent.parent
SECRETS_PATH = PROJECT_ROOT / "config" / "secrets.env"
SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.json"

if SECRETS_PATH.exists():
    load_dotenv(SECRETS_PATH, override=True)

sys.path.insert(0, str(PROJECT_ROOT))

from relay import config
from relay.relay import get_relay
from relay.telegram_sender import mark_owner_responded

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SLACK_CHUNK_SIZE = 3900  # Slack limit is ~4000; leave margin


def load_settings() -> dict:
    """Load settings from config file."""
    if SETTINGS_PATH.exists():
        return json.loads(SETTINGS_PATH.read_text())
    return {}


SETTINGS = load_settings()
SLACK_CHANNEL_ID: str | None = SETTINGS.get("slack_channel_id")
ALLOWED_SLACK_USERS: set[str] = set(SETTINGS.get("allowed_slack_users", []))

# Load tokens from environment
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_APP_TOKEN = os.environ.get("SLACK_APP_TOKEN", "")

if not SLACK_BOT_TOKEN or not SLACK_APP_TOKEN:
    logger.error("SLACK_BOT_TOKEN or SLACK_APP_TOKEN not set in environment")
    sys.exit(1)


def chunk_text(text: str, size: int = SLACK_CHUNK_SIZE) -> list[str]:
    """Split text into chunks at paragraph breaks where possible."""
    if len(text) <= size:
        return [text]

    chunks = []
    while text:
        if len(text) <= size:
            chunks.append(text)
            break
        # Prefer splitting at a paragraph break
        split_at = text.rfind("\n\n", 0, size)
        if split_at == -1:
            split_at = text.rfind("\n", 0, size)
        if split_at == -1:
            split_at = size
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip()

    return chunks


# Create Slack app with Socket Mode
app = App(token=SLACK_BOT_TOKEN, signing_secret=os.environ.get("SLACK_SIGNING_SECRET", ""))


@app.event({"type": "message", "subtype": "message_changed"})
@app.event({"type": "message", "subtype": "message_deleted"})
@app.event({"type": "message", "subtype": "file_share"})
def handle_message_subtype_events(ack):
    """Silently acknowledge message subtypes we don't process."""
    ack()


@app.message()
def handle_message(ack, message, say, logger):
    """Handle incoming Slack messages."""
    ack()

    # Ignore bot messages and thread replies to bot (unless we want to allow threading)
    if message.get("bot_id"):
        return
    if message.get("subtype") == "bot_message":
        return

    # Extract channel and user info
    channel_id = message.get("channel", "")
    user_id = message.get("user", "")

    # If configured to only listen in specific channel, check it
    if SLACK_CHANNEL_ID and channel_id != SLACK_CHANNEL_ID:
        return

    # Authorization check
    if ALLOWED_SLACK_USERS and user_id not in ALLOWED_SLACK_USERS:
        logger.warning(f"Unauthorized Slack user: {user_id}")
        return

    msg_text = (message.get("text") or "").strip()
    if not msg_text:
        return

    logger.info(f"[Slack] <{user_id}> {msg_text[:80]}")

    user_id_str = config.owner_user_id()  # canonical ID -- maps all platforms to same session

    # Mark that owner responded (conversation state tracking)
    mark_owner_responded()

    # Get response from relay
    try:
        relay = get_relay()
        response = relay.respond(msg_text, user_id_str, images=None)
    except Exception as e:
        logger.error(f"Error getting response: {e}")
        say(text=f"Error: {e}")
        return

    chunks = chunk_text(response)
    for chunk in chunks:
        say(text=chunk)

    logger.info(f"[Slack] Response sent ({len(response)} chars)")


def main():
    """Start Slack Socket Mode handler."""
    logger.info(f"Starting Slack interface for {config.agent_name()}...")
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()


if __name__ == "__main__":
    main()
