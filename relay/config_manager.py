"""Safe config management for the agent.

High-level tools for the agent to manage its own configuration without bash injection risks.
"""

import json
import logging
import subprocess
from pathlib import Path
from typing import Optional

from relay import config

logger = logging.getLogger(__name__)

# Config paths
SETTINGS_FILE = config.project_root() / "config" / "settings.json"


def read_my_config() -> dict:
    """Read the agent's current configuration safely.

    Returns:
        Dictionary with config data
    """
    try:
        if not SETTINGS_FILE.exists():
            return {
                "success": True,
                "message": "No settings file found, using defaults",
                "config": {}
            }

        with open(SETTINGS_FILE, 'r') as f:
            cfg = json.load(f)

        logger.info("Read agent config")

        return {
            "success": True,
            "config": cfg,
            "file_path": str(SETTINGS_FILE)
        }

    except Exception as e:
        logger.error(f"Failed to read config: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def set_default_model(model_name: str) -> dict:
    """Change the agent's default model with validation.

    Args:
        model_name: Model to use ("haiku", "sonnet", or "opus")

    Returns:
        Success/error dict
    """
    try:
        # Validate input - CRITICAL for security
        model_name = model_name.lower().strip()
        if model_name not in VALID_MODELS:
            return {
                "success": False,
                "error": f"Invalid model '{model_name}'. Must be one of: {', '.join(VALID_MODELS)}"
            }

        # Read current config
        cfg = {}
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE, 'r') as f:
                cfg = json.load(f)

        # Update model
        old_model = cfg.get("default_model", "sonnet")
        cfg["default_model"] = model_name

        # Write config
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(cfg, f, indent=2)

        logger.info(f"Changed default model: {old_model} -> {model_name}")

        return {
            "success": True,
            "message": f"Default model changed from {old_model} to {model_name}",
            "old_model": old_model,
            "new_model": model_name,
            "note": f"Restart required to take effect. Use restart_agent_service() to apply changes."
        }

    except Exception as e:
        logger.error(f"Failed to set model: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def update_config_setting(key: str, value) -> dict:
    """Update a specific config setting.

    Args:
        key: Config key (validated against whitelist)
        value: New value

    Returns:
        Success/error dict
    """
    # Whitelist of allowed config keys
    ALLOWED_KEYS = {
        "default_model": lambda v: v in VALID_MODELS,
        "extraction_timeout": lambda v: isinstance(v, int) and 0 < v < 3600,
        "heartbeat_idle_minutes": lambda v: isinstance(v, int) and 0 < v < 1440,
        "verbose_logging": lambda v: isinstance(v, bool)
    }

    try:
        if key not in ALLOWED_KEYS:
            return {
                "success": False,
                "error": f"Key '{key}' not allowed. Allowed keys: {', '.join(ALLOWED_KEYS.keys())}"
            }

        # Validate value
        validator = ALLOWED_KEYS[key]
        if not validator(value):
            return {
                "success": False,
                "error": f"Invalid value for '{key}'"
            }

        # Read current config
        cfg = {}
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE, 'r') as f:
                cfg = json.load(f)

        # Update setting
        old_value = cfg.get(key)
        cfg[key] = value

        # Write config
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(cfg, f, indent=2)

        logger.info(f"Updated config: {key} = {value}")

        return {
            "success": True,
            "message": f"Updated {key}: {old_value} -> {value}",
            "key": key,
            "old_value": old_value,
            "new_value": value
        }

    except Exception as e:
        logger.error(f"Failed to update config: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def restart_agent_service() -> dict:
    """Restart the agent's main service to apply config changes.

    Returns:
        Success/error dict
    """
    service_name = config.agent_service_name()
    try:
        # Use systemctl restart - no user input in command
        result = subprocess.run(
            ["systemctl", "--user", "restart", service_name],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode == 0:
            logger.info(f"{service_name} restarted successfully")
            return {
                "success": True,
                "message": f"{service_name} restarted. Config changes are now active."
            }
        else:
            logger.error(f"Service restart failed: {result.stderr}")
            return {
                "success": False,
                "error": f"Restart failed: {result.stderr}"
            }

    except subprocess.TimeoutExpired:
        logger.error("Service restart timed out")
        return {
            "success": False,
            "error": "Restart command timed out"
        }
    except Exception as e:
        logger.error(f"Failed to restart service: {e}")
        return {
            "success": False,
            "error": str(e)
        }
