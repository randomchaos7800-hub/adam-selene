import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from memory import storage


class TestStorage(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.get_path_patcher = patch("memory.storage.get_memory_path", return_value=self.root)
        self.get_path_patcher.start()
        self.addCleanup(self.get_path_patcher.stop)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_init_memory_normalizes_legacy_entities_schema(self):
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "entities.json").write_text(json.dumps({"entities": {"alice": {"category": "people", "aliases": [], "path": "life/areas/people/alice"}}}))
        storage.init_memory()
        normalized = json.loads((self.root / "entities.json").read_text())
        self.assertIn("alice", normalized)
        self.assertNotIn("entities", normalized)

    def test_add_fact_is_thread_safe(self):
        entity_dir = self.root / "life" / "areas" / "people" / "alice"
        entity_dir.mkdir(parents=True, exist_ok=True)
        (self.root / "entities.json").write_text(json.dumps({
            "alice": {"category": "people", "aliases": [], "path": "life/areas/people/alice"}
        }))
        (entity_dir / "facts.json").write_text(json.dumps({
            "entity": "alice",
            "category": "people",
            "facts": [],
        }))

        def _write(idx: int):
            storage.add_fact("alice", "status", f"fact {idx}")

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(_write, range(20)))

        facts = json.loads((entity_dir / "facts.json").read_text())["facts"]
        self.assertEqual(len(facts), 20)
        self.assertEqual(len({f["fact"] for f in facts}), 20)


if __name__ == "__main__":
    unittest.main()
