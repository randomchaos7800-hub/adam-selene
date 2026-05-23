import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from relay.snapshots import SnapshotManager


class SnapshotTestCase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.memory = Path(self.tempdir.name)
        (self.memory / "prompts").mkdir(parents=True)
        (self.memory / "prompts" / "system.txt").write_text("system prompt content")
        (self.memory / "prompts" / "user.txt").write_text("user prompt content")
        (self.memory / "constitution").mkdir(parents=True)
        (self.memory / "constitution" / "rules.json").write_text('{"rule": "value"}')
        (self.memory / "config.json").write_text('{"key": "value"}')
        (self.memory / "system_prompt.txt").write_text("main system prompt")
        (self.memory / "facts.json").write_text('{"fact": "sacred"}')
        (self.memory / "entity_data.json").write_text('{"entity": "data"}')
        (self.memory / "sessions.db").write_text("database content")
        (self.memory / "spend.db").write_text("spending database")
        self.manager = SnapshotManager(self.memory)

    def tearDown(self):
        self.tempdir.cleanup()

    def _snapshot_dir(self, name: str) -> Path:
        return self.memory / "snapshots" / name

    def test_create_snapshot_copies_expected_content(self):
        name = self.manager.create_snapshot(trigger="heartbeat")
        snapshot_dir = self._snapshot_dir(name)
        self.assertTrue((snapshot_dir / "prompts" / "system.txt").exists())
        self.assertTrue((snapshot_dir / "constitution" / "rules.json").exists())
        self.assertTrue((snapshot_dir / "config.json").exists())
        self.assertTrue((snapshot_dir / "system_prompt.txt").exists())
        self.assertFalse((snapshot_dir / "facts.json").exists())
        self.assertFalse((snapshot_dir / "entity_data.json").exists())
        self.assertFalse((snapshot_dir / "sessions.db").exists())
        self.assertFalse((snapshot_dir / "spend.db").exists())

        metadata = json.loads((snapshot_dir / "metadata.json").read_text())
        self.assertEqual(metadata["timestamp"], name)
        self.assertEqual(metadata["trigger"], "heartbeat")
        datetime.fromisoformat(metadata["created_at"])

    def test_list_snapshots_reports_metadata_and_size(self):
        name1 = self.manager.create_snapshot(trigger="manual")
        name2 = self.manager.create_snapshot(trigger="heartbeat")
        snapshots = self.manager.list_snapshots()
        self.assertEqual(len(snapshots), 2)
        self.assertEqual([s["name"] for s in snapshots], sorted([s["name"] for s in snapshots]))
        self.assertIn(name1, {s["name"] for s in snapshots})
        self.assertIn(name2, {s["name"] for s in snapshots})
        self.assertTrue(all("size_bytes" in s and s["size_bytes"] > 0 for s in snapshots))

    def test_restore_snapshot_restores_tracked_files_only(self):
        name = self.manager.create_snapshot()
        (self.memory / "config.json").write_text('{"modified": "value"}')
        (self.memory / "prompts" / "system.txt").write_text("modified content")
        (self.memory / "newfile.txt").write_text("new content")

        self.assertTrue(self.manager.restore_snapshot(name))
        self.assertEqual((self.memory / "config.json").read_text(), '{"key": "value"}')
        self.assertEqual((self.memory / "prompts" / "system.txt").read_text(), "system prompt content")
        self.assertTrue((self.memory / "newfile.txt").exists())

    def test_restore_snapshot_returns_false_for_missing_snapshot(self):
        self.assertFalse(self.manager.restore_snapshot("nonexistent"))

    def test_prune_old_snapshots_deletes_only_old_entries(self):
        old_name = self.manager.create_snapshot()
        time.sleep(0.1)
        new_name = self.manager.create_snapshot()

        old_meta = self._snapshot_dir(old_name) / "metadata.json"
        metadata = json.loads(old_meta.read_text())
        metadata["created_at"] = (datetime.now() - timedelta(hours=72)).isoformat()
        old_meta.write_text(json.dumps(metadata))

        deleted = self.manager.prune_old_snapshots(max_age_hours=48)
        self.assertEqual(deleted, 1)
        self.assertFalse(self._snapshot_dir(old_name).exists())
        self.assertTrue(self._snapshot_dir(new_name).exists())

    def test_init_creates_snapshots_directory(self):
        self.assertTrue((self.memory / "snapshots").exists())


if __name__ == "__main__":
    unittest.main()
