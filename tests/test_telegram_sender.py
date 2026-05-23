import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.modules.setdefault("telegram", types.SimpleNamespace(Bot=object))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *args, **kwargs: None))

from relay import telegram_sender


class TestTelegramSender(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.memory_root_patcher = patch("relay.config.memory_root", return_value=self.root)
        self.memory_root_patcher.start()
        self.addCleanup(self.memory_root_patcher.stop)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_failed_initiation_send_releases_waiting_state(self):
        with patch("relay.telegram_sender._send_telegram_message", new=AsyncMock(return_value={"success": False, "error": "boom"})):
            result = telegram_sender.send_message_to_owner("hello")

        self.assertFalse(result["success"])
        state = telegram_sender.get_conversation_state()
        self.assertEqual(state["state"], "WAITING")
        self.assertIsNone(state["initiation_sent_at"])

    def test_successful_initiation_send_marks_waiting_for_response(self):
        with patch("relay.telegram_sender._send_telegram_message", new=AsyncMock(return_value={"success": True, "message": "ok"})):
            result = telegram_sender.send_message_to_owner("hello")

        self.assertTrue(result["success"])
        state = telegram_sender.get_conversation_state()
        self.assertEqual(state["state"], "WAITING_FOR_RESPONSE")
        self.assertIsNotNone(state["initiation_sent_at"])


if __name__ == "__main__":
    unittest.main()
