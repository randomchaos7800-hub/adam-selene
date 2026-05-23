import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from relay import config_manager


class TestConfigManager(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.settings_file = Path(self.tempdir.name) / "settings.json"
        self.settings_file.write_text(json.dumps({
            "models": {"main": "google/gemma-4-31b-it", "extraction": "extract-model"},
            "openrouter": {"model": "google/gemma-4-31b-it"},
            "heartbeat": {"idle_minutes": 15},
            "service_name": "adam-selene.service",
        }))
        self.settings_patcher = patch("relay.config_manager._settings_file", return_value=self.settings_file)
        self.reload_patcher = patch("relay.config_manager.config.reload_settings", return_value={})
        self.settings_patcher.start()
        self.mock_reload = self.reload_patcher.start()
        self.addCleanup(self.settings_patcher.stop)
        self.addCleanup(self.reload_patcher.stop)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_read_my_config_returns_config(self):
        result = config_manager.read_my_config()
        self.assertTrue(result["success"])
        self.assertEqual(result["config"]["models"]["main"], "google/gemma-4-31b-it")

    def test_set_default_model_updates_nested_schema(self):
        result = config_manager.set_default_model("haiku")
        cfg = json.loads(self.settings_file.read_text())
        self.assertTrue(result["success"])
        self.assertEqual(cfg["models"]["main"], "anthropic/claude-3.5-haiku")
        self.assertEqual(cfg["openrouter"]["model"], "anthropic/claude-3.5-haiku")
        self.mock_reload.assert_called()

    def test_update_config_setting_updates_nested_key(self):
        result = config_manager.update_config_setting("heartbeat.idle_minutes", 30)
        cfg = json.loads(self.settings_file.read_text())
        self.assertTrue(result["success"])
        self.assertEqual(cfg["heartbeat"]["idle_minutes"], 30)

    def test_update_config_setting_rejects_invalid_key(self):
        result = config_manager.update_config_setting("verbose_logging", True)
        self.assertFalse(result["success"])
        self.assertIn("not allowed", result["error"])

    def test_restart_agent_service_reports_success(self):
        completed = Mock(returncode=0, stderr="")
        with patch("relay.config_manager.config.agent_service_name", return_value="adam-selene.service"):
            with patch("relay.config_manager.subprocess.run", return_value=completed) as mock_run:
                result = config_manager.restart_agent_service()
        self.assertTrue(result["success"])
        mock_run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
