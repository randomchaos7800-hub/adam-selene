"""Explore IRC channels and manage channel membership."""

import json
import logging
import socket
import subprocess
import time
from pathlib import Path

from relay import config

logger = logging.getLogger(__name__)

IRC_SERVER = "irc.efnet.org"
IRC_PORT = 6667


def _scout_nickname() -> str:
    return f"{config.agent_name()}Scout"


# Config file for active channels
def _channels_config() -> Path:
    return config.memory_root() / "irc_channels.json"


def list_channels(min_users: int = 10, limit: int = 50) -> dict:
    """Query IRC server for active channels.

    Args:
        min_users: Minimum users required to list channel
        limit: Maximum number of channels to return

    Returns:
        Dictionary with channel list and metadata
    """
    nickname = _scout_nickname()
    try:
        # Connect to IRC server
        irc = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        irc.settimeout(30)
        irc.connect((IRC_SERVER, IRC_PORT))

        # Send IRC handshake
        irc.send(f"NICK {nickname}\r\n".encode())
        irc.send(f"USER {nickname} 0 * :Channel Scout\r\n".encode())

        # Wait for welcome
        time.sleep(2)

        # Request channel list
        irc.send(b"LIST\r\n")

        # Collect channel data
        channels = []
        buffer = ""
        start_time = time.time()

        while time.time() - start_time < 25:
            try:
                data = irc.recv(4096).decode('utf-8', errors='ignore')
                if not data:
                    break

                buffer += data
                lines = buffer.split('\r\n')
                buffer = lines[-1]

                for line in lines[:-1]:
                    # Parse channel list responses (322 numeric)
                    if ' 322 ' in line:
                        parts = line.split()
                        if len(parts) >= 5:
                            channel_name = parts[3]
                            try:
                                user_count = int(parts[4])
                            except ValueError:
                                continue
                            topic = ' '.join(parts[5:])[1:] if len(parts) > 5 else ""

                            if user_count >= min_users:
                                channels.append({
                                    "name": channel_name,
                                    "users": user_count,
                                    "topic": topic
                                })

                    # End of list
                    elif ' 323 ' in line:
                        logger.info("Channel list complete")
                        break

            except socket.timeout:
                break

        irc.send(b"QUIT\r\n")
        irc.close()

        # Sort by user count
        channels.sort(key=lambda x: x['users'], reverse=True)
        channels = channels[:limit]

        logger.info(f"Found {len(channels)} channels with {min_users}+ users")

        return {
            "success": True,
            "channels": channels,
            "total": len(channels)
        }

    except Exception as e:
        logger.error(f"Failed to list channels: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def get_active_channels() -> list:
    """Get list of channels the agent is currently monitoring."""
    channels_config = _channels_config()
    if channels_config.exists():
        try:
            data = json.loads(channels_config.read_text())
            return data.get("channels", ["#philosophy"])
        except Exception:
            pass
    return ["#philosophy"]


def update_channels(channels: list) -> dict:
    """Update which channels the agent should join.

    Args:
        channels: List of channel names (e.g., ["#philosophy", "#ai"])

    Returns:
        Success/error dict
    """
    try:
        # Validate channels
        valid_channels = []
        for ch in channels:
            if not ch.startswith('#'):
                ch = f"#{ch}"
            valid_channels.append(ch)

        # Save to config
        channels_config = _channels_config()
        channels_config.parent.mkdir(parents=True, exist_ok=True)
        channels_config.write_text(json.dumps({
            "channels": valid_channels,
            "updated": time.time()
        }, indent=2))

        logger.info(f"Updated channel list: {valid_channels}")

        service_name = config.agent_service_name()
        return {
            "success": True,
            "message": f"Updated to monitor {len(valid_channels)} channels: {', '.join(valid_channels)}",
            "note": f"Restart IRC bot to apply changes (systemctl restart {service_name})"
        }

    except Exception as e:
        logger.error(f"Failed to update channels: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def restart_irc_bot() -> dict:
    """Restart the IRC bot service to apply channel changes.

    Returns:
        Success/error dict
    """
    service_name = config.load_settings().get("irc_service_name", "smartagent-irc.service")
    try:
        # Restart the systemd service
        result = subprocess.run(
            ["systemctl", "--user", "restart", service_name],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode == 0:
            logger.info("IRC bot service restarted successfully")
            return {
                "success": True,
                "message": "IRC bot restarted. Joining new channels now."
            }
        else:
            logger.error(f"Failed to restart IRC bot: {result.stderr}")
            return {
                "success": False,
                "error": f"Restart failed: {result.stderr}"
            }

    except subprocess.TimeoutExpired:
        logger.error("IRC bot restart timed out")
        return {
            "success": False,
            "error": "Restart command timed out"
        }
    except Exception as e:
        logger.error(f"Failed to restart IRC bot: {e}")
        return {
            "success": False,
            "error": str(e)
        }
