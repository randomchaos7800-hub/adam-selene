import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.modules.setdefault("httpx", SimpleNamespace(get=Mock(), AsyncClient=Mock()))
sys.modules.setdefault("openai", SimpleNamespace(OpenAI=Mock()))
sys.path.insert(0, str(Path(__file__).parent.parent))

from relay.heartbeat import Heartbeat
from relay.switchboard import BudgetExceededError


class TestHeartbeat(unittest.TestCase):
    def setUp(self):
        patcher_settings = patch("relay.heartbeat.config.load_settings", return_value={"openrouter": {"heartbeat_model": "hb-model"}})
        patcher_snapshot = patch("relay.heartbeat.SnapshotManager")
        patcher_switchboard = patch("relay.heartbeat.Switchboard")
        patcher_sessions = patch("relay.heartbeat.SessionStore")
        patcher_memory_root = patch("relay.heartbeat.config.memory_root", return_value=Path("/tmp/test-memory"))

        self.addCleanup(patcher_settings.stop)
        self.addCleanup(patcher_snapshot.stop)
        self.addCleanup(patcher_switchboard.stop)
        self.addCleanup(patcher_sessions.stop)
        self.addCleanup(patcher_memory_root.stop)

        self.mock_settings = patcher_settings.start()
        self.mock_snapshot_cls = patcher_snapshot.start()
        self.mock_switchboard_cls = patcher_switchboard.start()
        self.mock_sessions_cls = patcher_sessions.start()
        self.mock_memory_root = patcher_memory_root.start()

        self.heartbeat = Heartbeat(user_id="test-user")
        self.snapshot_manager = self.mock_snapshot_cls.return_value
        self.switchboard = self.mock_switchboard_cls.return_value
        self.session_store = self.mock_sessions_cls.return_value

    def _run(self, coro):
        return asyncio.run(coro)

    def _mock_reflection_response(self, payload: dict):
        response = Mock()
        response.content = [Mock(text=f"```json\n{json.dumps(payload)}\n```")]
        response.usage.input_tokens = 10
        response.usage.output_tokens = 10
        self.switchboard.call.return_value = response

    def test_init_sets_up_dependencies(self):
        self.mock_snapshot_cls.assert_called_once()
        self.mock_switchboard_cls.assert_called_once()
        self.mock_sessions_cls.assert_called_once()

    def test_reflect_creates_snapshot_first_and_prunes(self):
        self.session_store.get_conversation_text.return_value = "Some conversation text " * 10
        self._mock_reflection_response({"successes": [], "failures": [], "patterns": [], "suggestion": ""})

        self._run(self.heartbeat.reflect())

        self.snapshot_manager.create_snapshot.assert_called_once_with(trigger="heartbeat")
        self.snapshot_manager.prune_old_snapshots.assert_called_once_with(max_age_hours=48)

    def test_reflect_returns_none_without_user(self):
        self.heartbeat._resolve_user_id = Mock(return_value=None)
        result = self._run(self.heartbeat.reflect())
        self.assertIsNone(result)
        self.snapshot_manager.create_snapshot.assert_called_once()

    def test_reflect_returns_none_for_short_conversation(self):
        self.session_store.get_conversation_text.return_value = "Short"
        result = self._run(self.heartbeat.reflect())
        self.assertIsNone(result)

    def test_reflect_handles_budget_error(self):
        self.session_store.get_conversation_text.return_value = "Some conversation text " * 10
        self.switchboard.call.side_effect = BudgetExceededError("Budget exceeded")
        result = self._run(self.heartbeat.reflect())
        self.assertIsNone(result)

    def test_reflect_parses_json_and_logs_experiment(self):
        self.session_store.get_conversation_text.return_value = "Some conversation text " * 10
        payload = {
            "successes": ["successful thing"],
            "failures": ["failed thing"],
            "patterns": ["recurring pattern"],
            "suggestion": "Try using memory better",
        }
        self._mock_reflection_response(payload)

        # This payload has failures+patterns+suggestion set, which also
        # triggers reflect()'s real relay.lighthouse.write_entry() call —
        # mock it too, or this test writes a live file into LIGHTHOUSE/
        # on every run.
        with patch("relay.heartbeat.storage.log_experiment") as mock_log, \
             patch("relay.lighthouse.write_entry"):
            result = self._run(self.heartbeat.reflect())

        self.assertEqual(result, payload)
        mock_log.assert_called_once()
        self.assertIn("Heartbeat observation", mock_log.call_args.kwargs["hypothesis"])
        self.assertEqual(mock_log.call_args.kwargs["status"], "observed")

    def test_reflect_accepts_plain_json(self):
        self.session_store.get_conversation_text.return_value = "Some conversation text " * 10
        payload = {"successes": ["test"], "failures": [], "patterns": [], "suggestion": "test suggestion"}
        response = Mock()
        response.content = [Mock(text=json.dumps(payload))]
        self.switchboard.call.return_value = response

        result = self._run(self.heartbeat.reflect())
        self.assertEqual(result["suggestion"], "test suggestion")

    def test_reflect_writes_lighthouse_when_actionable(self):
        self.session_store.get_conversation_text.return_value = "Some conversation text " * 10
        payload = {
            "successes": ["worked"],
            "failures": ["failed"],
            "patterns": ["pattern"],
            "suggestion": "Act on this",
        }
        self._mock_reflection_response(payload)

        with patch("relay.lighthouse.write_entry") as mock_write:
            self._run(self.heartbeat.reflect())

        mock_write.assert_called_once()
        self.assertEqual(mock_write.call_args.kwargs["section"], "corrections")

    def test_reflect_calls_switchboard_with_expected_params(self):
        self.session_store.get_conversation_text.return_value = "Some conversation text " * 10
        self._mock_reflection_response({"successes": [], "failures": [], "patterns": [], "suggestion": ""})

        self._run(self.heartbeat.reflect())

        kwargs = self.switchboard.call.call_args.kwargs
        self.assertEqual(kwargs["tier"], 2)
        self.assertEqual(kwargs["max_tokens"], 1024)
        self.assertEqual(kwargs["model_override"], "hb-model")

    def test_run_autoresearch_uses_configured_base_url(self):
        response = Mock()
        response.json.return_value = {"ok": True}
        response.raise_for_status.return_value = None
        client = AsyncMock()
        client.post.return_value = response
        client.__aenter__.return_value = client
        client.__aexit__.return_value = False

        with patch("relay.heartbeat.config.load_settings", return_value={"autoresearch": {"base_url": "http://example:9999"}}):
            with patch("httpx.AsyncClient", return_value=client):
                result = self._run(self.heartbeat._run_autoresearch("test topic"))

        self.assertEqual(result, {"ok": True})
        client.post.assert_awaited_once()
        self.assertEqual(client.post.await_args.args[0], "http://example:9999/search")


