from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.workspace import MemoryWorkspace
from ralph.workspace.awareness import awareness_for_workspace, release_workspace_awareness
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.probe import (
    DEFAULT_MAX_SNAPSHOT_FILES,
    DefaultWorkspaceChangeProbe,
    WorkspaceChangeProbe,
    take_workspace_snapshot,
)

if TYPE_CHECKING:
    from collections.abc import Iterable


class _StubStore:
    def __init__(self, dirty: list[str]) -> None:
        self._dirty = dirty

    def peek_dirty_paths(self) -> list[str]:
        return list(self._dirty)


class _StubExploreIndex:
    def __init__(self, dirty: list[str]) -> None:
        self.store = _StubStore(dirty)


class _TrackedMemoryWorkspace(MemoryWorkspace):
    def __init__(self) -> None:
        super().__init__()
        self.tracked: list[str] = []

    def dirty_paths(self) -> list[str]:
        return list(self.tracked)


class _VersionedMemoryWorkspace(MemoryWorkspace):
    def __init__(self) -> None:
        super().__init__()
        self.version_val: int = 1

    def version(self) -> int:
        return self.version_val


class _BoundedIterWorkspace(MemoryWorkspace):
    def __init__(self) -> None:
        super().__init__()
        self.bounded_calls: list[int] = []

    def iter_files_bounded(self, base: str, *, limit: int) -> Iterable[str]:
        self.bounded_calls.append(limit)
        return ("file1.txt", "file2.txt")


def test_probe_protocol_conformance() -> None:
    probe = DefaultWorkspaceChangeProbe()
    assert isinstance(probe, WorkspaceChangeProbe)
    assert DEFAULT_MAX_SNAPSHOT_FILES > 0


def test_probe_detects_dotfile_env_change() -> None:
    workspace = MemoryWorkspace()
    workspace.write(".env", "PORT=8080\n")

    snapshot_initial = take_workspace_snapshot(workspace)
    assert ".env" in snapshot_initial
    assert "env" not in snapshot_initial

    probe = DefaultWorkspaceChangeProbe()
    probe.observe_before("dev", workspace)

    workspace.write(".env", "PORT=9090\n")
    assert probe.has_changed("dev", workspace) is True

    snapshot_after = take_workspace_snapshot(workspace)
    assert snapshot_after[".env"] != snapshot_initial[".env"]


def test_probe_ignores_agent_and_git_internal_roots() -> None:
    workspace = MemoryWorkspace()
    workspace.write("src/app.py", "print('hello')\n")

    probe = DefaultWorkspaceChangeProbe()
    probe.observe_before("dev", workspace)

    workspace.write(".agent/state.db", "state")
    workspace.write(".agent/logs/execution.log", "log")
    workspace.write(".git/HEAD", "ref: refs/heads/main")
    workspace.write(".git/index", "binary")
    workspace.write(".idea/workspace.xml", "<idea/>")
    workspace.write(".vscode/settings.json", "{}")
    workspace.write("__pycache__/module.cpython-314.pyc", "bytecode")
    workspace.write(".pytest_cache/v/cache/nodeids", "[]")
    workspace.write(".ruff_cache/cache", "cache")
    workspace.write(".mypy_cache/cache", "cache")
    workspace.write("src/app.py.swp", "swap")
    workspace.write("src/app.py~", "backup")

    assert probe.has_changed("dev", workspace) is False

    snapshot = take_workspace_snapshot(workspace)
    assert "src/app.py" in snapshot
    assert not any(k.startswith(".agent") for k in snapshot)
    assert not any(k.startswith(".git") for k in snapshot)
    assert not any(k.startswith(".idea") for k in snapshot)
    assert not any(k.startswith(".vscode") for k in snapshot)
    assert not any(k.startswith("__pycache__") for k in snapshot)
    assert not any(k.startswith(".pytest_cache") for k in snapshot)
    assert not any(k.endswith((".pyc", ".swp", "~")) for k in snapshot)


def test_probe_unchanged_workspace_returns_false() -> None:
    workspace = MemoryWorkspace()
    workspace.write("src/app.py", "print('hello')\n")
    workspace.write(".env", "KEY=VAL\n")

    probe = DefaultWorkspaceChangeProbe()
    probe.observe_before("dev", workspace)

    assert probe.has_changed("dev", workspace) is False


def test_probe_bounded_observation_max_files() -> None:
    workspace = MemoryWorkspace()
    for i in range(50):
        workspace.write(f"file_{i:03d}.txt", f"content_{i}\n")

    snap = take_workspace_snapshot(workspace, max_files=10)
    assert len(snap) == 10

    probe = DefaultWorkspaceChangeProbe(max_files=10)
    probe.observe_before("dev", workspace)
    assert probe.has_changed("dev", workspace) is False


