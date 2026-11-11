from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from ralph.agents.invoke._pty_line_reader import PtyLineReader
from ralph.agents.invoke._pty_transcript import find_latest_claude_transcript_entry
from ralph.agents.timeout_clock import FakeClock
from tests.agents.invoke.test_line_reader_queue_bound import _FakePtyHandle, _make_pty_ctx

if TYPE_CHECKING:
    from pathlib import Path


def _project_dir_name(workspace_root: Path) -> str:
    return str(workspace_root.resolve()).replace("/", "-").replace(" ", "-")


def test_pty_line_reader_snapshots_pre_existing_transcript_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    project_dir_name = _project_dir_name(workspace_root)
    project_root = tmp_path / ".claude" / "projects" / project_dir_name
    project_root.mkdir(parents=True)
    (project_root / "orchestrator-session.jsonl").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr("ralph.agents.invoke._pty_transcript.Path.home", lambda: tmp_path)

    master_fd = os.open("/dev/null", os.O_RDONLY)
    try:
        handle = _FakePtyHandle(master_fd)
        reader = PtyLineReader(
            handle,
            "test-agent",
            _make_pty_ctx(workspace_path=workspace_root),
            FakeClock(start=0.0),
            extras=None,
        )
        assert reader._pre_existing_transcript_names == frozenset({"orchestrator-session.jsonl"})
    finally:
        os.close(master_fd)


def test_pty_line_reader_snapshot_is_empty_with_no_workspace_path() -> None:
    master_fd = os.open("/dev/null", os.O_RDONLY)
    try:
        handle = _FakePtyHandle(master_fd)
        reader = PtyLineReader(
            handle,
            "test-agent",
            _make_pty_ctx(),
            FakeClock(start=0.0),
            extras=None,
        )
        assert reader._pre_existing_transcript_names == frozenset()
    finally:
        os.close(master_fd)


def test_transcript_discovery_end_to_end_prefers_the_new_child_over_a_live_sibling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    project_dir_name = _project_dir_name(workspace_root)
    project_root = tmp_path / ".claude" / "projects" / project_dir_name
    project_root.mkdir(parents=True)
    sibling = project_root / "sibling-session.jsonl"
    sibling.write_text("{}\n", encoding="utf-8")
    os.utime(sibling, (10.0, 10.0))
    monkeypatch.setattr("ralph.agents.invoke._pty_transcript.Path.home", lambda: tmp_path)

    master_fd = os.open("/dev/null", os.O_RDONLY)
    try:
        handle = _FakePtyHandle(master_fd)
        reader = PtyLineReader(
            handle,
            "test-agent",
            _make_pty_ctx(workspace_path=workspace_root),
            FakeClock(start=0.0),
            extras=None,
        )

        child = project_root / "child-session.jsonl"
        child.write_text("{}\n", encoding="utf-8")
        os.utime(child, (20.0, 20.0))
        os.utime(sibling, (30.0, 30.0))

        entry = find_latest_claude_transcript_entry(
            workspace_root,
            min_mtime=15.0,
            exclude_names=reader._pre_existing_transcript_names,
        )
        assert entry == (child, "child-session")
    finally:
        os.close(master_fd)


def test_pty_line_reader_prefers_extras_snapshot_over_live_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ralph.agents.invoke._pty_extras as pty_extras_module

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    project_dir_name = _project_dir_name(workspace_root)
    project_root = tmp_path / ".claude" / "projects" / project_dir_name
    project_root.mkdir(parents=True)
    (project_root / "child-session.jsonl").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr("ralph.agents.invoke._pty_transcript.Path.home", lambda: tmp_path)

    master_fd = os.open("/dev/null", os.O_RDONLY)
    try:
        handle = _FakePtyHandle(master_fd)
        reader = PtyLineReader(
            handle,
            "test-agent",
            _make_pty_ctx(workspace_path=workspace_root),
            FakeClock(start=0.0),
            extras=pty_extras_module.PtyExtras(pre_existing_transcript_names=frozenset()),
        )
        assert reader._pre_existing_transcript_names == frozenset()
    finally:
        os.close(master_fd)


def test_run_pty_and_read_lines_snapshots_before_spawning_the_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    import ralph.agents.invoke._pty_runner as pty_runner_module

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    calls: list[str] = []

    def _fake_existing_transcript_names(_workspace_path: Path) -> frozenset[str]:
        calls.append("snapshot")
        return frozenset()

    class _StopEarlyError(Exception):
        pass

    class _FakeProcessManager:
        def spawn_pty(self, *_args: object, **_kwargs: object) -> object:
            calls.append("spawn")
            raise _StopEarlyError

    monkeypatch.setattr(
        pty_runner_module, "existing_transcript_names", _fake_existing_transcript_names
    )
    monkeypatch.setattr(pty_runner_module, "get_process_manager", _FakeProcessManager)

    ctx = SimpleNamespace(
        extra_env=None,
        clock=None,
        workspace_path=workspace_root,
        config=_make_pty_ctx().config,
    )
    with pytest.raises(_StopEarlyError):
        next(iter(pty_runner_module.run_pty_and_read_lines(["claude"], ctx)))

    assert calls == ["snapshot", "spawn"]
