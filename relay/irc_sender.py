"""Send messages to IRC channels."""

import json
import logging
import socket
import time
from pathlib import Path

from relay import config

logger = logging.getLogger(__name__)

IRC_NETWORKS = {
    "efnet": {"server": "irc.efnet.org", "port": 6667, "channels_config": "irc_channels.json"},
    "libera": {"server": "irc.libera.chat", "port": 6667, "channels_config": "irc_channels_libera.json"},
}


def _irc_nickname() -> str:
    return config.agent_name()


def send_irc_message(channel: str, message: str, network: str = "efnet") -> dict:
    """Send a message to an IRC channel.

    Args:
        channel: Channel name (e.g., "#ai", "#llm")
        message: Message text to send
        network: IRC network key (default: "efnet")

    Returns:
        Success/error dict
    """
    nickname = _irc_nickname()

    # Resolve network config
    net = IRC_NETWORKS.get(network, IRC_NETWORKS["efnet"])
    irc_server = net["server"]
    irc_port = net["port"]

    # Validate channel
    if not channel.startswith('#'):
        channel = f"#{channel}"

    channels_config = config.memory_root() / net["channels_config"]
    allowed = ["#philosophy"]  # fallback
    if channels_config.exists():
        try:
            allowed = json.loads(channels_config.read_text()).get("channels", allowed)
        except Exception:
            pass
    if channel not in allowed:
        return {
            "success": False,
            "error": f"Not authorized for channel {channel} on {network}. Allowed: {allowed}"
        }

    try:
        # Create socket connection
        irc = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        irc.settimeout(10)

        # Connect to IRC server
        irc.connect((irc_server, irc_port))

        # Send IRC protocol messages
        irc.send(f"NICK {nickname}\r\n".encode())
        irc.send(f"USER {nickname} 0 * :{config.agent_name()} AI\r\n".encode())

        # Wait for welcome message
        time.sleep(2)

        # Join channel
        irc.send(f"JOIN {channel}\r\n".encode())
        time.sleep(1)

        # Send message
        # Split long messages
        max_length = 400
        if len(message) <= max_length:
            irc.send(f"PRIVMSG {channel} :{message}\r\n".encode())
        else:
            # Split by lines or chunks
            lines = message.split('\n')
            for line in lines[:5]:  # Max 5 lines to avoid spam
                if len(line) <= max_length:
                    irc.send(f"PRIVMSG {channel} :{line}\r\n".encode())
                    time.sleep(0.5)

        # Quit
        irc.send(b"QUIT\r\n")
        irc.close()

        logger.info(f"Sent message to {channel}")
        return {
            "success": True,
            "message": f"Message sent to {channel}"
        }

    except Exception as e:
        logger.error(f"Failed to send IRC message: {e}")
        return {
            "success": False,
            "error": str(e)
        }
