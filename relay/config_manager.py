"""Safe config management for the agent.

High-level tools for the agent to manage its own configuration without bash injection risks.
"""

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from relay import config

logger = logging.getLogger(__name__)

MODEL_ALIASES = {
    "haiku": "anthropic/claude-3.5-haiku",
    "sonnet": "anthropic/claude-3.7-sonnet",
    "opus": "anthropic/claude-3-opus",
}

ALLOWED_SETTINGS: dict[str, callable] = {
    "models.main": lambda v: isinstance(v, str) and bool(v.strip()),
    "models.extraction": lambda v: isinstance(v, str) and bool(v.strip()),
    "heartbeat.idle_minutes": lambda v: isinstance(v, int) and 0 < v < 1440,
    "heartbeat.enabled": lambda v: isinstance(v, bool),
    "heartbeat.model_override": lambda v: isinstance(v, str),
    "extraction.idle_timeout_seconds": lambda v: isinstance(v, int) and 0 < v < 3600,
    "extraction.incremental_every_n_messages": lambda v: isinstance(v, int) and 0 < v < 1000,
    "context.max_output_tokens": lambda v: isinstance(v, int) and 128 <= v <= 32768,
    "local.base_url": lambda v: isinstance(v, str) and v.startswith("http"),
    "local.model": lambda v: isinstance(v, str) and bool(v.strip()),
    "openrouter.model": lambda v: isinstance(v, str) and bool(v.strip()),
    "openrouter.fallback_model": lambda v: isinstance(v, str),
    "openrouter.heartbeat_model": lambda v: isinstance(v, str),
    "autoresearch.base_url": lambda v: isinstance(v, str) and v.startswith("http"),
    "service_name": lambda v: isinstance(v, str) and bool(v.strip()),
}


def _settings_file() -> Path:
    return config.project_root() / "config" / "settings.json"


def _load_settings() -> dict:
    settings_file = _settings_file()
    if not settings_file.exists():
        return {}
    return json.loads(settings_file.read_text())


def _write_settings(cfg: dict) -> None:
    settings_file = _settings_file()
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(json.dumps(cfg, indent=2) + "\n")
    config.reload_settings()


def _get_nested(cfg: dict, dotted_key: str):
    current = cfg
    for part in dotted_key.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _set_nested(cfg: dict, dotted_key: str, value) -> None:
    current = cfg
    parts = dotted_key.split(".")
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def read_my_config() -> dict:
    """Read the agent's current configuration safely."""
    try:
        cfg = _load_settings()
        if not cfg:
            return {
                "success": True,
                "message": "No settings file found, using defaults",
                "config": {},
            }

        logger.info("Read agent config")
        return {
            "success": True,
            "config": cfg,
            "file_path": str(_settings_file()),
        }
    except Exception as e:
        logger.error(f"Failed to read config: {e}")
        return {"success": False, "error": str(e)}


def set_default_model(model_name: str) -> dict:
    """Change the primary model while keeping the mirrored OpenRouter setting in sync."""
    try:
        model_name = model_name.strip()
        resolved = MODEL_ALIASES.get(model_name.lower(), model_name)
        if not resolved:
            return {"success": False, "error": "Model name cannot be empty"}

        cfg = _load_settings()
        old_model = _get_nested(cfg, "models.main")

        _set_nested(cfg, "models.main", resolved)
        _set_nested(cfg, "openrouter.model", resolved)
        _write_settings(cfg)

        logger.info(f"Changed default model: {old_model} -> {resolved}")
        return {
            "success": True,
            "message": f"Default model changed from {old_model} to {resolved}",
            "old_model": old_model,
            "new_model": resolved,
            "note": "Restart required to take effect. Use restart_agent_service() to apply changes.",
        }
    except Exception as e:
        logger.error(f"Failed to set model: {e}")
        return {"success": False, "error": str(e)}


def update_config_setting(key: str, value: Any) -> dict:
    """Update a specific nested config setting."""
    try:
        if key not in ALLOWED_SETTINGS:
            return {
                "success": False,
                "error": f"Key '{key}' not allowed. Allowed keys: {', '.join(ALLOWED_SETTINGS.keys())}",
            }

        if not ALLOWED_SETTINGS[key](value):
            return {"success": False, "error": f"Invalid value for '{key}'"}

        cfg = _load_settings()
        old_value = _get_nested(cfg, key)
        _set_nested(cfg, key, value)
        _write_settings(cfg)

        logger.info(f"Updated config: {key} = {value}")
        return {
            "success": True,
            "message": f"Updated {key}: {old_value} -> {value}",
            "key": key,
            "old_value": old_value,
            "new_value": value,
        }
    except Exception as e:
        logger.error(f"Failed to update config: {e}")
        return {"success": False, "error": str(e)}


def restart_agent_service() -> dict:
    """Restart the agent's main service to apply config changes."""
    service_name = config.agent_service_name()
    try:
        result = subprocess.run(
            ["systemctl", "--user", "restart", service_name],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode == 0:
            logger.info(f"{service_name} restarted successfully")
            return {
                "success": True,
                "message": f"{service_name} restarted. Config changes are now active.",
            }

        logger.error(f"Service restart failed: {result.stderr}")
        return {"success": False, "error": f"Restart failed: {result.stderr}"}
    except subprocess.TimeoutExpired:
        logger.error("Service restart timed out")
        return {"success": False, "error": "Restart command timed out"}
    except Exception as e:
        logger.error(f"Failed to restart service: {e}")
        return {"success": False, "error": str(e)}
