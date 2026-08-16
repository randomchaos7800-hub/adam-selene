import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from relay import claude_code_tool


class TestRunClaudeCodeSandboxContainment(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.sandbox = Path(self.tempdir.name) / "sandbox"
        self.sandbox.mkdir()
        self.addCleanup(self.tempdir.cleanup)
        self.patcher = patch.object(claude_code_tool, "_sandbox_dir", return_value=self.sandbox)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def _mock_result(self, stdout="output", returncode=0, stderr=""):
        result = Mock()
        result.stdout = stdout
        result.stderr = stderr
        result.returncode = returncode
        return result

    def test_traversal_subdir_is_rejected(self):
        with patch.object(claude_code_tool, "CLAUDE_BIN", "/usr/bin/claude"), \
             patch("subprocess.run") as mock_run:
            result = claude_code_tool.run_claude_code("do something", subdir="../../../etc")
        self.assertIn("Error", result)
        self.assertIn("outside", result)
        mock_run.assert_not_called()
        # No directory should have been created outside the sandbox either.
        self.assertFalse((self.sandbox.parent.parent / "etc-escaped-marker").exists())

    def test_traversal_subdir_does_not_create_escaped_directory(self):
        outside_target = self.sandbox.parent.parent  # two levels up from sandbox
        before = set(outside_target.iterdir()) if outside_target.exists() else set()
        with patch.object(claude_code_tool, "CLAUDE_BIN", "/usr/bin/claude"), \
             patch("subprocess.run"):
            claude_code_tool.run_claude_code("do something", subdir="../../evil")
        after = set(outside_target.iterdir()) if outside_target.exists() else set()
        self.assertEqual(before, after)

    def test_legitimate_nested_subdir_still_works(self):
        with patch.object(claude_code_tool, "CLAUDE_BIN", "/usr/bin/claude"), \
             patch("subprocess.run", return_value=self._mock_result()) as mock_run:
            result = claude_code_tool.run_claude_code("do something", subdir="projects/mytool")
        self.assertEqual(result, "output")
        mock_run.assert_called_once()
        self.assertTrue((self.sandbox / "projects" / "mytool").is_dir())
        self.assertEqual(mock_run.call_args.kwargs["cwd"], str((self.sandbox / "projects" / "mytool").resolve()))

    def test_no_subdir_uses_sandbox_root(self):
        with patch.object(claude_code_tool, "CLAUDE_BIN", "/usr/bin/claude"), \
             patch("subprocess.run", return_value=self._mock_result()) as mock_run:
            claude_code_tool.run_claude_code("do something")
        self.assertEqual(mock_run.call_args.kwargs["cwd"], str(self.sandbox))

    def test_subdir_equal_to_sandbox_itself_is_allowed(self):
        with patch.object(claude_code_tool, "CLAUDE_BIN", "/usr/bin/claude"), \
             patch("subprocess.run", return_value=self._mock_result()) as mock_run:
            result = claude_code_tool.run_claude_code("do something", subdir=".")
        self.assertEqual(result, "output")
        mock_run.assert_called_once()

    def test_sibling_directory_name_spoof_is_rejected(self):
        # A string-prefix containment check would be fooled by a sibling
        # directory that merely starts with the sandbox's name — Path.parents
        # membership must not be.
        (self.sandbox.parent / "sandbox-evil").mkdir()
        with patch.object(claude_code_tool, "CLAUDE_BIN", "/usr/bin/claude"), \
             patch("subprocess.run") as mock_run:
            result = claude_code_tool.run_claude_code("do something", subdir="../sandbox-evil")
        self.assertIn("Error", result)
        mock_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
