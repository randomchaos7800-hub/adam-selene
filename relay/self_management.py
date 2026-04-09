"""Self-management tools for the agent.

Write code, edit files, commit changes, manage vault secrets and credentials.
These are the tools that actually work.
"""

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from relay import config

logger = logging.getLogger(__name__)

AGENT_ROOT = config.project_root()
VAULT = Path.home() / ".vault" / "vault.sh"
BACKUP_SCRIPT = AGENT_ROOT / "scripts" / "backup.sh"
CREDENTIALS_DIR = Path.home() / ".config"


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------

def _validate_agent_path(filepath: str) -> Optional[Path]:
    """Resolve and validate path is within the agent's project root."""
    try:
        p = Path(filepath)
        if not p.is_absolute():
            p = AGENT_ROOT / p
        resolved = p.resolve()
        resolved.relative_to(AGENT_ROOT.resolve())
        return resolved
    except (ValueError, Exception):
        return None


# ---------------------------------------------------------------------------
# File writing / editing
# ---------------------------------------------------------------------------

def write_my_code(filepath: str, content: str) -> dict:
    """Write or overwrite a file within the agent's project root.

    Args:
        filepath: Path relative to the project root or absolute within it
        content: Full file content to write

    Returns:
        Success/error dict
    """
    path = _validate_agent_path(filepath)
    if not path:
        return {"success": False, "error": f"Path not allowed or invalid: {filepath}. Must be within {AGENT_ROOT}"}

    try:
        path.parent.mkdir(parents=True, exist_ok=True)

        # Atomic write via temp file
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False, suffix='.tmp') as f:
            f.write(content)
            tmp = f.name

        Path(tmp).rename(path)
        logger.info(f"Wrote {len(content)} bytes to {path}")
        return {
            "success": True,
            "message": f"Written: {path} ({len(content)} bytes)",
            "path": str(path),
        }
    except Exception as e:
        logger.error(f"write_my_code failed: {e}")
        return {"success": False, "error": str(e)}


def edit_my_code(filepath: str, old_str: str, new_str: str) -> dict:
    """Replace a specific string in a file. Safer than full rewrite for small changes.

    Args:
        filepath: Path to file (relative to the project root or absolute within it)
        old_str: Exact string to find and replace (must be unique in file)
        new_str: Replacement string

    Returns:
        Success/error dict
    """
    path = _validate_agent_path(filepath)
    if not path:
        return {"success": False, "error": f"Path not allowed: {filepath}"}

    if not path.exists():
        return {"success": False, "error": f"File does not exist: {path}"}

    try:
        content = path.read_text(encoding='utf-8')

        count = content.count(old_str)
        if count == 0:
            return {"success": False, "error": "old_str not found in file"}
        if count > 1:
            return {"success": False, "error": f"old_str found {count} times — must be unique. Add more context around it."}

        new_content = content.replace(old_str, new_str, 1)

        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False, suffix='.tmp') as f:
            f.write(new_content)
            tmp = f.name

        Path(tmp).rename(path)
        logger.info(f"Edited {path}")
        return {
            "success": True,
            "message": f"Replaced in {path}",
            "path": str(path),
        }
    except Exception as e:
        logger.error(f"edit_my_code failed: {e}")
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Git operations
# ---------------------------------------------------------------------------

def git_commit(message: str, files: Optional[list] = None) -> dict:
    """Stage and commit changes to the agent's git repo.

    Args:
        message: Commit message
        files: Specific files to stage. If None, stages all tracked modified files.

    Returns:
        Success/error dict
    """
    try:
        if files:
            # Stage specific files (validate each)
            for f in files:
                path = _validate_agent_path(f)
                if not path:
                    return {"success": False, "error": f"File not in agent dir: {f}"}
                subprocess.run(["git", "add", str(path)], cwd=AGENT_ROOT, check=True, capture_output=True)
        else:
            # Stage all tracked modified files
            subprocess.run(["git", "add", "-u"], cwd=AGENT_ROOT, check=True, capture_output=True)

        # Check if there's anything to commit
        status = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=AGENT_ROOT, capture_output=True, text=True
        )
        staged = status.stdout.strip()
        if not staged:
            return {"success": False, "error": "Nothing staged to commit"}

        result = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=AGENT_ROOT, capture_output=True, text=True, timeout=30
        )

        if result.returncode == 0:
            logger.info(f"Committed: {message}")
            return {
                "success": True,
                "message": f"Committed: {message}",
                "files": staged.splitlines(),
                "output": result.stdout.strip(),
            }
        else:
            return {"success": False, "error": result.stderr.strip() or result.stdout.strip()}

    except Exception as e:
        logger.error(f"git_commit failed: {e}")
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Backup operations
# ---------------------------------------------------------------------------

