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


class TestBiTemporalFacts(unittest.TestCase):
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

    def test_add_fact_defaults_valid_from_to_now(self):
        storage.add_fact("alice", "status", "some fact")
        fact = storage.read_entity("alice")["recent_facts"][0]
        self.assertIsNotNone(fact["valid_from"])
        self.assertIsNone(fact["valid_to"])

    def test_add_fact_accepts_explicit_valid_from(self):
        storage.add_fact("alice", "status", "switched jobs", valid_from="2026-03-01T00:00:00")
        fact = storage.read_entity("alice")["recent_facts"][0]
        self.assertEqual(fact["valid_from"], "2026-03-01T00:00:00")

    def test_supersede_stamps_valid_to_on_old_fact(self):
        old_id = storage.add_fact("alice", "status", "old fact")
        new_id = storage.add_fact("alice", "status", "new fact")
        storage.supersede_fact("alice", old_id, new_id)

        facts_file = self.root / "life" / "areas" / "people" / "alice" / "facts.json"
        all_facts = json.loads(facts_file.read_text())["facts"]
        old_fact = next(f for f in all_facts if f["id"] == old_id)
        self.assertIsNotNone(old_fact["valid_to"])

    def test_facts_valid_at_before_fact_existed_excludes_it(self):
        storage.add_fact("alice", "status", "future fact", valid_from="2026-06-01T00:00:00")
        result = storage.facts_valid_at("alice", "2026-01-01T00:00:00")
        self.assertEqual(result, [])

    def test_facts_valid_at_after_valid_from_includes_active_fact(self):
        storage.add_fact("alice", "status", "current fact", valid_from="2026-01-01T00:00:00")
        result = storage.facts_valid_at("alice", "2026-06-01T00:00:00")
        self.assertEqual(len(result), 1)

    def test_facts_valid_at_after_supersession_excludes_old_fact(self):
        old_id = storage.add_fact("alice", "status", "old fact", valid_from="2026-01-01T00:00:00")
        new_id = storage.add_fact("alice", "status", "new fact", valid_from="2026-06-01T00:00:00")
        storage.supersede_fact("alice", old_id, new_id)
        # supersede_fact stamps valid_to = now (test run time), which is
        # long after these fixed dates — query well before that point.
        result = storage.facts_valid_at("alice", "2026-03-01T00:00:00")
        result_ids = {f["id"] for f in result}
        self.assertIn(old_id, result_ids)
        self.assertNotIn(new_id, result_ids)  # not valid yet at this query time

    def test_facts_valid_at_reconstructs_historical_state(self):
        # The actual payoff: query a point in time BETWEEN when the old
        # fact was superseded and now — read_entity() alone can't do this,
        # it only ever shows current active facts.
        old_id = storage.add_fact("alice", "status", "worked at old job", valid_from="2026-01-01T00:00:00")
        new_id = storage.add_fact("alice", "status", "works at new job", valid_from="2026-06-01T00:00:00")
        storage.supersede_fact("alice", old_id, new_id)

        as_of_march = storage.facts_valid_at("alice", "2026-03-01T00:00:00")
        as_of_march_ids = {f["id"] for f in as_of_march}
        self.assertIn(old_id, as_of_march_ids)

    def test_facts_valid_at_unknown_entity_returns_empty(self):
        result = storage.facts_valid_at("nonexistent-entity", "2026-01-01T00:00:00")
        self.assertEqual(result, [])

    def test_facts_valid_at_handles_legacy_facts_without_bi_temporal_fields(self):
        # Simulate a fact written before valid_from/valid_to existed.
        facts_file = self.root / "life" / "areas" / "people" / "alice" / "facts.json"
        facts_file.write_text(json.dumps({
            "entity": "alice", "category": "people",
            "facts": [{"id": "legacy-1", "fact": "an old fact", "status": "active"}],
        }))
        result = storage.facts_valid_at("alice", "2026-01-01T00:00:00")
        self.assertEqual(len(result), 1)  # not silently dropped


if __name__ == "__main__":
    unittest.main()
