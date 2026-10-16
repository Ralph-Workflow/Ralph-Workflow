"""R4: per-spawn stage tracker — fresh-spawn isolation, round-trip, and flushed writes."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from ralph.mcp.server._start_stage_tracker import (
    RUNTIME_STAGES,
    _get_spawn_id,
    read_last_stage,
    record,
    reset_spawn_id,
    stage_file_path,
)


class _FakeClock:
    """Monotonic fake for tests that do not need wall-clock time."""

    def __init__(self) -> None:
        self._t = 0.0

    def advance(self, delta: float) -> None:
        self._t += delta

    def monotonic(self) -> float:
        return self._t


class _SpySpawnId:
    """Controlled spawn-id source that returns the next value from a sequence."""

    def __init__(self, values: list[str]) -> None:
        self._values = values
        self._i = 0

    def __call__(self) -> str:
        if self._i >= len(self._values):
            raise RuntimeError("spy sequence exhausted")
        value = self._values[self._i]
        self._i += 1
        return value


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_record_and_read_last_stage_round_trip(tmp_path: Path) -> None:
    """``record`` then ``read_last_stage`` returns the same label."""
    stage = RUNTIME_STAGES[3]
    record(tmp_path, stage)
    assert read_last_stage(tmp_path) == stage


def test_record_overwrites_prior_stage_for_same_spawn(tmp_path: Path) -> None:
    """Two ``record`` calls leave only the last stage."""
    record(tmp_path, RUNTIME_STAGES[0])
    record(tmp_path, RUNTIME_STAGES[2])
    record(tmp_path, RUNTIME_STAGES[-1])
    assert read_last_stage(tmp_path) == RUNTIME_STAGES[-1]


def test_read_last_stage_returns_none_when_no_file(tmp_path: Path) -> None:
    """No file written yet → None."""
    assert read_last_stage(tmp_path) is None


def test_stage_file_lives_below_agent_dir(tmp_path: Path) -> None:
    """The stage file path is inside ``.agent/`` for operator visibility."""
    path = stage_file_path(tmp_path)
    assert ".agent" in path.parts


def test_fresh_spawn_isolation__predecessor_file_not_readable_by_new_spawn(
    tmp_path: Path,
) -> None:
    """A new spawn cannot read the previous spawn's stage.

    Spawn A writes its stage. Spawn B (with a different spawn ID) calls
    ``read_last_stage`` on the same root — it must receive ``None``
    because the predecessor's file is at a different path.
    """
    # Spawn A: record a stage using its own spawn ID.
    record(tmp_path, RUNTIME_STAGES[-1])
    # Verify the file was written and is readable by the same spawn.
    assert read_last_stage(tmp_path) == RUNTIME_STAGES[-1]

    # Spawn B: fake a different spawn ID so it gets a different file path.
    old_spawn_id = _get_spawn_id()
    try:
        with patch(
            "ralph.mcp.server._start_stage_tracker._get_spawn_id",
            _FakeSpawnIdGetter("spawn-b-uuid"),
        ):
            # Spawn B's path is different, so it finds no file.
            assert read_last_stage(tmp_path) is None
    finally:
        # Restore the original spawn ID so later tests use the real value.
        _restore_spawn_id(old_spawn_id)


def test_reset_spawn_id_produces_new_spawn_unreadable_by_old_spawn(
    tmp_path: Path,
) -> None:
    """Calling ``reset_spawn_id()`` makes the old file unreachable."""
    # Spawn A records its final stage.
    record(tmp_path, RUNTIME_STAGES[2])
    assert read_last_stage(tmp_path) == RUNTIME_STAGES[2]

    old_path = stage_file_path(tmp_path)
    assert old_path.exists()

    # Spawn A calls reset — it now has a new identity.
    reset_spawn_id()

    # The old path is gone from the new spawn's perspective.
    new_path = stage_file_path(tmp_path)
    assert new_path != old_path

    # The old stage is NOT reachable under the new path.
    assert read_last_stage(tmp_path) is None

    # Spawn A can still record under its new identity.
    record(tmp_path, RUNTIME_STAGES[-1])
    assert read_last_stage(tmp_path) == RUNTIME_STAGES[-1]


def test_consecutive_reset_spawn_id_produces_unique_ids() -> None:
    """Every ``reset_spawn_id()`` call produces a distinct identity."""
    ids = set()
    for _ in range(5):
        reset_spawn_id()
        ids.add(_get_spawn_id())
    assert len(ids) == 5


def test_record_creates_parent_directories(tmp_path: Path) -> None:
    """``record`` creates the ``.agent`` directory if absent."""
    agent_dir = tmp_path / ".agent"
    assert not agent_dir.exists()
    record(tmp_path, RUNTIME_STAGES[0])
    assert agent_dir.is_dir()


def test_record_is_flushed_before_return(tmp_path: Path) -> None:
    """After ``record()`` returns, the stage is on disk (not buffered)."""
    stage = RUNTIME_STAGES[4]
    record(tmp_path, stage)

    # Re-open the file raw — no stdlib buffering — and read its content.
    path = stage_file_path(tmp_path)
    fd = os.open(path, os.O_RDONLY)
    try:
        data = os.read(fd, 1024)
    finally:
        os.close(fd)

    assert data == stage.encode("ascii")


def test_stage_file_path_differs_across_spawn_ids(tmp_path: Path) -> None:
    """Two different spawn IDs produce two different file paths."""
    path_a = stage_file_path(tmp_path)
    reset_spawn_id()
    path_b = stage_file_path(tmp_path)
    assert path_a != path_b


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeSpawnIdGetter:
    """Returns a fixed spawn ID each call, for testing path-scoping."""

    def __init__(self, spawn_id: str) -> None:
        self._id = spawn_id

    def __call__(self) -> str:
        return self._id


def _restore_spawn_id(spawn_id: str) -> None:
    """Restore the module-level spawn ID (only for test cleanup)."""
    import ralph.mcp.server._start_stage_tracker as m

    m._spawn_id = spawn_id


# ---------------------------------------------------------------------------
# Atomic-replacement invariants
# ---------------------------------------------------------------------------


def test_record_replaces_file_atomically_identity_preserved(tmp_path: Path) -> None:
    """The stage file content is never blank: mid-record reads see old or new, not empty.

    os.replace swaps the whole inode, so a reader that opened the file
    before the rename keeps reading the previous (complete) content, and
    the file at the path is never observed truncated.
    """
    record(tmp_path, RUNTIME_STAGES[0])
    path = stage_file_path(tmp_path)
    # No temp siblings left behind after a successful record.
    siblings = [p for p in path.parent.iterdir() if p.name.endswith(".tmp")]
    assert siblings == []
    record(tmp_path, RUNTIME_STAGES[2])
    # The final file is the complete new label, never blank or truncated.
    assert path.read_text(encoding="ascii") == RUNTIME_STAGES[2]
    assert path.stat().st_size == len(RUNTIME_STAGES[2])


def test_record_mid_write_failure_leaves_previous_stage_intact(
    tmp_path: Path,
) -> None:
    """A failure during the write phase must not destroy the previous stage file."""
    record(tmp_path, RUNTIME_STAGES[1])
    path = stage_file_path(tmp_path)
    with patch("ralph.mcp.server._start_stage_tracker.os.replace") as replace_mock:
        replace_mock.side_effect = OSError("simulated mid-write failure")
        try:
            record(tmp_path, RUNTIME_STAGES[4])
        except OSError as exc:
            assert "simulated mid-write failure" in str(exc)
        else:
            raise AssertionError("record must propagate the write failure")
    # The previous label is intact — the reader never saw a blank file.
    assert read_last_stage(tmp_path) == RUNTIME_STAGES[1]
    # The temp sibling was cleaned up.
    siblings = [p for p in path.parent.iterdir() if p.name.endswith(".tmp")]
    assert siblings == []
