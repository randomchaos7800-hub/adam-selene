import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from relay.fact_check import check_claims


class TestFactCheck(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.patcher = patch("relay.fact_check.config.project_root", return_value=self.root)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.addCleanup(self.tempdir.cleanup)

    def test_no_claim_returns_text_unchanged(self):
        text = "Here's some general advice about deployment strategies."
        self.assertEqual(check_claims(text), text)

    def test_claimed_path_that_exists_passes_silently(self):
        (self.root / "script.py").write_text("print('hi')")
        text = "I created `script.py` for you."
        self.assertEqual(check_claims(text), text)

    def test_claimed_path_that_does_not_exist_is_flagged(self):
        text = "I created `deploy.sh` for you."
        result = check_claims(text)
        self.assertIn("FACT-CHECK FAILED", result)
        self.assertIn("deploy.sh", result)

    def test_bare_slash_path_with_claim_verb_is_checked(self):
        text = "I've written the config to /etc/myapp/config.yaml already."
        result = check_claims(text)
        self.assertIn("FACT-CHECK FAILED", result)

    def test_home_relative_path_resolves_via_expanduser(self):
        # ~/... paths resolve against the real home dir, not project_root —
        # just confirm a nonexistent one gets flagged without crashing.
        text = "Saved it to `~/.config/nonexistent-app/config.toml`."
        result = check_claims(text)
        self.assertIn("FACT-CHECK FAILED", result)

    def test_path_mentioned_without_claim_verb_is_not_checked(self):
        text = "You might want to look at `config.yaml` for the settings."
        result = check_claims(text)
        self.assertNotIn("FACT-CHECK FAILED", result)

    def test_path_without_extension_is_ignored(self):
        text = "I created a new directory called `scripts` for this."
        result = check_claims(text)
        self.assertNotIn("FACT-CHECK FAILED", result)

    def test_multiple_missing_paths_all_listed(self):
        text = "I created `a.py` and also wrote `b.py` for this task."
        result = check_claims(text)
        self.assertIn("a.py", result)
        self.assertIn("b.py", result)

    def test_duplicate_claim_of_same_path_listed_once_in_warning(self):
        text = "I created `missing.py`. Yes, `missing.py` is definitely created."
        result = check_claims(text)
        warning_block = result.split("FACT-CHECK FAILED")[1]
        self.assertEqual(warning_block.count("missing.py"), 1)

    def test_empty_text_returns_unchanged(self):
        self.assertEqual(check_claims(""), "")

    def test_mix_of_existing_and_missing_flags_only_missing(self):
        (self.root / "real.py").write_text("x = 1")
        text = "I created `real.py` and also `fake.py` for this."
        result = check_claims(text)
        self.assertIn("fake.py", result)
        # real.py appears once in the original text, not duplicated into the warning
        self.assertEqual(result.count("real.py"), 1)


if __name__ == "__main__":
    unittest.main()
