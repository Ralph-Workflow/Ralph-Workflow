from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class FakeFileBackend:
    def __init__(self) -> None:
        self.files: dict[Path, str] = {}
        self.directories: set[Path] = set()

    def exists(self, path: Path) -> bool:
        return path in self.files or path in self.directories

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        self.directories.add(path)

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        return self.files[path]

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        self.files[path] = content

    def replace(self, source: Path, destination: Path) -> None:
        self.files[destination] = self.files.pop(source)

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        if missing_ok:
            self.files.pop(path, None)
            return
        del self.files[path]

    def glob(self, path: Path, pattern: str) -> list[Path]:
        suffix = pattern.replace("*", "")
        return [
            candidate
            for candidate in self.files
            if candidate.parent == path and candidate.name.endswith(suffix)
        ]
