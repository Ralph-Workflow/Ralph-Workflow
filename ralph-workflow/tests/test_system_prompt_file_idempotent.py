from __future__ import annotations

from pathlib import Path

from ralph.mcp.artifacts.file_backend import FileBackend
from ralph.prompts import system_prompt


class _CountingBackend(FileBackend):
    def __init__(self) -> None:
        self._files: dict[Path, str] = {}
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


def test_system_prompt_file_writer_writes_first_content() -> None:
    backend = _CountingBackend()
    destination = Path("/virtual-ws/.agent/tmp/agent_system_prompt.md")

    system_prompt._write_system_prompt_file(destination, "X", backend=backend)

    assert backend.write_text_calls == [(destination, "X")]
    assert backend._files[destination] == "X"


def test_system_prompt_file_writer_skips_identical_content() -> None:
    backend = _CountingBackend()
    destination = Path("/virtual-ws/.agent/tmp/agent_system_prompt.md")

    system_prompt._write_system_prompt_file(destination, "X", backend=backend)
    system_prompt._write_system_prompt_file(destination, "X", backend=backend)

    assert len(backend.write_text_calls) == 1
    assert backend._files[destination] == "X"


def test_system_prompt_file_writer_writes_changed_content() -> None:
    backend = _CountingBackend()
    destination = Path("/virtual-ws/.agent/tmp/agent_system_prompt.md")

    system_prompt._write_system_prompt_file(destination, "X", backend=backend)
    system_prompt._write_system_prompt_file(destination, "Y", backend=backend)

    assert backend.write_text_calls == [(destination, "X"), (destination, "Y")]
    assert backend._files[destination] == "Y"
