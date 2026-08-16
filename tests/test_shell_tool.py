import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from relay import shell_tool

_HAS_BWRAP = shutil.which("bwrap") is not None


class TestBlockedPatterns(unittest.TestCase):
    def test_blocks_rm_rf(self):
        self.assertIsNotNone(shell_tool._is_blocked("rm -rf /"))

    def test_blocks_vault_access(self):
        self.assertIsNotNone(shell_tool._is_blocked("cat ~/.vault/secrets.age"))

    def test_allows_ordinary_command(self):
        self.assertIsNone(shell_tool._is_blocked("ls -la"))

    def test_blocked_command_never_reaches_subprocess(self):
        with patch("subprocess.run") as mock_run:
            result = shell_tool.run_shell("rm -rf /")
        self.assertIn("Blocked", result)
        mock_run.assert_not_called()


class TestRequireSandboxSetting(unittest.TestCase):
    def test_defaults_true_when_unset(self):
        with patch("relay.shell_tool.config.load_settings", return_value={}):
            self.assertTrue(shell_tool._require_sandbox())

    def test_respects_explicit_false(self):
        with patch("relay.shell_tool.config.load_settings", return_value={"shell": {"require_sandbox": False}}):
            self.assertFalse(shell_tool._require_sandbox())


class TestFailClosedWithoutBwrap(unittest.TestCase):
    def test_refuses_to_run_unsandboxed_by_default(self):
        # Mock load_settings explicitly rather than relying on whatever a
        # real settings.json on the machine running this test happens to
        # contain — require_sandbox defaults to True, but a real deployment
        # config could set it False and silently make this test meaningless.
        with patch.object(shell_tool, "BWRAP_BIN", None), \
             patch("relay.shell_tool.config.load_settings", return_value={}), \
             patch("subprocess.run") as mock_run:
            result = shell_tool.run_shell("echo hi")
        self.assertIn("bubblewrap", result.lower())
        self.assertIn("Error", result)
        mock_run.assert_not_called()

    def test_explicit_opt_out_runs_unsandboxed_and_logs(self):
        mock_result = Mock(stdout="hi\n", stderr="", returncode=0)
        with patch.object(shell_tool, "BWRAP_BIN", None), \
             patch("relay.shell_tool.config.load_settings", return_value={"shell": {"require_sandbox": False}}), \
             patch("subprocess.run", return_value=mock_result) as mock_run:
            result = shell_tool.run_shell("echo hi")
        self.assertEqual(result, "hi")
        mock_run.assert_called_once()
        self.assertEqual(mock_run.call_args.kwargs.get("shell"), True)


