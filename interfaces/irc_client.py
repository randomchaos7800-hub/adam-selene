"""IRC interface for SmartAgent.

Connects to configured channels, responds when mentioned.
Run with: python -m interfaces.irc_client
"""

import json
import logging
import os
import re
import sys
from pathlib import Path

import irc.bot
import irc.client
from dotenv import load_dotenv

# Set up paths and load environment
PROJECT_ROOT = Path(__file__).parent.parent
SECRETS_PATH = PROJECT_ROOT / "config" / "secrets.env"

if SECRETS_PATH.exists():
    load_dotenv(SECRETS_PATH, override=True)

sys.path.insert(0, str(PROJECT_ROOT))

from relay import config
from relay.relay import get_relay

logger = logging.getLogger(__name__)

# IRC Configuration -- overridable via environment variables
IRC_SERVER = os.environ.get("IRC_SERVER", "irc.libera.chat")
IRC_PORT = int(os.environ.get("IRC_PORT", "6667"))
IRC_NICKNAME = config.agent_name()

# Channel configuration -- overridable via environment variable
CHANNELS_CONFIG = Path(os.environ.get("IRC_CHANNELS_CONFIG", str(config.memory_root() / "irc_channels.json")))

def get_channels():
    """Load channel list from config file."""
    if CHANNELS_CONFIG.exists():
        try:
            data = json.loads(CHANNELS_CONFIG.read_text())
            channels = data.get("channels", ["#philosophy"])
            logger.info(f"Loaded {len(channels)} channels from config")
            return channels
        except Exception as e:
            logger.error(f"Error loading channel config: {e}")
    return ["#philosophy"]

IRC_CHANNELS = get_channels()


class SmartAgentIRCBot(irc.bot.SingleServerIRCBot):
    """SmartAgent's IRC presence."""

    def __init__(self, api_key):
        """Initialize IRC bot."""
        irc.bot.SingleServerIRCBot.__init__(
            self,
            [(IRC_SERVER, IRC_PORT)],
            IRC_NICKNAME,
            f"{config.agent_name()} - AI reasoning partner"
        )
        self.relay = get_relay()
        logger.info(f"Connecting to {IRC_SERVER}:{IRC_PORT} as {IRC_NICKNAME}")

    def on_welcome(self, connection, event):
        """Join channels on successful connection."""
        logger.info("Connected to server, joining channels...")
        for channel in IRC_CHANNELS:
            connection.join(channel)
            logger.info(f"Joining {channel}")

    def on_pubmsg(self, connection, event):
        """Handle public channel messages."""
        channel = event.target
        sender = event.source.nick
        message = event.arguments[0]

        # Skip messages from ourselves
        if sender == connection.get_nickname():
            return

        logger.debug(f"{channel} <{sender}> {message}")

        try:
            # Build context-aware prompt
            is_mentioned = self._is_mentioned(message)

            # Create a prompt that includes the context
            if is_mentioned:
                prompt = f"[{sender} in {channel} mentioned you]: {message}\n\nRemember: You can use write_memory to save interesting insights or learnings from IRC discussions."
            else:
                prompt = f"[{sender} in {channel}]: {message}\n\nYou're monitoring IRC discussions. Only respond if you have something genuinely insightful to contribute. Most of the time, just observe. If you don't want to respond, say exactly 'SKIP' and nothing else.\n\nTip: Even when you SKIP responding, you can still use write_memory to save interesting insights for later."

            # Use channel as user_id for shared context
            user_id = f"irc:{channel}"
            response = self.relay.respond(prompt, user_id)

            # Don't respond if agent says to skip
            if response.strip().upper() == "SKIP":
                return

            # Send the response
            self._send_response(connection, channel, response)
            logger.info(f"Responded in {channel}: {response[:50]}...")

        except Exception as e:
            logger.error(f"Error generating response: {e}")

    def on_privmsg(self, connection, event):
        """Handle private messages."""
        sender = event.source.nick
        message = event.arguments[0]

        logger.info(f"Private message from {sender}: {message}")

        try:
            user_id = f"irc:pm:{sender}"
            response = self.relay.respond(message, user_id)
            self._send_response(connection, sender, response)

        except Exception as e:
            logger.error(f"Error in PM: {e}")
            connection.privmsg(sender, f"Error: {e}")

    def on_disconnect(self, connection, event):
        """Handle disconnection."""
        logger.warning(f"Disconnected from IRC: {event}")
        self.connection.reconnect()

    def on_nicknameinuse(self, connection, event):
        """Handle nickname already in use."""
        logger.error(f"Nickname '{IRC_NICKNAME}' already in use")
        # Add underscore and try again
        new_nick = IRC_NICKNAME + "_"
        logger.info(f"Trying alternative nickname: {new_nick}")
        connection.nick(new_nick)

    def on_error(self, connection, event):
        """Handle IRC errors."""
        logger.error(f"IRC error: {event}")

    def on_all_raw_messages(self, connection, event):
        """Log all raw IRC messages for debugging."""
        logger.debug(f"IRC: {event.type} - {event.arguments}")

    def _is_mentioned(self, message):
        """Check if the agent is mentioned in the message."""
        # Case-insensitive check for agent name as a word
        pattern = r'\b' + re.escape(config.agent_name().lower()) + r'\b'
        return bool(re.search(pattern, message, re.IGNORECASE))

    def _send_response(self, connection, target, response):
        """Send response, splitting if necessary."""
        response = response.replace('\r', '')
        # IRC line length limit is ~512 bytes
        # Leave room for protocol overhead
        max_length = 400

        if len(response) <= max_length:
            connection.privmsg(target, response)
        else:
            # Split into chunks
            lines = response.split('\n')
            current_chunk = ""

            for line in lines:
                if len(current_chunk) + len(line) + 1 <= max_length:
                    current_chunk += line + "\n"
                else:
                    if current_chunk:
                        connection.privmsg(target, current_chunk.rstrip())
                    current_chunk = line + "\n"

            if current_chunk:
                connection.privmsg(target, current_chunk.rstrip())


def main():
    """Start IRC bot."""
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO
    )

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("Error: OPENROUTER_API_KEY not set")
        sys.exit(1)

    bot = SmartAgentIRCBot(api_key)

    print(f"{config.agent_name()} IRC bot starting...")
    print(f"Connecting to {IRC_SERVER}")
    print(f"Channels: {', '.join(IRC_CHANNELS)}")
    print(f"Will respond when mentioned")
    print("Press Ctrl+C to stop.")

    try:
        bot.start()
    except KeyboardInterrupt:
        print("\nShutting down IRC bot")
        bot.die("Goodbye")


if __name__ == "__main__":
    main()
