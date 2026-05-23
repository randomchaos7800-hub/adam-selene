"""Shared file-system primitives: exclusive locking and atomic writes."""

import fcntl
import json
import os
import tempfile
from copy import deepcopy
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T")

def _lock_file_for(path: Path) -> Path:
    return path.parent / f".{path.name}.lock"


@contextmanager
def exclusive_lock(path: Path):
    lock_file = _lock_file_for(path)
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_file, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as tmp:
        tmp.write(content)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


def read_json_file(path: Path, default):
    with exclusive_lock(path):
        if not path.exists():
            return deepcopy(default)
        return json.loads(path.read_text())


def write_json_file(path: Path, data) -> None:
    with exclusive_lock(path):
        atomic_write_text(path, json.dumps(data, indent=2))


def update_json_file(path: Path, default, updater: Callable[[dict | list], T]) -> T:
    with exclusive_lock(path):
        if path.exists():
            data = json.loads(path.read_text())
        else:
            data = deepcopy(default)
        result = updater(data)
        atomic_write_text(path, json.dumps(data, indent=2))
        return result
