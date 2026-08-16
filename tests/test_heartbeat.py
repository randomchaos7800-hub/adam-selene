import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
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

    def _timestamp(self, i):
        # Proper zero-padded, always-sortable timestamps — _make_facts's
        # naive f"...:{i:02d}" seconds field breaks past i=59; these tests
        # need well over 200 distinct, correctly-ordering values.
        return f"2026-01-01T{(i // 3600) % 24:02d}:{(i // 60) % 60:02d}:{i % 60:02d}"

    def _distinct_content(self, i):
        # "unique fact number {i}" for varying i shares almost its entire
        # string with its neighbors (only the trailing digits differ), so
        # SequenceMatcher scores them well above the 0.95 near-dup
        # threshold against EACH OTHER — not a fixture I can use for
        # "these facts are all genuinely distinct" in these tests. A hash
        # gives every index a essentially-uncorrelated string instead.
        import hashlib
        return f"topic-{hashlib.md5(str(i).encode()).hexdigest()[:16]}"

    def test_large_entity_exact_dup_still_caught_regardless_of_window(self):
        # 250 facts, first two (newest, by descending timestamp) are
        # byte-identical — Tier 0 is unaffected by the Tier 1 window.
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

        self.assertEqual(stats["exact"], 1)
        mock_update.assert_called_once()

    def test_large_entity_near_dup_within_window_is_still_caught(self):
        # A large entity (300 facts, well past the 200-fact window size)
        # must still make near-dup compaction progress on facts within
        # NEAR_DUP_WINDOW of each other — this is the actual regression
        # guard: the old "skip Tier 1 entirely above 200 facts" bug would
        # have caught nothing here either.
        facts = []
        for i in range(300):
            if i == 0:
                content = "the sky is blue today"
            elif i == 1:
                content = "the sky is blue today!"  # near-dup, NOT byte-identical — must be Tier 1, not Tier 0
            else:
                content = self._distinct_content(i)
            facts.append({"id": f"f{i}", "fact": content, "status": "active", "active": True, "timestamp": self._timestamp(300 - i)})
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

    def test_large_entity_near_dup_outside_window_is_not_caught(self):
        # Demonstrates the bound is real: two near-identical facts more
        # than NEAR_DUP_WINDOW apart in scan order fall out of the sliding
        # window and are NOT compared against each other — this is the
        # accepted tradeoff for bounding per-tick cost, not a bug.
        from relay.heartbeat import NEAR_DUP_WINDOW
        n = NEAR_DUP_WINDOW + 50
        facts = []
        for i in range(n):
            if i == 0:
                content = "the sky is blue today"
            elif i == n - 1:
                content = "the sky is blue today!"  # near-dup of f0, but far apart in scan order
            else:
                content = self._distinct_content(i)
            facts.append({"id": f"f{i}", "fact": content, "status": "active", "active": True, "timestamp": self._timestamp(n - i)})
        entities = {"test-entity": {"path": "life/areas/concepts/test-entity", "category": "concepts"}}
        facts_file_content = {"entity": "test-entity", "category": "concepts", "facts": facts}

        with patch("relay.heartbeat.storage.get_memory_path", return_value=Path("/fake/memory")), \
             patch("relay.heartbeat.storage.load_entities", return_value=entities), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value=json.dumps(facts_file_content)), \
             patch("relay.heartbeat.storage.update_json_file") as mock_update, \
             patch("builtins.open", unittest.mock.mock_open()):
            stats = self.heartbeat._compact_memory()

        self.assertEqual(stats["near_dup"], 0)


