"""Snapshot manager for agent memory state.

Creates point-in-time backups of config/prompt files (never facts or databases).
"""

import json
import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from relay import config

logger = logging.getLogger(__name__)

# Files/dirs to include in snapshots
SNAPSHOT_DIRS = ["prompts", "constitution"]
SNAPSHOT_FILES = ["config.json", "system_prompt.txt"]

# Files to NEVER include
EXCLUDED_FILES = {"facts.json", "entity_data.json", "sessions.db", "spend.db", "entities.json"}


class SnapshotManager:
    """Manages point-in-time snapshots of agent config state."""

    def __init__(self, memory_path: Path = None):
        self.memory_path = Path(memory_path) if memory_path else config.memory_root()
        self.snapshots_dir = self.memory_path / "snapshots"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)

    def create_snapshot(self, trigger: str = "manual") -> str:
        """Create a snapshot of current config state.

        Args:
            trigger: What triggered the snapshot (manual, heartbeat, etc.)

        Returns:
            Snapshot name (timestamp string)
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        snapshot_dir = self.snapshots_dir / timestamp

        # Handle duplicate timestamps
        counter = 1
        while snapshot_dir.exists():
            snapshot_dir = self.snapshots_dir / f"{timestamp}_{counter:03d}"
            timestamp = snapshot_dir.name
            counter += 1

        snapshot_dir.mkdir(parents=True)

        # Copy directories
        for dir_name in SNAPSHOT_DIRS:
            src = self.memory_path / dir_name
            if src.exists() and src.is_dir():
                shutil.copytree(src, snapshot_dir / dir_name)

        # Copy individual files (excluding protected ones)
        for file_name in SNAPSHOT_FILES:
            src = self.memory_path / file_name
            if src.exists() and src.is_file() and file_name not in EXCLUDED_FILES:
                shutil.copy2(src, snapshot_dir / file_name)

        # Write metadata
        metadata = {
            "timestamp": timestamp,
            "trigger": trigger,
            "created_at": datetime.now().isoformat()
        }
        (snapshot_dir / "metadata.json").write_text(json.dumps(metadata, indent=2))

        logger.info(f"Snapshot created: {timestamp} (trigger: {trigger})")
        return timestamp

    def list_snapshots(self) -> list:
        """List all snapshots with metadata.

        Returns:
            List of snapshot info dicts, sorted by name.
        """
        snapshots = []

        if not self.snapshots_dir.exists():
            return snapshots

        for entry in sorted(self.snapshots_dir.iterdir()):
            if not entry.is_dir():
                continue

            info = {"name": entry.name}

            # Read metadata if available
            metadata_path = entry / "metadata.json"
            if metadata_path.exists():
                try:
                    metadata = json.loads(metadata_path.read_text())
                    info["trigger"] = metadata.get("trigger", "unknown")
                    info["timestamp"] = metadata.get("created_at", "")
                except Exception:
                    info["trigger"] = "unknown"
                    info["timestamp"] = ""

            # Calculate size
            total_size = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
            info["size_bytes"] = total_size

            snapshots.append(info)

        return snapshots

    def restore_snapshot(self, snapshot_name: str) -> bool:
        """Restore files from a snapshot.

        Args:
            snapshot_name: Name of the snapshot to restore

        Returns:
            True if successful, False if snapshot not found
        """
        snapshot_dir = self.snapshots_dir / snapshot_name

        if not snapshot_dir.exists():
            logger.error(f"Snapshot not found: {snapshot_name}")
            return False

        # Restore directories
        for dir_name in SNAPSHOT_DIRS:
            src = snapshot_dir / dir_name
            dst = self.memory_path / dir_name
            if src.exists() and src.is_dir():
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)

        # Restore individual files
        for item in snapshot_dir.iterdir():
            if item.is_file() and item.name != "metadata.json":
                shutil.copy2(item, self.memory_path / item.name)

        logger.info(f"Snapshot restored: {snapshot_name}")
        return True

    def prune_old_snapshots(self, max_age_hours: int = 48) -> int:
        """Delete snapshots older than max_age_hours.

        Args:
            max_age_hours: Maximum age in hours before pruning

        Returns:
            Number of snapshots deleted
        """
        if not self.snapshots_dir.exists():
            return 0

        cutoff = datetime.now() - timedelta(hours=max_age_hours)
        deleted = 0

        for entry in list(self.snapshots_dir.iterdir()):
            if not entry.is_dir():
                continue

            metadata_path = entry / "metadata.json"
            if metadata_path.exists():
                try:
                    metadata = json.loads(metadata_path.read_text())
                    created_at = datetime.fromisoformat(metadata["created_at"])
                    if created_at < cutoff:
                        shutil.rmtree(entry)
                        deleted += 1
                        logger.info(f"Pruned snapshot: {entry.name}")
                except Exception as e:
                    logger.warning(f"Could not parse snapshot metadata {entry.name}: {e}")

        return deleted
