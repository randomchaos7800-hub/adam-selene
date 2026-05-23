import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from relay.agenda import Agenda
from relay import working_memory


class TestResearchState(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.memory_root_patcher = patch("relay.config.memory_root", return_value=self.root)
        self.memory_root_patcher.start()
        self.addCleanup(self.memory_root_patcher.stop)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_agenda_add_is_thread_safe(self):
        agenda = Agenda()

        def _add(idx: int):
            agenda.add(topic=f"topic {idx}", priority=2)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(_add, range(20)))

        items = json.loads((self.root / "agenda.json").read_text())
        self.assertEqual(len(items), 20)
        self.assertEqual({item["topic"] for item in items}, {f"topic {idx}" for idx in range(20)})

    def test_agenda_duplicate_detection_is_atomic(self):
        agenda = Agenda()

        def _add(_idx: int):
            return agenda.add(topic="same topic", priority=1)

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(_add, range(10)))

        added_count = sum(1 for result in results if result["added"])
        self.assertEqual(added_count, 1)
        items = json.loads((self.root / "agenda.json").read_text())
        self.assertEqual(len(items), 1)

    def test_failure_log_is_thread_safe(self):
        def _log(idx: int):
            working_memory.log_failure(f"context {idx}", f"error {idx}")

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(_log, range(20)))

        failures = json.loads((self.root / "failure_log.json").read_text())
        self.assertEqual(len(failures), 20)
        self.assertEqual({entry["context"] for entry in failures}, {f"context {idx}" for idx in range(20)})


if __name__ == "__main__":
    unittest.main()
