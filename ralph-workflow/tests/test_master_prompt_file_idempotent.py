from __future__ import annotations

from pathlib import Path

from ralph.mcp.artifacts.file_backend import FileBackend
from ralph.prompts import master_prompt


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


def test_master_prompt_file_writer_writes_first_content() -> None:
    backend = _CountingBackend()
    destination = Path("/virtual-ws/.agent/tmp/agent_master_prompt.md")

    master_prompt._write_master_prompt_file(destination, "X", backend=backend)

    assert backend.write_text_calls == [(destination, "X")]
    assert backend._files[destination] == "X"


def test_master_prompt_file_writer_regression_skips_identical_parent_preparation() -> None:
    """S-3: an unchanged prompt replay does not mutate its parent directory."""
    backend = _CountingBackend()
    destination = Path("/virtual-ws/.agent/tmp/agent_master_prompt.md")

    master_prompt._write_master_prompt_file(destination, "X", backend=backend)
    master_prompt._write_master_prompt_file(destination, "X", backend=backend)

    assert backend.write_text_calls == [(destination, "X")]
    assert backend.mkdir_calls == [destination.parent]
    assert backend._files[destination] == "X"


def test_product_criteria_sync_regression_skips_identical_parent_preparation() -> None:
    """S-2: an unchanged fallback criteria replay does not touch its parent directory."""
    backend = _CountingBackend()
    workspace_root = Path("/virtual-ws")

    master_prompt._sync_product_criteria_file(
        workspace_root=workspace_root,
        default_product_criteria="criteria",
        backend=backend,
    )
    master_prompt._sync_product_criteria_file(
        workspace_root=workspace_root,
        default_product_criteria="criteria",
        backend=backend,
    )

    criteria_path = workspace_root / ".agent" / "PRODUCT_CRITERIA.md"
    assert [path for path, _content in backend.write_text_calls].count(criteria_path) == 1
    assert backend.mkdir_calls.count(criteria_path.parent) == 1
    assert backend._files[criteria_path] == "criteria"


def test_master_prompt_file_writer_writes_changed_content() -> None:
    backend = _CountingBackend()
    destination = Path("/virtual-ws/.agent/tmp/agent_master_prompt.md")

    master_prompt._write_master_prompt_file(destination, "X", backend=backend)
    master_prompt._write_master_prompt_file(destination, "Y", backend=backend)

    assert backend.write_text_calls == [(destination, "X"), (destination, "Y")]
    assert backend._files[destination] == "Y"
