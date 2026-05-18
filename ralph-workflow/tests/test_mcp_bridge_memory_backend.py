"""MemoryBackend — in-memory FileBackend implementation for testing."""

from __future__ import annotations

import fnmatch
from pathlib import Path


class MemoryBackend:
    """In-memory FileBackend implementation for testing."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def exists(self, path: Path) -> bool:
        path_str = str(path)
        return path_str in self._data or any(
            k.startswith(path_str + "/") for k in self._data
        )

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        pass

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        return self._data[str(path)]

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        self._data[str(path)] = content

    def replace(self, source: Path, destination: Path) -> None:
        self._data[str(destination)] = self._data.pop(str(source))

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        key = str(path)
        if key in self._data:
            del self._data[key]
        elif not missing_ok:
            raise FileNotFoundError(path)

    def glob(self, path: Path, pattern: str) -> list[Path]:
        return [
            Path(k)
            for k in self._data
            if Path(k).parent == path and fnmatch.fnmatch(Path(k).name, pattern)
        ]
