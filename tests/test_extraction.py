import unittest
from unittest.mock import Mock, patch

from memory import extraction
from memory.extraction import _verify_decision


class TestVerifyDecisionNone(unittest.TestCase):
    def test_none_verdict_kept_when_genuinely_similar(self):
        fact = {"entity": "alice", "content": "Alice prefers concise responses"}
        decision = {"operation": "NONE", "supersedes_id": None}
        existing = {"alice": [{"id": "f1", "fact": "Alice prefers concise responses over long ones"}]}

        result = _verify_decision(fact, decision, existing)
        self.assertEqual(result["operation"], "NONE")

    def test_none_verdict_overridden_to_add_when_not_actually_similar(self):
        fact = {"entity": "alice", "content": "Alice's favorite color is blue"}
        decision = {"operation": "NONE", "supersedes_id": None}
        existing = {"alice": [{"id": "f1", "fact": "Alice works as a software engineer"}]}

        result = _verify_decision(fact, decision, existing)
        self.assertEqual(result["operation"], "ADD")
        self.assertIsNone(result["supersedes_id"])

    def test_none_verdict_with_no_existing_facts_for_entity_overridden(self):
        fact = {"entity": "bob", "content": "Bob just started a new job"}
        decision = {"operation": "NONE", "supersedes_id": None}
        existing = {}  # no facts recorded for bob at all

        result = _verify_decision(fact, decision, existing)
        self.assertEqual(result["operation"], "ADD")

    def test_none_verdict_with_empty_content_passes_through_unchanged(self):
        fact = {"entity": "alice", "content": ""}
        decision = {"operation": "NONE", "supersedes_id": None}
        existing = {"alice": [{"id": "f1", "fact": "something"}]}

        result = _verify_decision(fact, decision, existing)
        self.assertEqual(result["operation"], "NONE")  # unchanged — nothing to verify against


class TestVerifyDecisionUpdate(unittest.TestCase):
    def test_update_verdict_kept_when_supersedes_id_exists(self):
        fact = {"entity": "alice", "content": "Alice now works remotely"}
        decision = {"operation": "UPDATE", "supersedes_id": "f1"}
        existing = {"alice": [{"id": "f1", "fact": "Alice works in the office"}]}

        result = _verify_decision(fact, decision, existing)
        self.assertEqual(result["operation"], "UPDATE")
        self.assertEqual(result["supersedes_id"], "f1")

    def test_update_verdict_downgraded_when_supersedes_id_unknown(self):
        fact = {"entity": "alice", "content": "Alice now works remotely"}
        decision = {"operation": "UPDATE", "supersedes_id": "hallucinated-id"}
        existing = {"alice": [{"id": "f1", "fact": "Alice works in the office"}]}

        result = _verify_decision(fact, decision, existing)
        self.assertEqual(result["operation"], "ADD")
        self.assertIsNone(result["supersedes_id"])

    def test_update_verdict_with_no_supersedes_id_passes_through(self):
        # UPDATE without a supersedes_id is already a Stage 2 output shape
        # oddity, but shouldn't crash — just passes through unchanged.
        fact = {"entity": "alice", "content": "Alice now works remotely"}
        decision = {"operation": "UPDATE", "supersedes_id": None}
        existing = {"alice": [{"id": "f1", "fact": "Alice works in the office"}]}

        result = _verify_decision(fact, decision, existing)
        self.assertEqual(result["operation"], "UPDATE")


class TestVerifyDecisionAdd(unittest.TestCase):
    def test_add_verdict_passes_through_unchanged(self):
        fact = {"entity": "alice", "content": "Alice got a new pet"}
        decision = {"operation": "ADD", "supersedes_id": None, "reason": "new info"}
        existing = {"alice": [{"id": "f1", "fact": "Alice works in the office"}]}

        result = _verify_decision(fact, decision, existing)
        self.assertEqual(result, decision)


class TestExtractToMemoryProvenance(unittest.TestCase):
    def _mock_extractor(self, facts, new_entities=None):
        mock = Mock()
        mock.extract.return_value = {"facts": facts, "new_entities": new_entities or [], "timeline_entry": ""}
        mock.compare_against_memory.return_value = [
            {"index": i, "operation": "ADD", "supersedes_id": None, "reason": "new"} for i in range(len(facts))
        ]
        return mock

    def test_default_provenance_is_owner_stated(self):
        facts = [{"entity": "alice", "type": "status", "content": "new fact"}]
        with patch("memory.extraction.Extractor", return_value=self._mock_extractor(facts)), \
             patch("memory.extraction.storage.add_entity"), \
             patch("memory.extraction.storage.load_entities", return_value={"alice": {}}), \
             patch("memory.extraction.storage.read_entity", return_value=None), \
             patch("memory.extraction.storage.add_fact", return_value="fact-1") as mock_add_fact, \
             patch("memory.extraction.storage.append_timeline"):
            extraction.extract_to_memory("some conversation")

        mock_add_fact.assert_called_once()
        self.assertEqual(mock_add_fact.call_args.kwargs["provenance"], "owner_stated")

    def test_explicit_provenance_threads_through_to_add_fact(self):
        facts = [{"entity": "alice", "type": "status", "content": "irc-sourced fact"}]
        with patch("memory.extraction.Extractor", return_value=self._mock_extractor(facts)), \
             patch("memory.extraction.storage.add_entity"), \
             patch("memory.extraction.storage.load_entities", return_value={"alice": {}}), \
             patch("memory.extraction.storage.read_entity", return_value=None), \
             patch("memory.extraction.storage.add_fact", return_value="fact-1") as mock_add_fact, \
             patch("memory.extraction.storage.append_timeline"):
            extraction.extract_to_memory("irc conversation", provenance="tool_derived")

        self.assertEqual(mock_add_fact.call_args.kwargs["provenance"], "tool_derived")

    def test_run_alias_passes_provenance_through(self):
        facts = [{"entity": "alice", "type": "status", "content": "irc-sourced fact"}]
        with patch("memory.extraction.Extractor", return_value=self._mock_extractor(facts)), \
             patch("memory.extraction.storage.add_entity"), \
             patch("memory.extraction.storage.load_entities", return_value={"alice": {}}), \
             patch("memory.extraction.storage.read_entity", return_value=None), \
             patch("memory.extraction.storage.add_fact", return_value="fact-1") as mock_add_fact, \
             patch("memory.extraction.storage.append_timeline"):
            extraction.run("irc conversation", provenance="tool_derived")

        self.assertEqual(mock_add_fact.call_args.kwargs["provenance"], "tool_derived")


if __name__ == "__main__":
    unittest.main()
