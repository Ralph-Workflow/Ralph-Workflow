from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import FileBackend
from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline.state import PipelineState

if TYPE_CHECKING:
    from collections.abc import Dict


class _ReplacingCountingBackend(FileBackend):
    def __init__(self) -> None:
        self._files: Dict[Path, str] = {}
        self.write_text_calls: list[tuple[Path, str]] = []
        self.replace_calls: list[tuple[Path, Path]] = []
        self.mkdir_calls: list[Path] = []
        self.sync_directory_calls: list[Path] = []

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
        self.replace_calls.append((source, destination))
        self._files[destination] = self._files.pop(source)

    def sync_directory(self, path: Path) -> None:
        self.sync_directory_calls.append(path)

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        if missing_ok:
            self._files.pop(path, None)
            return
        del self._files[path]

    def glob(self, path: Path, pattern: str) -> list[Path]:
        del path, pattern
        return []


def test_checkpoint_save_regression_writes_and_replaces_on_first_save() -> None:
    backend = _ReplacingCountingBackend()
    dest = Path("/virtual-ws/.agent/checkpoint.json")
    tmp_prefix = f"{dest.with_suffix('.tmp').name}."
    state = PipelineState(phase="planning")
    serialized = state.model_dump_json(indent=2)

    ckpt.save(state, dest, backend=backend)

    assert len(backend.write_text_calls) == 1
    staging_path, written = backend.write_text_calls[0]
    assert staging_path.name.startswith(tmp_prefix)
    assert staging_path.parent == dest.parent
    assert written == serialized
    assert backend.replace_calls == [(staging_path, dest)]
    assert backend._files[dest] == serialized
    assert staging_path not in backend._files
    assert dest.parent in backend.mkdir_calls
    assert backend.sync_directory_calls == [dest.parent]


def test_checkpoint_save_regression_skips_replace_when_state_identical() -> None:
    backend = _ReplacingCountingBackend()
    dest = Path("/virtual-ws/.agent/checkpoint.json")
    tmp = dest.with_suffix(".tmp")
    state = PipelineState(phase="planning")

    ckpt.save(state, dest, backend=backend)
    writes_after_first = len(backend.write_text_calls)
    replaces_after_first = len(backend.replace_calls)
    mkdirs_after_first = len(backend.mkdir_calls)
    syncs_after_first = len(backend.sync_directory_calls)
    stored_after_first = backend._files[dest]

    ckpt.save(state, dest, backend=backend)

    assert len(backend.write_text_calls) == writes_after_first
    assert len(backend.replace_calls) == replaces_after_first
    assert len(backend.mkdir_calls) == mkdirs_after_first
    assert len(backend.sync_directory_calls) == syncs_after_first
    assert backend._files[dest] == stored_after_first
    assert tmp not in backend._files


def test_checkpoint_save_regression_writes_and_replaces_when_state_changed() -> None:
    backend = _ReplacingCountingBackend()
    dest = Path("/virtual-ws/.agent/checkpoint.json")
    tmp = dest.with_suffix(".tmp")
    initial = PipelineState(phase="planning")
    changed = PipelineState(phase="development")

    ckpt.save(initial, dest, backend=backend)
    writes_after_first = len(backend.write_text_calls)
    replaces_after_first = len(backend.replace_calls)
    syncs_after_first = len(backend.sync_directory_calls)

    ckpt.save(changed, dest, backend=backend)

    assert len(backend.write_text_calls) == writes_after_first + 1
    assert len(backend.replace_calls) == replaces_after_first + 1
    assert len(backend.sync_directory_calls) == syncs_after_first + 1
    assert backend._files[dest] == changed.model_dump_json(indent=2)
    assert tmp not in backend._files