class TestIsDuplicateCorrection(unittest.TestCase):
    def setUp(self):
        patcher_settings = patch("relay.heartbeat.config.load_settings", return_value={"openrouter": {"heartbeat_model": "hb-model"}})
        patcher_snapshot = patch("relay.heartbeat.SnapshotManager")
        patcher_switchboard = patch("relay.heartbeat.Switchboard")
        patcher_sessions = patch("relay.heartbeat.SessionStore")
        patcher_memory_root = patch("relay.heartbeat.config.memory_root", return_value=Path("/tmp/test-memory"))
        for p in (patcher_settings, patcher_snapshot, patcher_switchboard, patcher_sessions, patcher_memory_root):
            p.start()
            self.addCleanup(p.stop)
        self.heartbeat = Heartbeat(user_id="test-user")

        self.tempdir = tempfile.TemporaryDirectory()
        self.lighthouse_root = Path(self.tempdir.name)
        self.corrections_dir = self.lighthouse_root / "corrections"
        self.corrections_dir.mkdir(parents=True)
        self.addCleanup(self.tempdir.cleanup)
        self.lh_patcher = patch("relay.lighthouse.LIGHTHOUSE_ROOT", self.lighthouse_root)
        self.lh_patcher.start()
        self.addCleanup(self.lh_patcher.stop)

    def _write_correction(self, filename: str, content: str = "some content here"):
        (self.corrections_dir / filename).write_text(content)

    def test_no_corrections_dir_returns_false(self):
        shutil.rmtree(self.corrections_dir)
        self.assertFalse(self.heartbeat._is_duplicate_correction("try using memory better"))

    def test_no_matching_file_returns_false(self):
        self._write_correction("2026-01-01_0000_completely-unrelated-topic.md", "unrelated body")
        self.assertFalse(self.heartbeat._is_duplicate_correction("try using memory better today"))

    def test_high_word_overlap_with_filename_is_duplicate(self):
        self._write_correction("2026-01-01_0000_try-using-memory-better.md", "unrelated body text")
        self.assertTrue(self.heartbeat._is_duplicate_correction("try using memory better"))

    def test_verbatim_substring_in_content_is_duplicate(self):
        self._write_correction(
            "2026-01-01_0000_something-else-entirely.md",
            "Suggested change: try using memory better next time",
        )
        self.assertTrue(self.heartbeat._is_duplicate_correction("try using memory better"))

    def test_old_file_outside_window_is_ignored(self):
        path = self.corrections_dir / "2026-01-01_0000_try-using-memory-better.md"
        path.write_text("unrelated body")
        old_time = time.time() - (5 * 3600)  # 5 hours ago, outside 4h window
        os.utime(path, (old_time, old_time))
        self.assertFalse(self.heartbeat._is_duplicate_correction("try using memory better"))

    def test_recent_file_within_window_is_checked(self):
        path = self.corrections_dir / "2026-01-01_0000_try-using-memory-better.md"
        path.write_text("unrelated body")
        recent_time = time.time() - (2 * 3600)  # 2 hours ago, inside 4h window
        os.utime(path, (recent_time, recent_time))
        self.assertTrue(self.heartbeat._is_duplicate_correction("try using memory better"))