class TestRelationshipPulse(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.memory_root = Path(self.tempdir.name)
        self.addCleanup(self.tempdir.cleanup)

        patcher_snapshot = patch("relay.heartbeat.SnapshotManager")
        patcher_switchboard = patch("relay.heartbeat.Switchboard")
        patcher_sessions = patch("relay.heartbeat.SessionStore")
        patcher_memory_root = patch("relay.heartbeat.config.memory_root", return_value=self.memory_root)
        patcher_settings = patch("relay.heartbeat.config.load_settings", return_value={})
        for p in (patcher_snapshot, patcher_switchboard, patcher_sessions, patcher_memory_root, patcher_settings):
            p.start()
            self.addCleanup(p.stop)
        self.heartbeat = Heartbeat(user_id="test-user")

    def _entity(self, name, days_ago):
        ts = (datetime.now() - timedelta(days=days_ago)).isoformat() if days_ago is not None else None
        recent_facts = [{"fact": "something", "timestamp": ts}] if ts else []
        return {"name": name, "category": "people", "aliases": []}, recent_facts

    def test_no_people_entities_returns_none(self):
        with patch("relay.heartbeat.storage.list_entities_by_category", return_value=[]):
            result = self._run(self.heartbeat.relationship_pulse())
        self.assertIsNone(result)

    def test_fresh_relationship_is_not_flagged(self):
        entity, recent_facts = self._entity("alice", days_ago=3)
        with patch("relay.heartbeat.storage.list_entities_by_category", return_value=[entity]), \
             patch("relay.heartbeat.storage.read_recent_facts", return_value=recent_facts):
            result = self._run(self.heartbeat.relationship_pulse())
        self.assertIsNone(result)

    def test_stale_relationship_is_found_and_returned(self):
        entity, recent_facts = self._entity("alice", days_ago=30)
        with patch("relay.heartbeat.storage.list_entities_by_category", return_value=[entity]), \
             patch("relay.heartbeat.storage.read_recent_facts", return_value=recent_facts), \
             patch("relay.lighthouse.write_entry") as mock_write, \
             patch.object(self.heartbeat, "_push_rate_limit_ok", return_value=False):
            result = self._run(self.heartbeat.relationship_pulse())
        self.assertEqual(result["stale_entity"], "alice")
        self.assertGreaterEqual(result["days_stale"], 21)
        mock_write.assert_called_once()
        self.assertEqual(mock_write.call_args.kwargs["section"], "patterns")

    def test_entity_with_no_facts_is_skipped(self):
        entity, recent_facts = self._entity("alice", days_ago=None)
        with patch("relay.heartbeat.storage.list_entities_by_category", return_value=[entity]), \
             patch("relay.heartbeat.storage.read_recent_facts", return_value=recent_facts):
            result = self._run(self.heartbeat.relationship_pulse())
        self.assertIsNone(result)

    def test_timezone_aware_fact_timestamp_does_not_crash(self):
        # A fact timestamp with a UTC offset (e.g. written by a different
        # code path than the naive-local one add_fact() uses today) used
        # to blow up max(timestamps) / the days-stale subtraction with a
        # naive-vs-aware TypeError, silently swallowed by the outer
        # except Exception in Heartbeat.start() — the whole feature would
        # just no-op for the day instead of surfacing the real fact.
        entity = {"name": "alice", "category": "people", "aliases": []}
        stale_ts = (datetime.now() - timedelta(days=30)).isoformat() + "+00:00"
        recent_facts = [{"fact": "something", "timestamp": stale_ts}]
        with patch("relay.heartbeat.storage.list_entities_by_category", return_value=[entity]), \
             patch("relay.heartbeat.storage.read_recent_facts", return_value=recent_facts), \
             patch("relay.lighthouse.write_entry"), \
             patch.object(self.heartbeat, "_push_rate_limit_ok", return_value=False):
            result = self._run(self.heartbeat.relationship_pulse())
        self.assertEqual(result["stale_entity"], "alice")

    def test_second_call_same_day_is_a_noop(self):
        entity, recent_facts = self._entity("alice", days_ago=30)
        with patch("relay.heartbeat.storage.list_entities_by_category", return_value=[entity]), \
             patch("relay.heartbeat.storage.read_recent_facts", return_value=recent_facts), \
             patch("relay.lighthouse.write_entry") as mock_write, \
             patch.object(self.heartbeat, "_push_rate_limit_ok", return_value=False):
            first = self._run(self.heartbeat.relationship_pulse())
            second = self._run(self.heartbeat.relationship_pulse())
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        mock_write.assert_called_once()  # not called again on the same-day no-op

    def test_cooldown_prevents_re_flagging_within_window(self):
        entity, recent_facts = self._entity("alice", days_ago=30)
        state_path = self.memory_root / "relationship_pulse_state.json"

        with patch("relay.heartbeat.storage.list_entities_by_category", return_value=[entity]), \
             patch("relay.heartbeat.storage.read_recent_facts", return_value=recent_facts), \
             patch("relay.lighthouse.write_entry"), \
             patch.object(self.heartbeat, "_push_rate_limit_ok", return_value=False):
            self._run(self.heartbeat.relationship_pulse())

        # Force the state's last_run_date back a day so the next call isn't
        # blocked by the same-day check, only by the cooldown we want to test.
        state = json.loads(state_path.read_text())
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        state["last_run_date"] = yesterday
        state_path.write_text(json.dumps(state))

        with patch("relay.heartbeat.storage.list_entities_by_category", return_value=[entity]), \
             patch("relay.heartbeat.storage.read_recent_facts", return_value=recent_facts), \
             patch("relay.lighthouse.write_entry") as mock_write_2, \
             patch.object(self.heartbeat, "_push_rate_limit_ok", return_value=False):
            result = self._run(self.heartbeat.relationship_pulse())

        self.assertIsNone(result)  # still within the 14-day cooldown for alice
        mock_write_2.assert_not_called()

    def test_most_stale_person_picked_first(self):
        entity_a, facts_a = self._entity("alice", days_ago=25)
        entity_b, facts_b = self._entity("bob", days_ago=60)

        def _read_recent_facts(name):
            return facts_a if name == "alice" else facts_b

        with patch("relay.heartbeat.storage.list_entities_by_category", return_value=[entity_a, entity_b]), \
             patch("relay.heartbeat.storage.read_recent_facts", side_effect=_read_recent_facts), \
             patch("relay.lighthouse.write_entry"), \
             patch.object(self.heartbeat, "_push_rate_limit_ok", return_value=False):
            result = self._run(self.heartbeat.relationship_pulse())

        self.assertEqual(result["stale_entity"], "bob")

    def test_disabled_via_settings_returns_none(self):
        entity, recent_facts = self._entity("alice", days_ago=60)
        with patch("relay.heartbeat.config.load_settings", return_value={"relationship_pulse": {"enabled": False}}), \
             patch("relay.heartbeat.storage.list_entities_by_category", return_value=[entity]), \
             patch("relay.heartbeat.storage.read_recent_facts", return_value=recent_facts), \
             patch("relay.lighthouse.write_entry") as mock_write:
            result = self._run(self.heartbeat.relationship_pulse())
        self.assertIsNone(result)
        mock_write.assert_not_called()

    def test_push_attempted_when_rate_limit_ok(self):
        # relay.telegram_sender does `from telegram import Bot` at module
        # level, which pulls in python-telegram-bot internals that choke
        # on this test file's own module-level httpx stub (set up for
        # unrelated reasons — see the sys.modules.setdefault calls at the
        # top of this file). Pre-seeding sys.modules with a fake
        # relay.telegram_sender avoids ever triggering that real import.
        entity, recent_facts = self._entity("alice", days_ago=30)
        fake_module = Mock()
        fake_module.can_send_message.return_value = (True, "ok")
        fake_module._send_telegram_message = AsyncMock(return_value={"success": True})
        with patch("relay.heartbeat.storage.list_entities_by_category", return_value=[entity]), \
             patch("relay.heartbeat.storage.read_recent_facts", return_value=recent_facts), \
             patch("relay.lighthouse.write_entry"), \
             patch.object(self.heartbeat, "_push_rate_limit_ok", return_value=True), \
             patch.dict(sys.modules, {"relay.telegram_sender": fake_module}):
            self._run(self.heartbeat.relationship_pulse())
        fake_module._send_telegram_message.assert_awaited_once()
        self.assertIn("Alice", fake_module._send_telegram_message.await_args.args[0])
        fake_module.mark_initiation_sent.assert_called_once()

    def test_push_skipped_when_rate_limit_not_ok(self):
        entity, recent_facts = self._entity("alice", days_ago=30)
        fake_module = Mock()
        fake_module._send_telegram_message = AsyncMock()
        with patch("relay.heartbeat.storage.list_entities_by_category", return_value=[entity]), \
             patch("relay.heartbeat.storage.read_recent_facts", return_value=recent_facts), \
             patch("relay.lighthouse.write_entry"), \
             patch.object(self.heartbeat, "_push_rate_limit_ok", return_value=False), \
             patch.dict(sys.modules, {"relay.telegram_sender": fake_module}):
            self._run(self.heartbeat.relationship_pulse())
        fake_module._send_telegram_message.assert_not_awaited()

    def _run(self, coro):
        return asyncio.run(coro)


if __name__ == "__main__":
    unittest.main()
