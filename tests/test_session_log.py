import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from relay import session_log


class TestSessionLog(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.memory_root_patcher = patch("relay.config.memory_root", return_value=self.root)
        self.memory_root_patcher.start()
        self.addCleanup(self.memory_root_patcher.stop)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_index_upsert_is_thread_safe(self):
        started_at = "2026-05-23T12:00:00+00:00"

        def _write(idx: int):
            session_log._index_upsert(
                session_id=f"sid-{idx}",
                started_at=started_at,
                user_id="owner",
                interface="test",
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(_write, range(25)))

        index = json.loads((self.root / "sessions" / "index.json").read_text())
        self.assertEqual(len(index), 25)
        self.assertIn("sid-0", index)
        self.assertIn("sid-24", index)

    def test_shell_audit_append_is_thread_safe(self):
        def _write(idx: int):
            session_log.log_shell_exec(f"echo {idx}", blocked=False, exit_code=0)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(_write, range(20)))

        audit_file = self.root / "sessions" / "shell_audit.jsonl"
        lines = audit_file.read_text().splitlines()
        self.assertEqual(len(lines), 20)
        parsed = [json.loads(line) for line in lines]
        self.assertEqual({entry["command"] for entry in parsed}, {f"echo {idx}" for idx in range(20)})


if __name__ == "__main__":
    unittest.main()
