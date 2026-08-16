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


class TestFactAuthority(unittest.TestCase):
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

    def test_default_authority_for_ordinary_owner_stated_fact_is_standard(self):
        storage.add_fact("alice", "preference", "prefers tea")
        fact = storage.read_entity("alice")["recent_facts"][0]
        self.assertEqual(fact["authority"], "standard")

    def test_tool_derived_fact_always_gets_low_authority_regardless_of_category(self):
        storage.add_fact("alice", "constraint", "must always do X", provenance="tool_derived")
        fact = storage.read_entity("alice")["recent_facts"][0]
        self.assertEqual(fact["authority"], "low")

    def test_owner_stated_constraint_does_not_auto_elevate_to_high(self):
        # "constraint" in this codebase's taxonomy means an ordinary
        # world-fact ("Surgery costs $X", "Deadline is Friday"), not a
        # behavioral directive to the agent — category alone must never
        # grant "high" authority, even for owner_stated provenance.
        storage.add_fact("alice", "constraint", "surgery costs $4000", provenance="owner_stated")
        fact = storage.read_entity("alice")["recent_facts"][0]
        self.assertEqual(fact["authority"], "standard")

    def test_agent_inferred_constraint_does_not_get_high_authority(self):
        storage.add_fact("alice", "constraint", "seems to want X always", provenance="agent_inferred")
        fact = storage.read_entity("alice")["recent_facts"][0]
        self.assertEqual(fact["authority"], "standard")

    def test_explicit_high_authority_override_is_respected(self):
        # "high" is reachable — just never auto-derived from category.
        storage.add_fact("alice", "constraint", "never do X without asking", authority="high")
        fact = storage.read_entity("alice")["recent_facts"][0]
        self.assertEqual(fact["authority"], "high")

    def test_explicit_authority_override_is_respected(self):
        storage.add_fact("alice", "status", "some fact", authority="low")
        fact = storage.read_entity("alice")["recent_facts"][0]
        self.assertEqual(fact["authority"], "low")

    def test_invalid_explicit_authority_falls_back_to_derived_default(self):
        storage.add_fact("alice", "status", "some fact", authority="totally_made_up")
        fact = storage.read_entity("alice")["recent_facts"][0]
        self.assertEqual(fact["authority"], "standard")

    def test_is_actionable_authority_true_for_standard_and_high(self):
        self.assertTrue(storage.is_actionable_authority({"authority": "standard"}))
        self.assertTrue(storage.is_actionable_authority({"authority": "high"}))

    def test_is_actionable_authority_false_for_low(self):
        self.assertFalse(storage.is_actionable_authority({"authority": "low"}))

    def test_is_actionable_authority_defaults_true_for_legacy_facts_without_the_field(self):
        self.assertTrue(storage.is_actionable_authority({"fact": "old fact, no authority field"}))


class TestReadRecentFacts(unittest.TestCase):
    """read_recent_facts() is read_entity()'s facts-only sibling — same
    active/recent selection, without the summary.md read a caller that
    only needs timestamps (e.g. heartbeat's relationship pulse) doesn't
    use."""

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
            "alice": {"category": "people", "aliases": ["al"], "path": "life/areas/people/alice"}
        }))
        (entity_dir / "facts.json").write_text(json.dumps({"entity": "alice", "category": "people", "facts": []}))

    def test_matches_read_entity_recent_facts(self):
        storage.add_fact("alice", "preference", "prefers tea")
        storage.add_fact("alice", "status", "moved to Chicago")
        self.assertEqual(
            storage.read_recent_facts("alice"),
            storage.read_entity("alice")["recent_facts"],
        )

    def test_resolves_via_alias(self):
        storage.add_fact("alice", "preference", "prefers tea")
        self.assertEqual(storage.read_recent_facts("al"), storage.read_recent_facts("alice"))

    def test_unknown_entity_returns_none(self):
        self.assertIsNone(storage.read_recent_facts("nobody"))

    def test_no_facts_file_returns_empty_list(self):
        (self.root / "life" / "areas" / "people" / "alice" / "facts.json").unlink()
        self.assertEqual(storage.read_recent_facts("alice"), [])

    def test_does_not_read_summary_file(self):
        # summary.md is never even created in this test's fixture — if
        # read_recent_facts touched it, this would raise instead of
        # silently returning an empty-facts result.
        storage.add_fact("alice", "preference", "prefers tea")
        self.assertFalse((self.root / "life" / "areas" / "people" / "alice" / "summary.md").exists())
        self.assertEqual(len(storage.read_recent_facts("alice")), 1)


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

    def test_facts_valid_at_accepts_timezone_aware_at_time(self):
        # Stored valid_from/valid_to are always naive local time
        # (datetime.now().isoformat()) — a tz-aware at_time (valid ISO
        # 8601 with a UTC offset or 'Z' suffix) must not raise TypeError
        # from comparing naive vs aware datetimes.
        storage.add_fact("alice", "status", "current fact", valid_from="2026-01-01T00:00:00")
        try:
            result = storage.facts_valid_at("alice", "2026-06-01T00:00:00+00:00")
        except TypeError as e:
            self.fail(f"facts_valid_at raised TypeError on tz-aware at_time: {e}")
        self.assertEqual(len(result), 1)

    def test_facts_valid_at_accepts_zulu_suffix_at_time(self):
        storage.add_fact("alice", "status", "current fact", valid_from="2026-01-01T00:00:00")
        try:
            result = storage.facts_valid_at("alice", "2026-06-01T00:00:00Z")
        except TypeError as e:
            self.fail(f"facts_valid_at raised TypeError on Z-suffixed at_time: {e}")
        self.assertEqual(len(result), 1)

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
