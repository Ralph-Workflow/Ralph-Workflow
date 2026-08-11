"""Characterization baseline for filesystem-proportional workspace activity.

The test drives only public persistence and watch lifecycle seams through
in-memory fakes.  It does not touch the host filesystem or a watchdog observer.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.invoke._workspace import WorkspaceMonitor
from ralph.agents.invoke._workspace_change_classifier import WorkspaceChangeClassifier
from ralph.mcp.artifacts.file_backend import FileBackend
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from collections.abc import Dict

    import pytest


class _ActivityBackend(FileBackend):
    """In-memory persistence boundary exposing observable publications."""

    def __init__(self) -> None:
        self._files: Dict[Path, str] = {}
        self.publications: list[tuple[Path, str]] = []
        self.preparations: list[Path] = []

    def exists(self, path: Path) -> bool:
        return path in self._files

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        del parents, exist_ok
        self.preparations.append(path)

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        del encoding
        return self._files[path]

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        del encoding
        self.publications.append((path, content))
        self._files[path] = content

    def read_bytes(self, path: Path) -> bytes:
        return self._files[path].encode()

    def write_bytes(self, path: Path, content: bytes) -> None:
        self._files[path] = content.decode()

    def replace(self, source: Path, destination: Path) -> None:
        self._files[destination] = self._files.pop(source)

    def sync_directory(self, path: Path) -> None:
        del path

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        if missing_ok:
            self._files.pop(path, None)
            return
        del self._files[path]

    def glob(self, path: Path, pattern: str) -> list[Path]:
        del path, pattern
        return []


class _ActivityObserver:
    """In-memory watch boundary exposing recursive registrations."""

    def __init__(self) -> None:
        self.registrations: list[tuple[str, bool]] = []

    def schedule(self, event_handler: object, path: str, recursive: bool = False) -> None:
        del event_handler
        self.registrations.append((path, recursive))

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def join(self, timeout: float | None = None) -> None:
        del timeout


def test_unchanged_workspace_cycle_preserves_bytes_without_new_publication_or_watch_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-1: unchanged cycle leaves final bytes and activity boundaries unchanged."""
    backend = _ActivityBackend()
    observer = _ActivityObserver()
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace._create_watchdog_observer",
        lambda: observer,
    )
    workspace = FsWorkspace(Path("/virtual-ws"), backend=backend)
    monitor = WorkspaceMonitor(Path("/virtual-ws"), classifier=WorkspaceChangeClassifier())

    monitor.start()
    workspace.write("artifacts/result.txt", "alpha")
    monitor.start()
    workspace.write("artifacts/result.txt", "alpha")

    result_path = Path("/virtual-ws/artifacts/result.txt")
    assert backend._files[result_path] == "alpha"
    assert backend.publications == [(result_path, "alpha")]
    assert backend.preparations == [result_path.parent]
    assert observer.registrations == [("/virtual-ws", True)]

    workspace.write("artifacts/result.txt", "beta")

    assert backend._files[result_path] == "beta"
    assert backend.publications == [(result_path, "alpha"), (result_path, "beta")]
    assert observer.registrations == [("/virtual-ws", True)]


def test_independent_monitors_for_one_workspace_share_one_recursive_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-2 regression: equivalent in-process consumers share one root watch."""
    first = _ActivityObserver()
    second = _ActivityObserver()
    observers = iter((first, second))
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace._create_watchdog_observer",
        lambda: next(observers),
    )

    first_monitor = WorkspaceMonitor(Path("/virtual-ws"), classifier=WorkspaceChangeClassifier())
    second_monitor = WorkspaceMonitor(Path("/virtual-ws"), classifier=WorkspaceChangeClassifier())
    first_monitor.start()
    second_monitor.start()

    assert first.registrations == [("/virtual-ws", True)]
    assert second.registrations == []

    first_monitor.dispatch_event(type("Event", (), {"src_path": "/virtual-ws/src/app.py"})())
    assert first_monitor.changed_files == {"/virtual-ws/src/app.py"}
    assert second_monitor.changed_files == {"/virtual-ws/src/app.py"}

    first_monitor.stop()
    assert first.registrations == [("/virtual-ws", True)]
    second_monitor.stop()
