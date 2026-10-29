"""Access to a model's input files, independent of where they are stored."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Protocol


class FileProvider(Protocol):
    def read_bytes(self, path: str) -> bytes: ...


def check_relative_path(path: str) -> str:
    """Input paths are relative, use forward slashes, and never leave the model root."""
    p = PurePosixPath(path)
    if not path or p.is_absolute() or "\\" in path or ".." in p.parts or ":" in path:
        raise ValueError(
            f"file path '{path}' must be relative to the model, use '/' and not contain '..'"
        )
    return path


class DirectoryFiles:
    """Input files in a directory on disk, e.g. an unpacked model."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def read_bytes(self, path: str) -> bytes:
        check_relative_path(path)
        full = (self.root / path).resolve()
        if not full.is_relative_to(self.root):
            raise ValueError(f"file path '{path}' leaves the model directory")
        return full.read_bytes()

    def __repr__(self) -> str:
        return f"DirectoryFiles({str(self.root)!r})"


class MemoryFiles:
    """Input files held in memory, keyed by relative path."""

    def __init__(self, files: dict[str, bytes] | None = None):
        self.files = dict(files or {})

    def read_bytes(self, path: str) -> bytes:
        check_relative_path(path)
        try:
            return self.files[path]
        except KeyError:
            raise FileNotFoundError(path) from None
