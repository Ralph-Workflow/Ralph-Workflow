"""Black-box regression tests for idempotent capability-state persistence."""

from __future__ import annotations

from pathlib import Path

from ralph.mcp.artifacts.file_backend import FileBackend
from ralph.skills._capability_entry import CapabilityEntry
from ralph.skills._capability_state import CapabilityState
from ralph.skills._capability_status import CapabilityStatus
from ralph.skills._state_store import save_capability_state


class _CountingBackend(FileBackend):
    def __init__(self) -> None:
        self.files: dict[Path, str] = {}
        self.write_text_calls: list[tuple[Path, str]] = []
        self.mkdir_calls: list[Path] = []

    def exists(self, path: Path) -> bool:
        return path in self.files

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        del parents, exist_ok
        self.mkdir_calls.append(path)

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        del encoding
        return self.files[path]

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        del encoding
        self.write_text_calls.append((path, content))
        self.files[path] = content

    def replace(self, source: Path, destination: Path) -> None:
        self.files[destination] = self.files.pop(source)

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        if missing_ok:
            self.files.pop(path, None)
            return
        del self.files[path]

    def glob(self, path: Path, pattern: str) -> list[Path]:
        del path, pattern
        return []


def test_capability_state_regression_first_save_writes_serialized_state() -> None:
    """Step 4: a first save performs one backend write with unchanged JSON bytes."""
    backend = _CountingBackend()
    path = Path("/virtual-config/ralph-workflow-capabilities.json")
    state = CapabilityState()

    save_capability_state(state, path, backend=backend)

    assert backend.mkdir_calls == [path.parent]
    assert backend.write_text_calls == [(path, state.model_dump_json(indent=2))]
    assert backend.files[path] == state.model_dump_json(indent=2)


def test_capability_state_regression_identical_save_skips_physical_write() -> None:
    """Step 4: a byte-identical second save performs no additional backend write."""
    backend = _CountingBackend()
    path = Path("/virtual-config/ralph-workflow-capabilities.json")
    state = CapabilityState()

    save_capability_state(state, path, backend=backend)
    save_capability_state(state, path, backend=backend)

    assert backend.write_text_calls == [(path, state.model_dump_json(indent=2))]
    assert backend.files[path] == state.model_dump_json(indent=2)


def test_capability_state_regression_changed_save_writes_updated_state() -> None:
    """Step 4: changing the state performs a second write with the new JSON bytes."""
    backend = _CountingBackend()
    path = Path("/virtual-config/ralph-workflow-capabilities.json")
    initial = CapabilityState()
    changed = CapabilityState(
        web_search=CapabilityEntry(status=CapabilityStatus.INSTALLED_HEALTHY)
    )

    save_capability_state(initial, path, backend=backend)
    save_capability_state(changed, path, backend=backend)

    assert backend.write_text_calls == [
        (path, initial.model_dump_json(indent=2)),
        (path, changed.model_dump_json(indent=2)),
    ]
    assert backend.files[path] == changed.model_dump_json(indent=2)