def backup_myself() -> dict:
    """Run the agent's backup script to create a snapshot."""
    try:
        if not BACKUP_SCRIPT.exists():
            return {"success": False, "error": f"Backup script not found: {BACKUP_SCRIPT}"}

        result = subprocess.run(
            ["bash", str(BACKUP_SCRIPT)],
            capture_output=True, text=True, timeout=120
        )

        if result.returncode == 0:
            return {"success": True, "message": "Backup completed", "output": result.stdout[-500:]}
        else:
            return {"success": False, "error": result.stderr[-300:] or result.stdout[-300:]}

    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Backup timed out after 120s"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def list_backups() -> dict:
    """List available backups on the backup drive."""
    backup_dir = Path("/mnt/backup")
    agent_name = config.agent_name().lower()
    try:
        if not backup_dir.exists():
            return {"success": False, "error": "Backup drive not mounted at /mnt/backup"}

        agent_backups = list(backup_dir.glob(f"{agent_name}*.tar*")) + list(backup_dir.glob(f"{agent_name}/"))
        entries = []
        for p in sorted(agent_backups, key=lambda x: x.stat().st_mtime, reverse=True)[:10]:
            stat = p.stat()
            entries.append({
                "name": p.name,
                "path": str(p),
                "size_mb": round(stat.st_size / 1e6, 1),
                "modified": stat.st_mtime,
            })

        return {"success": True, "backups": entries, "count": len(entries)}
    except Exception as e:
        return {"success": False, "error": str(e)}


def restore_from_backup(backup_path: str) -> dict:
    """Restore the agent from a backup. Use list_backups() first to find valid paths."""
    try:
        p = Path(backup_path).resolve()
        if not str(p).startswith("/mnt/backup"):
            return {"success": False, "error": "Restore path must be within /mnt/backup"}
        if not p.exists():
            return {"success": False, "error": f"Backup not found: {p}"}

        return {
            "success": False,
            "error": f"Restore requires {config.owner_name()}'s confirmation. Tell them which backup you want to restore and why.",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Vault / credentials
# ---------------------------------------------------------------------------

def vault_get(key: str) -> dict:
    """Read a secret from the vault.

    Args:
        key: Vault key name (e.g. 'openrouter_api_key')

    Returns:
        Dict with value (not logged)
    """
    key = key.strip()
    if not key or not key.replace('_', '').replace('-', '').isalnum():
        return {"success": False, "error": "Invalid key name"}

    try:
        result = subprocess.run(
            [str(VAULT), "get", key],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            value = result.stdout.strip()
            if value:
                return {"success": True, "key": key, "value": value}
            else:
                return {"success": False, "error": f"Key '{key}' not found in vault"}
        else:
            return {"success": False, "error": f"Vault error: {result.stderr.strip()}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def vault_set(key: str, value: str) -> dict:
    """Store a secret in the vault.

    Args:
        key: Vault key name
        value: Secret value

    Returns:
        Success/error dict
    """
    key = key.strip()
    if not key or not key.replace('_', '').replace('-', '').isalnum():
        return {"success": False, "error": "Invalid key name — use lowercase letters, numbers, underscores only"}

    try:
        result = subprocess.run(
            [str(VAULT), "set", key, value],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            logger.info(f"vault_set: stored key '{key}'")
            return {"success": True, "message": f"Stored '{key}' in vault"}
        else:
            return {"success": False, "error": result.stderr.strip() or "Vault set failed"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def store_credential(service: str, data: dict) -> dict:
    """Store credentials for a service in ~/.config/{service}/credentials.json
    AND in the vault for persistence.

    Args:
        service: Service name (e.g. 'github', 'openrouter')
        data: Dict of key->value pairs to store

    Returns:
        Success/error dict
    """
    service = service.strip().lower().replace(' ', '_')
    if not service or not service.replace('_', '').replace('-', '').isalnum():
        return {"success": False, "error": "Invalid service name"}

    try:
        cred_dir = CREDENTIALS_DIR / service
        cred_dir.mkdir(parents=True, exist_ok=True)
        cred_file = cred_dir / "credentials.json"

        # Merge with existing if present
        existing = {}
        if cred_file.exists():
            try:
                existing = json.loads(cred_file.read_text())
            except Exception:
                pass

        existing.update(data)
        cred_file.write_text(json.dumps(existing, indent=2))
        cred_file.chmod(0o600)

        # Also store each key in vault
        for k, v in data.items():
            vault_key = f"{service}_{k}"
            vault_set(vault_key, str(v))

        logger.info(f"Stored credentials for {service}")
        return {
            "success": True,
            "message": f"Credentials for '{service}' stored at {cred_file}",
            "keys_stored": list(data.keys()),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def read_credential(service: str) -> dict:
    """Read credentials for a service from ~/.config/{service}/credentials.json.

    Args:
        service: Service name

    Returns:
        Dict with credentials data
    """
    service = service.strip().lower()
    cred_file = CREDENTIALS_DIR / service / "credentials.json"

    try:
        if cred_file.exists():
            data = json.loads(cred_file.read_text())
            return {"success": True, "service": service, "credentials": data}
        else:
            return {"success": False, "error": f"No credentials found for '{service}' at {cred_file}"}
    except Exception as e:
        return {"success": False, "error": str(e)}
