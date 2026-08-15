import asyncio
import json
import sys
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


if __name__ == "__main__":
    unittest.main()
