import pytest
import tempfile
import hashlib
from pathlib import Path
from relay.constitution import ConstitutionLoader, ConstitutionTamperError


@pytest.fixture
def temp_memory_dir():
    """Create a temporary memory directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


class TestConstitutionLoader:
    def test_initialize_creates_both_files(self, temp_memory_dir):
        """Test that initialize creates both L0.md and L0.hash files."""
        loader = ConstitutionLoader(temp_memory_dir)
        test_content = "This is the constitution"

        loader.initialize(test_content)

        assert loader.constitution_file.exists()
        assert loader.hash_file.exists()

    def test_load_returns_correct_content(self, temp_memory_dir):
        """Test that load returns the correct constitution content."""
        loader = ConstitutionLoader(temp_memory_dir)
        test_content = "This is the constitution"

        loader.initialize(test_content)
        loaded_content = loader.load(validate=False)

        assert loaded_content == test_content

    def test_load_with_validation_passes_when_hash_matches(self, temp_memory_dir):
        """Test that load with validation passes when hash matches."""
        loader = ConstitutionLoader(temp_memory_dir)
        test_content = "This is the constitution"

        loader.initialize(test_content)
        loaded_content = loader.load(validate=True)

        assert loaded_content == test_content

    def test_load_raises_tamper_error_when_content_modified(self, temp_memory_dir):
        """Test that load raises ConstitutionTamperError when content is modified."""
        loader = ConstitutionLoader(temp_memory_dir)
        test_content = "This is the constitution"

        loader.initialize(test_content)

        # Modify the constitution content
        loader.constitution_file.write_text("Modified content", encoding="utf-8")

        with pytest.raises(ConstitutionTamperError):
            loader.load(validate=True)

    def test_exists_returns_true_when_both_files_exist(self, temp_memory_dir):
        """Test that exists() returns True when both files exist."""
        loader = ConstitutionLoader(temp_memory_dir)
        test_content = "This is the constitution"

        loader.initialize(test_content)
        assert loader.exists() is True

    def test_exists_returns_false_when_files_missing(self, temp_memory_dir):
        """Test that exists() returns False when files are missing."""
        loader = ConstitutionLoader(temp_memory_dir)
        assert loader.exists() is False

    def test_exists_returns_false_when_only_constitution_exists(self, temp_memory_dir):
        """Test that exists() returns False when only constitution file exists."""
        loader = ConstitutionLoader(temp_memory_dir)
        loader.constitution_dir.mkdir(parents=True, exist_ok=True)
        loader.constitution_file.write_text("content", encoding="utf-8")

        assert loader.exists() is False

    def test_exists_returns_false_when_only_hash_exists(self, temp_memory_dir):
        """Test that exists() returns False when only hash file exists."""
        loader = ConstitutionLoader(temp_memory_dir)
        loader.constitution_dir.mkdir(parents=True, exist_ok=True)
        loader.hash_file.write_text("somehash", encoding="utf-8")

        assert loader.exists() is False

    def test_get_hash_returns_sha256_hex(self, temp_memory_dir):
        """Test that get_hash returns the correct SHA256 hex digest."""
        loader = ConstitutionLoader(temp_memory_dir)
        test_content = "This is the constitution"

        loader.initialize(test_content)
        expected_hash = hashlib.sha256(test_content.encode("utf-8")).hexdigest()
        actual_hash = loader.get_hash()

        assert actual_hash == expected_hash

    def test_get_hash_consistency(self, temp_memory_dir):
        """Test that get_hash is consistent across multiple calls."""
        loader = ConstitutionLoader(temp_memory_dir)
        test_content = "This is the constitution"

        loader.initialize(test_content)
        hash1 = loader.get_hash()
        hash2 = loader.get_hash()

        assert hash1 == hash2

    def test_update_hash_regenerates_hash(self, temp_memory_dir):
        """Test that update_hash regenerates the hash file."""
        loader = ConstitutionLoader(temp_memory_dir)
        test_content = "Original content"

        loader.initialize(test_content)
        original_hash = loader.hash_file.read_text(encoding="utf-8")

        # Modify content
        new_content = "Modified content"
        loader.constitution_file.write_text(new_content, encoding="utf-8")

        # Hash should be different now
        current_hash = loader.get_hash()
        assert current_hash != original_hash

        # Update hash
        loader.update_hash()
        updated_hash = loader.hash_file.read_text(encoding="utf-8")

        assert updated_hash == current_hash

    def test_initialize_creates_directory_if_missing(self, temp_memory_dir):
        """Test that initialize creates the constitution directory if it doesn't exist."""
        loader = ConstitutionLoader(temp_memory_dir)
        assert not loader.constitution_dir.exists()

        loader.initialize("content")

        assert loader.constitution_dir.exists()

    def test_load_without_validate_ignores_hash(self, temp_memory_dir):
        """Test that load without validation ignores hash mismatches."""
        loader = ConstitutionLoader(temp_memory_dir)
        test_content = "This is the constitution"

        loader.initialize(test_content)
        # Modify the content to break the hash
        loader.constitution_file.write_text("Different content", encoding="utf-8")

        # Should not raise an error when validate=False
        loaded_content = loader.load(validate=False)
        assert loaded_content == "Different content"

    def test_load_raises_file_not_found_when_constitution_missing(self, temp_memory_dir):
        """Test that load raises FileNotFoundError when constitution file is missing."""
        loader = ConstitutionLoader(temp_memory_dir)

        with pytest.raises(FileNotFoundError):
            loader.load()

    def test_load_raises_file_not_found_when_hash_missing_with_validation(self, temp_memory_dir):
        """Test that load raises FileNotFoundError when hash file is missing with validation."""
        loader = ConstitutionLoader(temp_memory_dir)
        loader.constitution_dir.mkdir(parents=True, exist_ok=True)
        loader.constitution_file.write_text("content", encoding="utf-8")

        with pytest.raises(FileNotFoundError):
            loader.load(validate=True)