class TestBwrapArgvConstruction(unittest.TestCase):
    """Fast, portable — mocks subprocess.run, doesn't need bwrap installed.
    Verifies the sandbox is actually being asked for the properties it
    claims (read-only root, secrets masked, env cleared) rather than just
    trusting that it is."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.tempdir.name) / "project"
        self.memory_root = Path(self.tempdir.name) / "memory"
        self.project_root.mkdir()
        self.memory_root.mkdir()
        self.addCleanup(self.tempdir.cleanup)
        self.patchers = [
            patch.object(shell_tool, "BWRAP_BIN", "/usr/bin/bwrap"),
            patch("relay.shell_tool.config.project_root", return_value=self.project_root),
            patch("relay.shell_tool.config.memory_root", return_value=self.memory_root),
        ]
        for p in self.patchers:
            p.start()
            self.addCleanup(p.stop)

    def _run_and_capture_argv(self, command="echo hi", **kwargs):
        mock_result = Mock(stdout="hi\n", stderr="", returncode=0)
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            shell_tool.run_shell(command, **kwargs)
        self.assertTrue(mock_run.called)
        return mock_run.call_args.args[0]

    def test_uses_bwrap_binary(self):
        argv = self._run_and_capture_argv()
        self.assertEqual(argv[0], "/usr/bin/bwrap")

    def test_root_is_read_only_bound(self):
        argv = self._run_and_capture_argv()
        self.assertIn("--ro-bind", argv)
        idx = argv.index("--ro-bind")
        self.assertEqual(argv[idx + 1], "/")
        self.assertEqual(argv[idx + 2], "/")

    def test_home_is_blanked(self):
        argv = self._run_and_capture_argv()
        self.assertIn(str(Path.home().resolve()), argv)
        home_idx = argv.index(str(Path.home().resolve()))
        self.assertEqual(argv[home_idx - 1], "--tmpfs")

    def test_project_root_is_writable(self):
        argv = self._run_and_capture_argv()
        # project_root appears twice consecutively (src, dest of --bind);
        # .index() finds the src position, immediately preceded by --bind
        # (not --ro-bind — read-write).
        idx = argv.index(str(self.project_root.resolve()))
        self.assertEqual(argv[idx - 1], "--bind")
        self.assertEqual(argv[idx + 1], str(self.project_root.resolve()))

    def test_secrets_env_is_masked_when_present(self):
        (self.project_root / "config").mkdir()
        (self.project_root / "config" / "secrets.env").write_text("SECRET=1")
        argv = self._run_and_capture_argv()
        secrets_path = str((self.project_root / "config" / "secrets.env").resolve())
        self.assertIn(secrets_path, argv)
        idx = argv.index(secrets_path)
        self.assertEqual(argv[idx - 2:idx], ["--ro-bind", "/dev/null"])

    def test_secrets_env_not_referenced_when_absent(self):
        argv = self._run_and_capture_argv()
        secrets_path = str((self.project_root / "config" / "secrets.env").resolve())
        self.assertNotIn(secrets_path, argv)

    def test_environment_is_cleared(self):
        argv = self._run_and_capture_argv()
        self.assertIn("--clearenv", argv)

    def test_allowlisted_env_vars_pass_through(self):
        with patch.dict("os.environ", {"PATH": "/usr/bin"}):
            argv = self._run_and_capture_argv()
        clearenv_idx = argv.index("--clearenv")
        remainder = argv[clearenv_idx:]
        self.assertIn("PATH", remainder)

    def test_command_is_passed_via_sh_c(self):
        argv = self._run_and_capture_argv(command="echo distinctive-marker-xyz")
        self.assertEqual(argv[-3], "/bin/sh")
        self.assertEqual(argv[-2], "-c")
        self.assertEqual(argv[-1], "echo distinctive-marker-xyz")

    def test_namespace_isolation_flags_present(self):
        argv = self._run_and_capture_argv()
        for flag in ("--unshare-pid", "--unshare-uts", "--unshare-ipc", "--new-session", "--die-with-parent"):
            self.assertIn(flag, argv)

    def test_proc_remounted_for_pid_namespace(self):
        # --unshare-pid alone leaves /proc as whatever --ro-bind / /
        # already bound — the HOST's procfs, reflecting the wrong PID
        # namespace. Verified directly (outside this mocked test) that
        # without --proc /proc, /proc/self resolves to a different PID
        # than the sandboxed process's own — a real, silent correctness
        # bug for any command doing /proc introspection, not just a
        # theoretical concern.
        argv = self._run_and_capture_argv()
        self.assertIn("--proc", argv)
        idx = argv.index("--proc")
        self.assertEqual(argv[idx + 1], "/proc")

    def test_build_sandbox_prefix_supports_extra_ro_binds(self):
        from relay.shell_tool import build_sandbox_prefix
        extra = Path(self.tempdir.name) / "extra-secret-dir"
        extra.mkdir()
        prefix = build_sandbox_prefix(self.project_root, extra_ro_binds=[extra])
        idx = prefix.index(str(extra))
        self.assertEqual(prefix[idx - 1], "--ro-bind")

    def test_build_sandbox_prefix_supports_extra_env_passthrough(self):
        from relay.shell_tool import build_sandbox_prefix
        with patch.dict("os.environ", {"SOME_TOOL_SPECIFIC_VAR": "value"}):
            prefix = build_sandbox_prefix(self.project_root, extra_env_passthrough=["SOME_TOOL_SPECIFIC_VAR"])
        self.assertIn("SOME_TOOL_SPECIFIC_VAR", prefix)

    def test_build_sandbox_prefix_returns_none_without_bwrap(self):
        from relay.shell_tool import build_sandbox_prefix
        with patch.object(shell_tool, "BWRAP_BIN", None):
            self.assertIsNone(build_sandbox_prefix(self.project_root))

    def test_secret_mask_paths_included_when_present(self):
        fake_ssh = Path(self.tempdir.name) / "fake-home" / ".ssh"
        fake_ssh.mkdir(parents=True)
        with patch.object(shell_tool, "_SECRET_MASK_PATHS", [fake_ssh]):
            argv = self._run_and_capture_argv()
        idx = argv.index(str(fake_ssh))
        self.assertEqual(argv[idx - 2:idx], ["--ro-bind", "/dev/null"])


class TestResolveChdir(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tempdir.name) / "home"
        self.project_root = self.home / "adam-selene"
        self.memory_root = self.home / "adam-selene-memory"
        self.outside = Path(self.tempdir.name) / "outside"
        for p in (self.project_root, self.memory_root, self.outside):
            p.mkdir(parents=True)
        self.addCleanup(self.tempdir.cleanup)

    def test_project_root_itself_is_preserved(self):
        result = shell_tool._resolve_chdir(self.project_root, self.project_root, self.memory_root, self.home)
        self.assertEqual(result, self.project_root.resolve())

    def test_nested_dir_under_project_root_is_preserved(self):
        nested = self.project_root / "sub" / "dir"
        nested.mkdir(parents=True)
        result = shell_tool._resolve_chdir(nested, self.project_root, self.memory_root, self.home)
        self.assertEqual(result, nested.resolve())

    def test_memory_root_is_preserved(self):
        result = shell_tool._resolve_chdir(self.memory_root, self.project_root, self.memory_root, self.home)
        self.assertEqual(result, self.memory_root.resolve())

    def test_arbitrary_dir_under_home_falls_back_to_project_root(self):
        elsewhere = self.home / "some-other-dir"
        elsewhere.mkdir()
        result = shell_tool._resolve_chdir(elsewhere, self.project_root, self.memory_root, self.home)
        self.assertEqual(result, self.project_root)

    def test_dir_outside_home_passes_through_unchanged(self):
        result = shell_tool._resolve_chdir(self.outside, self.project_root, self.memory_root, self.home)
        self.assertEqual(result, self.outside.resolve())


@unittest.skipUnless(_HAS_BWRAP, "bubblewrap not installed — skipping real-sandbox integration tests")
class TestRealBwrapIntegration(unittest.TestCase):
    """Actually invokes bwrap — verifies the security properties hold for
    real, not just that the right flags were requested."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.tempdir.name) / "project"
        self.project_root.mkdir()
        (self.project_root / "config").mkdir()
        self.addCleanup(self.tempdir.cleanup)
        self.patchers = [
            patch("relay.shell_tool.config.project_root", return_value=self.project_root),
            patch("relay.shell_tool.config.memory_root", return_value=self.project_root),
        ]
        for p in self.patchers:
            p.start()
            self.addCleanup(p.stop)

    def test_basic_command_runs(self):
        result = shell_tool.run_shell("echo sandboxed-hello")
        self.assertIn("sandboxed-hello", result)

    def test_write_inside_project_root_succeeds(self):
        shell_tool.run_shell("echo written > inside.txt")
        self.assertTrue((self.project_root / "inside.txt").exists())

    def test_write_to_real_host_path_outside_writable_scope_fails(self):
        # /etc sits under the read-only whole-root bind, not under any of
        # the writable regions (project_root, memory_root, the /tmp
        # scratch tmpfs) — a genuine boundary, unlike a path merely
        # sitting elsewhere inside the /tmp tmpfs (which is fully
        # writable/creatable throughout by nature, since it's a tmpfs —
        # not a meaningful boundary to test against).
        result = shell_tool.run_shell("touch /etc/should-not-exist-adam-selene-test.txt 2>&1; echo EXIT:$?")
        self.assertIn("EXIT:1", result)
        self.assertFalse(Path("/etc/should-not-exist-adam-selene-test.txt").exists())

    def test_secrets_env_content_never_readable(self):
        secrets_file = self.project_root / "config" / "secrets.env"
        secrets_file.write_text("REAL_SECRET=must-not-leak")
        result = shell_tool.run_shell("cat config/secrets.env")
        self.assertNotIn("must-not-leak", result)

    def test_env_var_secret_does_not_leak_into_sandbox(self):
        with patch.dict("os.environ", {"FAKE_API_KEY": "sk-must-not-leak-xyz"}):
            result = shell_tool.run_shell("echo VALUE=$FAKE_API_KEY")
        self.assertNotIn("sk-must-not-leak-xyz", result)


if __name__ == "__main__":
    unittest.main()
