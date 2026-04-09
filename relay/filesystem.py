"""Safe filesystem access for the agent.

Read-only access to project files with path validation.
"""

import logging
import os
from pathlib import Path
from typing import Optional

from relay import config

logger = logging.getLogger(__name__)

# Allowed base paths (whitelist)
ALLOWED_PATHS = [
    config.project_root(),
    config.memory_root(),
]


def _validate_path(path: str) -> Optional[Path]:
    """Validate path is within allowed directories.

    Args:
        path: Path to validate

    Returns:
        Resolved Path object if valid, None if invalid
    """
    try:
        # Resolve to absolute path
        resolved = Path(path).resolve()

        # Check if within allowed paths
        for allowed in ALLOWED_PATHS:
            try:
                resolved.relative_to(allowed)
                return resolved
            except ValueError:
                continue

        logger.warning(f"Path not allowed: {path}")
        return None

    except Exception as e:
        logger.error(f"Path validation error: {e}")
        return None


def list_files(path: str = None) -> dict:
    """List files in a directory.

    Args:
        path: Directory path to list (defaults to project root)

    Returns:
        Dictionary with file listing
    """
    if path is None:
        path = str(config.project_root())
    try:
        validated_path = _validate_path(path)
        if not validated_path:
            return {
                "success": False,
                "error": f"Access denied: {path} is outside allowed directories"
            }

        if not validated_path.exists():
            return {
                "success": False,
                "error": f"Path does not exist: {path}"
            }

        if not validated_path.is_dir():
            return {
                "success": False,
                "error": f"Not a directory: {path}"
            }

        # List directory contents
        items = []
        for item in sorted(validated_path.iterdir()):
            item_type = "dir" if item.is_dir() else "file"
            size = item.stat().st_size if item.is_file() else None

            items.append({
                "name": item.name,
                "type": item_type,
                "size": size,
                "path": str(item)
            })

        logger.info(f"Listed {len(items)} items in {path}")

        return {
            "success": True,
            "path": str(validated_path),
            "items": items,
            "count": len(items)
        }

    except Exception as e:
        logger.error(f"Failed to list directory: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def read_file(path: str, max_lines: int = 500) -> dict:
    """Read a file's contents.

    Args:
        path: File path to read
        max_lines: Maximum lines to read (default: 500)

    Returns:
        Dictionary with file contents
    """
    try:
        validated_path = _validate_path(path)
        if not validated_path:
            return {
                "success": False,
                "error": f"Access denied: {path} is outside allowed directories"
            }

        if not validated_path.exists():
            return {
                "success": False,
                "error": f"File does not exist: {path}"
            }

        if not validated_path.is_file():
            return {
                "success": False,
                "error": f"Not a file: {path}"
            }

        # Read file
        with open(validated_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()

        total_lines = len(lines)

        # Truncate if too long
        if total_lines > max_lines:
            lines = lines[:max_lines]
            content = ''.join(lines)
            truncated = True
        else:
            content = ''.join(lines)
            truncated = False

        logger.info(f"Read {total_lines} lines from {path}")

        return {
            "success": True,
            "path": str(validated_path),
            "content": content,
            "lines": total_lines,
            "truncated": truncated
        }

    except Exception as e:
        logger.error(f"Failed to read file: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def search_files(pattern: str, base_path: str = None, max_results: int = 50) -> dict:
    """Search for files matching a pattern.

    Args:
        pattern: Glob pattern (e.g., "*.py", "**/*.swift")
        base_path: Directory to search in (defaults to project root)
        max_results: Maximum results to return

    Returns:
        Dictionary with matching files
    """
    if base_path is None:
        base_path = str(config.project_root())
    try:
        validated_path = _validate_path(base_path)
        if not validated_path:
            return {
                "success": False,
                "error": f"Access denied: {base_path} is outside allowed directories"
            }

        if not validated_path.exists():
            return {
                "success": False,
                "error": f"Path does not exist: {base_path}"
            }

        # Search for files
        matches = []
        for match in validated_path.glob(pattern):
            if match.is_file():
                matches.append({
                    "name": match.name,
                    "path": str(match),
                    "size": match.stat().st_size
                })

                if len(matches) >= max_results:
                    break

        logger.info(f"Found {len(matches)} files matching '{pattern}' in {base_path}")

        return {
            "success": True,
            "pattern": pattern,
            "base_path": str(validated_path),
            "matches": matches,
            "count": len(matches),
            "truncated": len(matches) >= max_results
        }

    except Exception as e:
        logger.error(f"Search failed: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def file_info(path: str) -> dict:
    """Get information about a file or directory.

    Args:
        path: Path to inspect

    Returns:
        Dictionary with file/directory info
    """
    try:
        validated_path = _validate_path(path)
        if not validated_path:
            return {
                "success": False,
                "error": f"Access denied: {path} is outside allowed directories"
            }

        if not validated_path.exists():
            return {
                "success": False,
                "error": f"Path does not exist: {path}"
            }

        stat = validated_path.stat()

        info = {
            "path": str(validated_path),
            "name": validated_path.name,
            "type": "directory" if validated_path.is_dir() else "file",
            "size": stat.st_size,
            "modified": stat.st_mtime,
            "permissions": oct(stat.st_mode)[-3:]
        }

        if validated_path.is_dir():
            # Count items in directory
            item_count = len(list(validated_path.iterdir()))
            info["item_count"] = item_count

        logger.info(f"Got info for {path}")

        return {
            "success": True,
            "info": info
        }

    except Exception as e:
        logger.error(f"Failed to get file info: {e}")
        return {
            "success": False,
            "error": str(e)
        }
