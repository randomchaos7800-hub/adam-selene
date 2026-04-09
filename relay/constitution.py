import hashlib
from pathlib import Path

from relay import config


class ConstitutionTamperError(Exception):
    """Raised when constitution hash doesn't match."""
    pass


class ConstitutionLoader:
    def __init__(self, memory_path: Path = None):
        """
        memory_path: Path to agent memory root.
        Constitution lives at: {memory_path}/constitution/L0.md
        Hash lives at: {memory_path}/constitution/L0.hash
        """
        self.memory_path = Path(memory_path) if memory_path else config.memory_root()
        self.constitution_dir = self.memory_path / "constitution"
        self.constitution_file = self.constitution_dir / "L0.md"
        self.hash_file = self.constitution_dir / "L0.hash"

    def load(self, validate: bool = True) -> str:
        """
        Load constitution content.
        If validate=True and hash doesn't match, raise ConstitutionTamperError
        Returns the constitution text.
        """
        if not self.constitution_file.exists():
            raise FileNotFoundError(f"Constitution file not found: {self.constitution_file}")

        content = self.constitution_file.read_text(encoding="utf-8")

        if validate:
            if not self.hash_file.exists():
                raise FileNotFoundError(f"Hash file not found: {self.hash_file}")

            stored_hash = self.hash_file.read_text(encoding="utf-8").strip()
            current_hash = self.get_hash()

            if stored_hash != current_hash:
                raise ConstitutionTamperError(
                    f"Constitution hash mismatch. Expected: {stored_hash}, Got: {current_hash}"
                )

        return content

    def exists(self) -> bool:
        """Check if constitution files exist."""
        return self.constitution_file.exists() and self.hash_file.exists()

    def initialize(self, content: str) -> None:
        """
        Create constitution files.
        Write content to L0.md
        Generate SHA256 hash, write to L0.hash
        """
        self.constitution_dir.mkdir(parents=True, exist_ok=True)
        self.constitution_file.write_text(content, encoding="utf-8")
        self.update_hash()

    def get_hash(self) -> str:
        """Get current hash of L0.md content."""
        if not self.constitution_file.exists():
            raise FileNotFoundError(f"Constitution file not found: {self.constitution_file}")

        content = self.constitution_file.read_text(encoding="utf-8")
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def update_hash(self) -> None:
        """Regenerate hash from current L0.md content."""
        if not self.constitution_file.exists():
            raise FileNotFoundError(f"Constitution file not found: {self.constitution_file}")

        self.constitution_dir.mkdir(parents=True, exist_ok=True)
        current_hash = self.get_hash()
        self.hash_file.write_text(current_hash, encoding="utf-8")
