import hashlib
import tempfile
import unittest
from pathlib import Path

from relay.constitution import ConstitutionLoader, ConstitutionTamperError


class TestConstitutionLoader(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.memory_dir = Path(self.tempdir.name)
        self.loader = ConstitutionLoader(self.memory_dir)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_initialize_creates_both_files(self):
        self.loader.initialize("This is the constitution")
        self.assertTrue(self.loader.constitution_file.exists())
        self.assertTrue(self.loader.hash_file.exists())

    def test_load_returns_correct_content(self):
        self.loader.initialize("This is the constitution")
        self.assertEqual(self.loader.load(validate=False), "This is the constitution")

    def test_load_with_validation_passes_when_hash_matches(self):
        self.loader.initialize("This is the constitution")
        self.assertEqual(self.loader.load(validate=True), "This is the constitution")

    def test_load_raises_tamper_error_when_content_modified(self):
        self.loader.initialize("This is the constitution")
        self.loader.constitution_file.write_text("Modified content", encoding="utf-8")
        with self.assertRaises(ConstitutionTamperError):
            self.loader.load(validate=True)

    def test_exists_variants(self):
        self.assertFalse(self.loader.exists())
        self.loader.constitution_dir.mkdir(parents=True, exist_ok=True)
        self.loader.constitution_file.write_text("content", encoding="utf-8")
        self.assertFalse(self.loader.exists())
        self.loader.hash_file.write_text("hash", encoding="utf-8")
        self.assertTrue(self.loader.exists())

    def test_get_hash_returns_sha256_hex(self):
        content = "This is the constitution"
        self.loader.initialize(content)
        expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
        self.assertEqual(self.loader.get_hash(), expected)

    def test_update_hash_regenerates_hash(self):
        self.loader.initialize("Original content")
        self.loader.constitution_file.write_text("Modified content", encoding="utf-8")
        self.loader.update_hash()
        self.assertEqual(
            self.loader.hash_file.read_text(encoding="utf-8"),
            self.loader.get_hash(),
        )

    def test_initialize_creates_directory_if_missing(self):
        self.assertFalse(self.loader.constitution_dir.exists())
        self.loader.initialize("content")
        self.assertTrue(self.loader.constitution_dir.exists())

    def test_load_without_validate_ignores_hash(self):
        self.loader.initialize("This is the constitution")
        self.loader.constitution_file.write_text("Different content", encoding="utf-8")
        self.assertEqual(self.loader.load(validate=False), "Different content")

    def test_missing_files_raise(self):
        with self.assertRaises(FileNotFoundError):
            self.loader.load()

        self.loader.constitution_dir.mkdir(parents=True, exist_ok=True)
        self.loader.constitution_file.write_text("content", encoding="utf-8")
        with self.assertRaises(FileNotFoundError):
            self.loader.load(validate=True)


if __name__ == "__main__":
    unittest.main()
