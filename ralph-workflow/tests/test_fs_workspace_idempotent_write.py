from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import FileBackend
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from collections.abc import Dict


class _CountingBackend(FileBackend):
    def __init__(self) -> None:
        self._files: Dict[Path, str] = {}
        self.write_text_calls: list[tuple[Path, str]] = []
        self.mkdir_calls: list[Path] = []

    def exists(self, path: Path) -> bool:
        return path in self._files

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        del parents, exist_ok
        self.mkdir_calls.append(path)

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        del encoding
        return self._files[path]

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        del encoding
        self.write_text_calls.append((path, content))
        self._files[path] = content

    def replace(self, source: Path, destination: Path) -> None:
        del source, destination

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        if missing_ok:
            self._files.pop(path, None)
            return
        del self._files[path]

    def glob(self, path: Path, pattern: str) -> list[Path]:
        del path, pattern
        return []


def test_fresh_write_creates_file_with_expected_content() -> None:
    backend = _CountingBackend()
    ws = FsWorkspace(Path("/virtual-ws"), backend=backend)

    ws.write("output.txt", "alpha")

    assert len(backend.write_text_calls) == 1
    assert backend.write_text_calls[0][1] == "alpha"
    assert backend._files[Path("/virtual-ws/output.txt")] == "alpha"


def test_identical_write_is_skipped() -> None:
    backend = _CountingBackend()
    ws = FsWorkspace(Path("/virtual-ws"), backend=backend)

    ws.write("output.txt", "alpha")
    ws.write("output.txt", "alpha")

    assert len(backend.write_text_calls) == 1
    assert backend.write_text_calls[0][1] == "alpha"
    assert backend._files[Path("/virtual-ws/output.txt")] == "alpha"


def test_identical_write_skips_parent_directory_creation() -> None:
    backend = _CountingBackend()
    ws = FsWorkspace(Path("/virtual-ws"), backend=backend)

    ws.write("nested/output.txt", "alpha")
    ws.write("nested/output.txt", "alpha")

    assert backend.write_text_calls == [(Path("/virtual-ws/nested/output.txt"), "alpha")]
    assert backend.mkdir_calls == [Path("/virtual-ws/nested")]


def test_changed_write_persists() -> None:
    backend = _CountingBackend()
    ws = FsWorkspace(Path("/virtual-ws"), backend=backend)

    ws.write("output.txt", "alpha")
    ws.write("output.txt", "beta")

    assert len(backend.write_text_calls) == 2
    assert backend.write_text_calls[1][1] == "beta"
    assert backend._files[Path("/virtual-ws/output.txt")] == "beta"


def test_mkdir_is_routed_through_injected_backend() -> None:
    backend = _CountingBackend()
    ws = FsWorkspace(Path("/virtual-ws"), backend=backend)

    ws.write("a/b/c/deep.txt", "content")

    assert any(path == Path("/virtual-ws/a/b/c") for path in backend.mkdir_calls)
    assert backend._files[Path("/virtual-ws/a/b/c/deep.txt")] == "content"


def test_workspace_round_trip_uses_the_injected_file_backend() -> None:
    backend = _CountingBackend()
    ws = FsWorkspace(Path("/virtual-ws"), backend=backend)

    ws.write("output.txt", "alpha")

    assert ws.read("output.txt") == "alpha"


def test_write_signature_returns_none() -> None:
    backend = _CountingBackend()
    ws = FsWorkspace(Path("/virtual-ws"), backend=backend)

    result = ws.write("file.txt", "data")

    assert result is None
