import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from relay.tools import execute_tool


class TestToolDispatch(unittest.TestCase):
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
        self.reload_patcher.start()
        self.addCleanup(self.settings_patcher.stop)
        self.addCleanup(self.reload_patcher.stop)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_read_my_config_via_execute_tool(self):
        result = execute_tool("read_my_config", {}, user_id="owner")
        self.assertIn("models", result)
        self.assertIn("google/gemma-4-31b-it", result)

    def test_set_default_model_via_execute_tool(self):
        result = execute_tool("set_default_model", {"model_name": "haiku"}, user_id="owner")
        cfg = json.loads(self.settings_file.read_text())
        self.assertIn("Default model changed", result)
        self.assertEqual(cfg["models"]["main"], "anthropic/claude-3.5-haiku")

    def test_update_config_setting_via_execute_tool(self):
        result = execute_tool("update_config_setting", {"key": "heartbeat.idle_minutes", "value": 30}, user_id="owner")
        cfg = json.loads(self.settings_file.read_text())
        self.assertIn("Updated heartbeat.idle_minutes", result)
        self.assertEqual(cfg["heartbeat"]["idle_minutes"], 30)

    def test_github_create_repo_formats_repo_url(self):
        with patch("relay.github_tools.execute_github_tool", return_value={
            "success": True,
            "message": "created",
            "repo_url": "https://github.com/example/repo",
        }):
            result = execute_tool("github_create_repo", {"repo_name": "repo"}, user_id="owner")

        self.assertIn("Repository URL: https://github.com/example/repo", result)

    def test_github_get_file_content_formats_nested_result(self):
        with patch("relay.github_tools.execute_github_tool", return_value={
            "success": True,
            "file": {"path": "README.md", "content": "hello"},
        }):
            result = execute_tool("github_get_file_content", {"repo_name": "repo", "file_path": "README.md"}, user_id="owner")

        self.assertEqual(result, "File: README.md\n\nhello")


if __name__ == "__main__":
    unittest.main()