class TestCompactMemoryNearDupCap(unittest.TestCase):
    def setUp(self):
        patcher_settings = patch("relay.heartbeat.config.load_settings", return_value={"openrouter": {"heartbeat_model": "hb-model"}})
        patcher_snapshot = patch("relay.heartbeat.SnapshotManager")
        patcher_switchboard = patch("relay.heartbeat.Switchboard")
        patcher_sessions = patch("relay.heartbeat.SessionStore")
        patcher_memory_root = patch("relay.heartbeat.config.memory_root", return_value=Path("/tmp/test-memory"))
        for p in (patcher_settings, patcher_snapshot, patcher_switchboard, patcher_sessions, patcher_memory_root):
            p.start()
            self.addCleanup(p.stop)
        self.heartbeat = Heartbeat(user_id="test-user")

    def _make_facts(self, n, exact_dup_pair=False):
        facts = []
        for i in range(n):
            content = "duplicate content here" if (exact_dup_pair and i < 2) else f"unique fact number {i}"
            facts.append({"id": f"f{i}", "fact": content, "status": "active", "active": True, "timestamp": f"2026-01-01T00:00:{i:02d}"})
        return facts

    def test_small_entity_runs_near_dup_scan(self):
        facts = [
            {"id": "f0", "fact": "the sky is blue today", "status": "active", "active": True, "timestamp": "2026-01-01T00:00:01"},
            {"id": "f1", "fact": "the sky is blue today!", "status": "active", "active": True, "timestamp": "2026-01-01T00:00:02"},
        ]
        entities = {"test-entity": {"path": "life/areas/concepts/test-entity", "category": "concepts"}}
        facts_file_content = {"entity": "test-entity", "category": "concepts", "facts": facts}

        with patch("relay.heartbeat.storage.get_memory_path", return_value=Path("/fake/memory")), \
             patch("relay.heartbeat.storage.load_entities", return_value=entities), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value=json.dumps(facts_file_content)), \
             patch("relay.heartbeat.storage.update_json_file") as mock_update, \
             patch("builtins.open", unittest.mock.mock_open()):
            stats = self.heartbeat._compact_memory()

        self.assertEqual(stats["near_dup"], 1)
        mock_update.assert_called_once()

    def test_large_entity_skips_near_dup_scan_but_keeps_exact_dup(self):
        # 250 facts, first two are byte-identical (Tier 0 catches this
        # regardless of the Tier 1 cap).
        facts = self._make_facts(250, exact_dup_pair=True)
        entities = {"test-entity": {"path": "life/areas/concepts/test-entity", "category": "concepts"}}
        facts_file_content = {"entity": "test-entity", "category": "concepts", "facts": facts}

        with patch("relay.heartbeat.storage.get_memory_path", return_value=Path("/fake/memory")), \
             patch("relay.heartbeat.storage.load_entities", return_value=entities), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value=json.dumps(facts_file_content)), \
             patch("relay.heartbeat.storage.update_json_file") as mock_update, \
             patch("builtins.open", unittest.mock.mock_open()):
            stats = self.heartbeat._compact_memory()

        self.assertEqual(stats["near_dup"], 0)  # Tier 1 skipped, cap exceeded
        self.assertEqual(stats["exact"], 1)     # Tier 0 still ran
        mock_update.assert_called_once()


if __name__ == "__main__":
    unittest.main()
