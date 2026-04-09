"""Central configuration for SmartAgent.

All paths and names flow from settings.json and .env.
No hardcoded personal data anywhere in the codebase.
"""

import json
import os
from pathlib import Path
from functools import lru_cache

_PROJECT_ROOT = Path(__file__).parent.parent
_SETTINGS_PATH = _PROJECT_ROOT / "config" / "settings.json"


@lru_cache(maxsize=1)
def load_settings() -> dict:
    """Load settings.json. Cached after first call."""
    if _SETTINGS_PATH.exists():
        return json.loads(_SETTINGS_PATH.read_text())
    return {}


def reload_settings() -> dict:
    """Force reload settings (clears cache)."""
    load_settings.cache_clear()
    return load_settings()


def project_root() -> Path:
    """Root directory of the SmartAgent installation."""
    return _PROJECT_ROOT


def memory_root() -> Path:
    """Root directory for agent memory storage."""
    settings = load_settings()
    return Path(settings.get("memory_path", "~/smartagent-memory")).expanduser()


def agent_name() -> str:
    """The agent's configured name."""
    return load_settings().get("agent_name", "SmartAgent")


def owner_name() -> str:
    """The owner/operator's name."""
    return load_settings().get("owner_name", "User")


def owner_user_id() -> str:
    """The owner's user ID string (used in session storage)."""
    return load_settings().get("owner_user_id", "owner")


def agent_service_name() -> str:
    """The systemd service name for this agent."""
    return load_settings().get("service_name", "smartagent.service")


def lighthouse_root() -> Path:
    """Root directory for LIGHTHOUSE reasoning journal."""
    return project_root() / "LIGHTHOUSE"


def get_env(key: str, default: str = "") -> str:
    """Get environment variable with fallback."""
    return os.environ.get(key, default)
