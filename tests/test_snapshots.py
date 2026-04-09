"""
Tests for SnapshotManager.
"""

import json
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from relay.snapshots import SnapshotManager


@pytest.fixture
def temp_agent_memory():
    """Create a temporary smartagent-memory directory structure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        agent_memory = Path(tmpdir)

        # Create typical agent memory structure
        (agent_memory / "prompts").mkdir(parents=True)
        (agent_memory / "prompts" / "system.txt").write_text("system prompt content")
        (agent_memory / "prompts" / "user.txt").write_text("user prompt content")

        (agent_memory / "constitution").mkdir(parents=True)
        (agent_memory / "constitution" / "rules.json").write_text('{"rule": "value"}')

        (agent_memory / "config.json").write_text('{"key": "value"}')
        (agent_memory / "system_prompt.txt").write_text("main system prompt")

        # Create files that should NOT be included in snapshot
        (agent_memory / "facts.json").write_text('{"fact": "sacred"}')
        (agent_memory / "entity_data.json").write_text('{"entity": "data"}')
        (agent_memory / "sessions.db").write_text("database content")
        (agent_memory / "spend.db").write_text("spending database")

        yield agent_memory


@pytest.fixture
def snapshot_manager(temp_agent_memory):
    """Create a SnapshotManager instance."""
    return SnapshotManager(temp_agent_memory)


class TestCreateSnapshot:
    """Tests for create_snapshot method."""

    def test_create_snapshot_creates_directory(self, snapshot_manager, temp_agent_memory):
        """Test that create_snapshot creates a directory with proper structure."""
        snapshot_name = snapshot_manager.create_snapshot(trigger="manual")

        snapshot_dir = temp_agent_memory / "snapshots" / snapshot_name
        assert snapshot_dir.exists()
        assert snapshot_dir.is_dir()

    def test_create_snapshot_returns_timestamp(self, snapshot_manager):
        """Test that create_snapshot returns timestamp in YYYYMMDD_HHMMSS format."""
        snapshot_name = snapshot_manager.create_snapshot()

        # Verify timestamp format (YYYYMMDD_HHMMSS or YYYYMMDD_HHMMSS_NNN for duplicates)
        parts = snapshot_name.split("_")
        assert len(parts) >= 2
        assert len(parts[0]) == 8  # YYYYMMDD
        assert len(parts[1]) == 6  # HHMMSS
        assert parts[0].isdigit()
        assert parts[1].isdigit()

    def test_create_snapshot_copies_prompts(self, snapshot_manager, temp_agent_memory):
        """Test that prompts directory is copied."""
        snapshot_name = snapshot_manager.create_snapshot()

        snapshot_dir = temp_agent_memory / "snapshots" / snapshot_name
        assert (snapshot_dir / "prompts" / "system.txt").exists()
        assert (snapshot_dir / "prompts" / "user.txt").exists()

        # Verify content
        assert (snapshot_dir / "prompts" / "system.txt").read_text() == "system prompt content"

    def test_create_snapshot_copies_constitution(self, snapshot_manager, temp_agent_memory):
        """Test that constitution directory is copied."""
        snapshot_name = snapshot_manager.create_snapshot()

        snapshot_dir = temp_agent_memory / "snapshots" / snapshot_name
        assert (snapshot_dir / "constitution" / "rules.json").exists()

    def test_create_snapshot_copies_config_files(self, snapshot_manager, temp_agent_memory):
        """Test that config files are copied."""
        snapshot_name = snapshot_manager.create_snapshot()

        snapshot_dir = temp_agent_memory / "snapshots" / snapshot_name
        assert (snapshot_dir / "config.json").exists()
        assert (snapshot_dir / "system_prompt.txt").exists()

    def test_create_snapshot_excludes_facts(self, snapshot_manager, temp_agent_memory):
        """Test that facts.json is NOT included in snapshot."""
        snapshot_name = snapshot_manager.create_snapshot()

        snapshot_dir = temp_agent_memory / "snapshots" / snapshot_name
        assert not (snapshot_dir / "facts.json").exists()

    def test_create_snapshot_excludes_entity_data(self, snapshot_manager, temp_agent_memory):
        """Test that entity_data.json is NOT included in snapshot."""
        snapshot_name = snapshot_manager.create_snapshot()

        snapshot_dir = temp_agent_memory / "snapshots" / snapshot_name
        assert not (snapshot_dir / "entity_data.json").exists()

    def test_create_snapshot_excludes_databases(self, snapshot_manager, temp_agent_memory):
        """Test that database files are NOT included in snapshot."""
        snapshot_name = snapshot_manager.create_snapshot()

        snapshot_dir = temp_agent_memory / "snapshots" / snapshot_name
        assert not (snapshot_dir / "sessions.db").exists()
        assert not (snapshot_dir / "spend.db").exists()

    def test_create_snapshot_creates_metadata(self, snapshot_manager, temp_agent_memory):
        """Test that metadata.json is created with correct content."""
        snapshot_name = snapshot_manager.create_snapshot(trigger="heartbeat")

        snapshot_dir = temp_agent_memory / "snapshots" / snapshot_name
        metadata_path = snapshot_dir / "metadata.json"
        assert metadata_path.exists()

        # Verify metadata content
        metadata = json.loads(metadata_path.read_text())
        assert metadata["timestamp"] == snapshot_name
        assert metadata["trigger"] == "heartbeat"
        assert "created_at" in metadata

    def test_create_snapshot_metadata_iso_timestamp(self, snapshot_manager, temp_agent_memory):
        """Test that metadata created_at is valid ISO format."""
        snapshot_name = snapshot_manager.create_snapshot()

        snapshot_dir = temp_agent_memory / "snapshots" / snapshot_name
        metadata = json.loads((snapshot_dir / "metadata.json").read_text())

        # Verify ISO format can be parsed
        created_at = datetime.fromisoformat(metadata["created_at"])
        assert created_at is not None


class TestListSnapshots:
    """Tests for list_snapshots method."""

    def test_list_snapshots_empty_directory(self, snapshot_manager):
        """Test list_snapshots on empty snapshots directory."""
        snapshots = snapshot_manager.list_snapshots()
        assert snapshots == []

    def test_list_snapshots_single_snapshot(self, snapshot_manager):
        """Test list_snapshots with a single snapshot."""
        snapshot_name = snapshot_manager.create_snapshot(trigger="manual")

        snapshots = snapshot_manager.list_snapshots()
        assert len(snapshots) == 1
        assert snapshots[0]["name"] == snapshot_name
        assert snapshots[0]["trigger"] == "manual"

    def test_list_snapshots_multiple_snapshots(self, snapshot_manager):
        """Test list_snapshots with multiple snapshots."""
        snapshot_name1 = snapshot_manager.create_snapshot(trigger="manual")
        snapshot_name2 = snapshot_manager.create_snapshot(trigger="heartbeat")

        snapshots = snapshot_manager.list_snapshots()
        assert len(snapshots) == 2
        # Verify both snapshot names exist (order may vary due to same-second creation)
        names = {s["name"] for s in snapshots}
        assert snapshot_name1 in names
        assert snapshot_name2 in names

    def test_list_snapshots_includes_size(self, snapshot_manager):
        """Test that list_snapshots includes size_bytes."""
        snapshot_manager.create_snapshot()

        snapshots = snapshot_manager.list_snapshots()
        assert len(snapshots) == 1
        assert "size_bytes" in snapshots[0]
        assert snapshots[0]["size_bytes"] > 0

    def test_list_snapshots_includes_timestamp(self, snapshot_manager):
        """Test that list_snapshots includes timestamp."""
        snapshot_manager.create_snapshot()

        snapshots = snapshot_manager.list_snapshots()
        assert len(snapshots) == 1
        assert "timestamp" in snapshots[0]

    def test_list_snapshots_sorted_by_name(self, snapshot_manager):
        """Test that snapshots are sorted by name."""
        name1 = snapshot_manager.create_snapshot()
        name2 = snapshot_manager.create_snapshot()

        snapshots = snapshot_manager.list_snapshots()
        # Verify all snapshot names are present and sorted
        names = [s["name"] for s in snapshots]
        assert name1 in names
        assert name2 in names
        # Verify sorted order (by directory listing)
        assert names == sorted(names)


class TestRestoreSnapshot:
    """Tests for restore_snapshot method."""

    def test_restore_snapshot_nonexistent(self, snapshot_manager):
        """Test restore_snapshot with nonexistent snapshot."""
        result = snapshot_manager.restore_snapshot("nonexistent")
        assert result is False

    def test_restore_snapshot_copies_files(self, snapshot_manager, temp_agent_memory):
        """Test that restore_snapshot copies files back."""
        # Create snapshot
        snapshot_name = snapshot_manager.create_snapshot()

        # Modify original files
        (temp_agent_memory / "config.json").write_text('{"modified": "value"}')

        # Restore
        result = snapshot_manager.restore_snapshot(snapshot_name)
        assert result is True

        # Verify restored content
        assert (temp_agent_memory / "config.json").read_text() == '{"key": "value"}'

    def test_restore_snapshot_copies_directories(self, snapshot_manager, temp_agent_memory):
        """Test that restore_snapshot copies directories back."""
        # Create snapshot
        snapshot_name = snapshot_manager.create_snapshot()

        # Modify original directory
        (temp_agent_memory / "prompts" / "system.txt").write_text("modified content")

        # Restore
        result = snapshot_manager.restore_snapshot(snapshot_name)
        assert result is True

        # Verify restored content
        assert (temp_agent_memory / "prompts" / "system.txt").read_text() == "system prompt content"

    def test_restore_snapshot_overwrites_existing(self, snapshot_manager, temp_agent_memory):
        """Test that restore_snapshot overwrites existing files."""
        # Create snapshot
        snapshot_name = snapshot_manager.create_snapshot()

        # Create new file that doesn't exist in snapshot
        (temp_agent_memory / "newfile.txt").write_text("new content")

        # Restore
        result = snapshot_manager.restore_snapshot(snapshot_name)
        assert result is True

        # Verify new file still exists (restore doesn't delete things not in snapshot)
        assert (temp_agent_memory / "newfile.txt").exists()

    def test_restore_snapshot_success_return_value(self, snapshot_manager):
        """Test that restore_snapshot returns True on success."""
        snapshot_name = snapshot_manager.create_snapshot()

        result = snapshot_manager.restore_snapshot(snapshot_name)
        assert result is True


class TestPruneOldSnapshots:
    """Tests for prune_old_snapshots method."""

    def test_prune_old_snapshots_empty_directory(self, snapshot_manager):
        """Test prune_old_snapshots on empty directory."""
        deleted = snapshot_manager.prune_old_snapshots()
        assert deleted == 0

    def test_prune_old_snapshots_keeps_recent(self, snapshot_manager):
        """Test that recent snapshots are kept."""
        snapshot_manager.create_snapshot()

        deleted = snapshot_manager.prune_old_snapshots(max_age_hours=48)
        assert deleted == 0

    def test_prune_old_snapshots_deletes_old(self, snapshot_manager, temp_agent_memory):
        """Test that old snapshots are deleted."""
        # Create snapshot
        snapshot_name = snapshot_manager.create_snapshot()

        # Mock the creation time to be old
        snapshot_dir = temp_agent_memory / "snapshots" / snapshot_name
        old_time = (datetime.now() - timedelta(hours=72)).isoformat()

        metadata = json.loads((snapshot_dir / "metadata.json").read_text())
        metadata["created_at"] = old_time
        (snapshot_dir / "metadata.json").write_text(json.dumps(metadata))

        # Prune with 48-hour window
        deleted = snapshot_manager.prune_old_snapshots(max_age_hours=48)
        assert deleted == 1

        # Verify snapshot is deleted
        assert not snapshot_dir.exists()

    def test_prune_old_snapshots_keeps_new_deletes_old(self, snapshot_manager, temp_agent_memory):
        """Test pruning with mix of new and old snapshots."""
        # Create first snapshot
        snapshot_name1 = snapshot_manager.create_snapshot()

        # Wait a bit to ensure different creation times
        time.sleep(0.1)

        # Create second snapshot
        snapshot_name2 = snapshot_manager.create_snapshot()

        # Make first snapshot old
        snapshot_dir1 = temp_agent_memory / "snapshots" / snapshot_name1
        old_time = (datetime.now() - timedelta(hours=72)).isoformat()
        metadata = json.loads((snapshot_dir1 / "metadata.json").read_text())
        metadata["created_at"] = old_time
        (snapshot_dir1 / "metadata.json").write_text(json.dumps(metadata))

        # Prune
        deleted = snapshot_manager.prune_old_snapshots(max_age_hours=48)
        assert deleted == 1

        # Verify first is deleted, second remains
        assert not snapshot_dir1.exists()
        snapshot_dir2 = temp_agent_memory / "snapshots" / snapshot_name2
        assert snapshot_dir2.exists()

    def test_prune_old_snapshots_return_count(self, snapshot_manager, temp_agent_memory):
        """Test that prune_old_snapshots returns correct count."""
        # Create three snapshots with delays
        snapshots = []
        for i in range(3):
            snapshots.append(snapshot_manager.create_snapshot())
            if i < 2:
                time.sleep(0.1)

        # Make all old
        for snapshot_name in snapshots:
            snapshot_dir = temp_agent_memory / "snapshots" / snapshot_name
            old_time = (datetime.now() - timedelta(hours=72)).isoformat()
            metadata = json.loads((snapshot_dir / "metadata.json").read_text())
            metadata["created_at"] = old_time
            (snapshot_dir / "metadata.json").write_text(json.dumps(metadata))

        # Prune
        deleted = snapshot_manager.prune_old_snapshots(max_age_hours=48)
        assert deleted == 3


class TestMemoriesExcluded:
    """Tests to ensure memories are never included in snapshots."""

    def test_original_memories_untouched_after_snapshot(self, snapshot_manager, temp_agent_memory):
        """Test that original memory files remain after snapshot."""
        # Read original memory files
        facts_before = (temp_agent_memory / "facts.json").read_text()
        entity_data_before = (temp_agent_memory / "entity_data.json").read_text()

        # Create snapshot
        snapshot_manager.create_snapshot()

        # Verify files are unchanged
        assert (temp_agent_memory / "facts.json").read_text() == facts_before
        assert (temp_agent_memory / "entity_data.json").read_text() == entity_data_before

    def test_snapshot_does_not_contain_facts(self, snapshot_manager, temp_agent_memory):
        """Test that facts.json never appears in any snapshot."""
        for _ in range(3):
            snapshot_name = snapshot_manager.create_snapshot()
            snapshot_dir = temp_agent_memory / "snapshots" / snapshot_name
            assert not (snapshot_dir / "facts.json").exists()

    def test_snapshot_does_not_contain_session_db(self, snapshot_manager, temp_agent_memory):
        """Test that sessions.db never appears in any snapshot."""
        snapshot_name = snapshot_manager.create_snapshot()
        snapshot_dir = temp_agent_memory / "snapshots" / snapshot_name
        assert not (snapshot_dir / "sessions.db").exists()

    def test_snapshot_does_not_contain_spend_db(self, snapshot_manager, temp_agent_memory):
        """Test that spend.db never appears in any snapshot."""
        snapshot_name = snapshot_manager.create_snapshot()
        snapshot_dir = temp_agent_memory / "snapshots" / snapshot_name
        assert not (snapshot_dir / "spend.db").exists()


class TestInitialization:
    """Tests for SnapshotManager initialization."""

    def test_init_creates_snapshots_directory(self, temp_agent_memory):
        """Test that __init__ creates snapshots directory."""
        snapshots_dir = temp_agent_memory / "snapshots"
        assert not snapshots_dir.exists()

        SnapshotManager(temp_agent_memory)

        assert snapshots_dir.exists()
        assert snapshots_dir.is_dir()

    def test_init_with_existing_snapshots_directory(self, temp_agent_memory):
        """Test initialization with pre-existing snapshots directory."""
        snapshots_dir = temp_agent_memory / "snapshots"
        snapshots_dir.mkdir(parents=True)

        manager = SnapshotManager(temp_agent_memory)
        assert manager.snapshots_dir.exists()