def test_probe_bounded_observation_with_tracked_dirty_paths() -> None:
    workspace = _TrackedMemoryWorkspace()
    for i in range(20):
        workspace.write(f"file_{i:03d}.txt", f"content_{i}\n")

    probe = DefaultWorkspaceChangeProbe()
    probe.observe_before("dev", workspace)

    assert probe.has_changed("dev", workspace) is False

    probe.observe_before("dev", workspace)
    workspace.write(".env", "PORT=3000\n")
    workspace.tracked.append(".env")

    assert probe.has_changed("dev", workspace) is True


def test_probe_bounded_observation_with_explore_index() -> None:
    workspace = MemoryWorkspace()
    dirty_list: list[str] = []
    workspace.explore_index = _StubExploreIndex(dirty_list)

    probe = DefaultWorkspaceChangeProbe()
    probe.observe_before("dev", workspace)

    assert probe.has_changed("dev", workspace) is False

    probe.observe_before("dev", workspace)
    workspace.write(".env", "NEW=1\n")
    dirty_list.append(".env")

    assert probe.has_changed("dev", workspace) is True


def test_probe_bounded_observation_with_workspace_version() -> None:
    workspace = _VersionedMemoryWorkspace()

    probe = DefaultWorkspaceChangeProbe()
    probe.observe_before("dev", workspace)

    assert probe.has_changed("dev", workspace) is False

    probe.observe_before("dev", workspace)
    workspace.version_val = 2
    assert probe.has_changed("dev", workspace) is True


def test_probe_bounded_observation_with_iter_files_bounded() -> None:
    ws = _BoundedIterWorkspace()
    ws.write("file1.txt", "1")
    ws.write("file2.txt", "2")

    snap = take_workspace_snapshot(ws, max_files=5)
    assert ws.bounded_calls == [5]
    assert "file1.txt" in snap


def test_probe_ignores_engine_owned_root_files_rewritten_during_an_attempt() -> None:
    workspace = MemoryWorkspace()
    workspace.write("src/app.py", "print('hello')\n")
    engine_owned = (
        "checkpoint.json",
        ".agents/mcp_config.json",
        ".cursor/mcp.json",
        ".kimi-code/mcp.json",
    )
    for path in engine_owned:
        workspace.write(path, "{}")

    probe = DefaultWorkspaceChangeProbe()
    probe.observe_before("development_analysis", workspace)
    for path in engine_owned:
        workspace.write(path, '{"checkpoint_saved_count": 2}')

    assert probe.has_changed("development_analysis", workspace) is False
    assert not set(engine_owned) & set(take_workspace_snapshot(workspace))


def _fs_workspace(root: Path) -> FsWorkspace:
    (root / "src").mkdir()
    (root / "README.md").write_text("readme\n", encoding="utf-8")
    (root / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")
    return FsWorkspace(root)


def test_probe_reports_no_change_when_the_awareness_registry_comes_and_goes(
    tmp_path: Path,
) -> None:
    workspace = _fs_workspace(tmp_path)
    probe = DefaultWorkspaceChangeProbe()
    probe.observe_before("development_analysis", workspace)
    try:
        awareness_for_workspace(tmp_path).set_watch_active()
        probe.observe_before("development_analysis", workspace)
    finally:
        release_workspace_awareness(tmp_path)

    assert probe.has_changed("development_analysis", workspace) is False


def test_probe_reports_no_change_when_the_awareness_queue_is_drained(
    tmp_path: Path,
) -> None:
    workspace = _fs_workspace(tmp_path)
    awareness = awareness_for_workspace(tmp_path)
    try:
        awareness.record(str(tmp_path / "README.md"))
        probe = DefaultWorkspaceChangeProbe()
        probe.observe_before("development_analysis", workspace)
        awareness.drain()
        scratch = tmp_path / "src" / "scratch.tmp"
        scratch.write_text("x", encoding="utf-8")
        awareness.record(str(scratch))
        scratch.unlink()

        assert probe.has_changed("development_analysis", workspace) is False
    finally:
        release_workspace_awareness(tmp_path)


def test_probe_detects_an_edit_the_awareness_recorded_beyond_the_walk_limit(
    tmp_path: Path,
) -> None:
    workspace = _fs_workspace(tmp_path)
    awareness = awareness_for_workspace(tmp_path)
    try:
        probe = DefaultWorkspaceChangeProbe(max_files=1)
        probe.observe_before("development", workspace)
        edited = tmp_path / "src" / "app.py"
        edited.write_text("print('changed by the agent')\n", encoding="utf-8")
        awareness.record(str(edited))

        assert probe.has_changed("development", workspace) is True
    finally:
        release_workspace_awareness(tmp_path)
