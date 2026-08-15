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


class TestFactProvenance(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.get_path_patcher = patch("memory.storage.get_memory_path", return_value=self.root)
        self.get_path_patcher.start()
        self.addCleanup(self.get_path_patcher.stop)
        self.addCleanup(self.tempdir.cleanup)

        entity_dir = self.root / "life" / "areas" / "people" / "alice"
        entity_dir.mkdir(parents=True, exist_ok=True)
        (self.root / "entities.json").write_text(json.dumps({
            "alice": {"category": "people", "aliases": [], "path": "life/areas/people/alice"}
        }))
        (entity_dir / "facts.json").write_text(json.dumps({"entity": "alice", "category": "people", "facts": []}))

    def test_default_provenance_is_owner_stated(self):
        storage.add_fact("alice", "status", "some fact")
        facts = storage.read_entity("alice")["recent_facts"]
        self.assertEqual(facts[0]["provenance"], "owner_stated")

    def test_explicit_provenance_is_stamped(self):
        storage.add_fact("alice", "status", "some fact", provenance="tool_derived")
        facts = storage.read_entity("alice")["recent_facts"]
        self.assertEqual(facts[0]["provenance"], "tool_derived")

    def test_invalid_provenance_falls_back_to_agent_inferred(self):
        storage.add_fact("alice", "status", "some fact", provenance="totally_made_up")
        facts = storage.read_entity("alice")["recent_facts"]
        self.assertEqual(facts[0]["provenance"], "agent_inferred")

    def test_is_trusted_provenance_true_for_owner_stated(self):
        self.assertTrue(storage.is_trusted_provenance({"provenance": "owner_stated"}))

    def test_is_trusted_provenance_true_for_agent_inferred(self):
        self.assertTrue(storage.is_trusted_provenance({"provenance": "agent_inferred"}))

    def test_is_trusted_provenance_false_for_tool_derived(self):
        self.assertFalse(storage.is_trusted_provenance({"provenance": "tool_derived"}))

    def test_is_trusted_provenance_defaults_true_for_legacy_facts_without_the_field(self):
        # Facts written before provenance existed have no field at all —
        # must not be retroactively treated as untrusted.
        self.assertTrue(storage.is_trusted_provenance({"fact": "old fact, no provenance field"}))


if __name__ == "__main__":
    unittest.main()
