import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from relay import self_management


class TestSelfManagement(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.creds_patcher = patch.object(self_management, "CREDENTIALS_DIR", self.root)
        self.creds_patcher.start()
        self.addCleanup(self.creds_patcher.stop)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_store_credential_aborts_on_vault_failure(self):
        with patch("relay.self_management.vault_set", side_effect=[
            {"success": True},
            {"success": False, "error": "vault down"},
        ]):
            result = self_management.store_credential("github", {"token": "abc", "user": "dino"})

        self.assertFalse(result["success"])
        self.assertIn("Vault persistence failed", result["error"])
        cred_file = self.root / "github" / "credentials.json"
        self.assertFalse(cred_file.exists())

    def test_store_credential_writes_file_after_vault_success(self):
        with patch("relay.self_management.vault_set", return_value={"success": True}):
            result = self_management.store_credential("github", {"token": "abc"})

        self.assertTrue(result["success"])
        cred_file = self.root / "github" / "credentials.json"
        self.assertTrue(cred_file.exists())
        self.assertEqual(json.loads(cred_file.read_text()), {"token": "abc"})


if __name__ == "__main__":
    unittest.main()
