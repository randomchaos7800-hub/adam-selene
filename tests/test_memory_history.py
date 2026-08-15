import unittest
from unittest.mock import patch

from relay.tool_domains.memory_history import _handle_read_memory_history


class TestReadMemoryHistoryHandler(unittest.TestCase):
    def test_missing_entity_returns_error(self):
        result = _handle_read_memory_history({"at_time": "2026-01-01"})
        self.assertIn("required", result)

    def test_missing_at_time_returns_error(self):
        result = _handle_read_memory_history({"entity": "alice"})
        self.assertIn("required", result)

    def test_invalid_at_time_returns_error(self):
        with patch("relay.tool_domains.memory_history.storage.facts_valid_at", side_effect=ValueError):
            result = _handle_read_memory_history({"entity": "alice", "at_time": "not-a-date"})
        self.assertIn("valid ISO 8601", result)

    def test_no_facts_found_returns_helpful_message(self):
        with patch("relay.tool_domains.memory_history.storage.facts_valid_at", return_value=[]):
            result = _handle_read_memory_history({"entity": "alice", "at_time": "2026-01-01"})
        self.assertIn("No facts", result)

    def test_formats_facts_found(self):
        facts = [{"fact": "worked remotely", "category": "status", "status": "superseded"}]
        with patch("relay.tool_domains.memory_history.storage.facts_valid_at", return_value=facts):
            result = _handle_read_memory_history({"entity": "alice", "at_time": "2026-01-01"})
        self.assertIn("worked remotely", result)
        self.assertIn("superseded", result)


if __name__ == "__main__":
    unittest.main()
