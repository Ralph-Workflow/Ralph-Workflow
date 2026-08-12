"""wt-04-claude-parsing regression: transcript discovery under a live sibling session.

The orchestrating Claude Code session and any ``claude`` interactive
child process it spawns in the *same workspace* write to the exact
same ``~/.claude/projects/<project-key>`` directory. Before this fix,
``PtyLineReader`` had no way to tell "a file that already existed and
is still being appended to by an unrelated session" apart from "the
freshly-spawned child's own transcript file" -- both satisfy
``find_latest_claude_transcript_entry``'s ``min_mtime`` floor equally
well, and "latest mtime wins" could lock onto the wrong one for the
entire run. ``PtyLineReader.__init__`` now snapshots the transcript
file names already on disk for the workspace BEFORE the child can
write anything, so that snapshot can be excluded from the discovery
fallback (see ``ralph.agents.invoke._pty_transcript.existing_transcript_names``).
"""

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


def test_pty_line_reader_snapshots_pre_existing_transcript_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The constructor records the pre-existing ``*.jsonl`` names, publicly."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    project_dir_name = str(workspace_root).replace("/", "-")
    project_root = tmp_path / ".claude" / "projects" / project_dir_name
    project_root.mkdir(parents=True)
    (project_root / "orchestrator-session.jsonl").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        "ralph.agents.invoke._pty_transcript.Path.home", lambda: tmp_path
    )

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
        assert reader._pre_existing_transcript_names == frozenset(
            {"orchestrator-session.jsonl"}
        )
    finally:
        os.close(master_fd)


def test_pty_line_reader_snapshot_is_empty_with_no_workspace_path() -> None:
    """No ``workspace_path`` on the ctx means no snapshot is possible or needed."""
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
    """Full discovery flow: snapshot at construction, then discovery excludes it.

    This replays the exact race that broke the live
    ``smoke-interactive-claude`` run: the reader is constructed while an
    unrelated, already-existing sibling session lives in the same
    project directory; the child's transcript file appears and grows
    after construction; the sibling ALSO keeps growing (advancing its
    mtime past the child's). Discovery MUST still resolve to the
    child's file, not the sibling's.
    """
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    project_dir_name = str(workspace_root).replace("/", "-")
    project_root = tmp_path / ".claude" / "projects" / project_dir_name
    project_root.mkdir(parents=True)
    sibling = project_root / "sibling-session.jsonl"
    sibling.write_text("{}\n", encoding="utf-8")
    os.utime(sibling, (10.0, 10.0))
    monkeypatch.setattr(
        "ralph.agents.invoke._pty_transcript.Path.home", lambda: tmp_path
    )

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

        # The child's own transcript file appears after construction.
        child = project_root / "child-session.jsonl"
        child.write_text("{}\n", encoding="utf-8")
        os.utime(child, (20.0, 20.0))
        # The sibling keeps being active too -- its mtime now exceeds
        # the child's, which is exactly what broke the old heuristic.
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
    """``_extras.pre_existing_transcript_names`` wins over a live re-snapshot.

    A snapshot taken inside ``PtyLineReader.__init__`` runs strictly
    AFTER the caller (``run_pty_and_read_lines``) has already spawned
    the child process, so it could already see -- and wrongly exclude
    -- the child's own freshly-created transcript file. The caller's
    pre-spawn snapshot must always win when supplied.
    """
    import ralph.agents.invoke._pty_extras as pty_extras_module

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    project_dir_name = str(workspace_root).replace("/", "-")
    project_root = tmp_path / ".claude" / "projects" / project_dir_name
    project_root.mkdir(parents=True)
    # A file that exists by the time PtyLineReader.__init__ runs (as if
    # the child had already been spawned and written its first line)
    # but was NOT part of the caller's pre-spawn snapshot.
    (project_root / "child-session.jsonl").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        "ralph.agents.invoke._pty_transcript.Path.home", lambda: tmp_path
    )

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
        # The caller's snapshot (taken before the child existed) is
        # honored verbatim -- it must NOT be overwritten by a live
        # re-snapshot that would now see "child-session.jsonl" and
        # wrongly treat it as pre-existing.
        assert reader._pre_existing_transcript_names == frozenset()
    finally:
        os.close(master_fd)


def test_run_pty_and_read_lines_snapshots_before_spawning_the_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``run_pty_and_read_lines`` takes the snapshot BEFORE ``spawn_pty``.

    This is the actual regression: taking the snapshot any later (e.g.
    inside ``PtyLineReader.__init__``, which only runs after
    ``spawn_pty`` returns) can already see the child's own freshly
    created transcript file and wrongly self-exclude it.
    """
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

    # ``run_pty_and_read_lines`` reads ``extra_env``/``clock`` before
    # ``spawn_pty`` and reads several more ``ctx`` fields only after (in
    # code this test never reaches, since ``_StopEarly`` fires inside
    # ``spawn_pty``) -- a bare SimpleNamespace with just the fields
    # touched before the stop point is the minimal, faithful ctx.
    ctx = SimpleNamespace(
        extra_env=None,
        clock=None,
        workspace_path=workspace_root,
        config=_make_pty_ctx().config,
    )
    with pytest.raises(_StopEarlyError):
        next(iter(pty_runner_module.run_pty_and_read_lines(["claude"], ctx)))

    assert calls == ["snapshot", "spawn"]
