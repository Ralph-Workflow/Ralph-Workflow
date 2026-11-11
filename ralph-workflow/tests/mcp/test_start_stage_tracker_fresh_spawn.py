from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from ralph.mcp.server._start_stage_tracker import (
    RUNTIME_STAGES,
    read_last_stage,
    record,
    reset_spawn_id,
    stage_file_path,
)


def test_record_and_read_last_stage_round_trip(tmp_path: Path) -> None:
    stage = RUNTIME_STAGES[4]
    record(tmp_path, stage)
    assert read_last_stage(tmp_path) == stage


def test_record_overwrites_prior_stage_for_same_spawn(tmp_path: Path) -> None:
    record(tmp_path, RUNTIME_STAGES[0])
    record(tmp_path, RUNTIME_STAGES[3])
    record(tmp_path, RUNTIME_STAGES[-1])
    assert read_last_stage(tmp_path) == RUNTIME_STAGES[-1]


def test_read_last_stage_returns_none_when_no_file(tmp_path: Path) -> None:
    assert read_last_stage(tmp_path) is None


def test_stage_file_lives_below_agent_dir(tmp_path: Path) -> None:
    path = stage_file_path(tmp_path)
    assert ".agent" in path.parts


def test_fresh_spawn_isolation__predecessor_file_not_readable_by_new_spawn(
    tmp_path: Path,
) -> None:
    record(tmp_path, RUNTIME_STAGES[-1])
    assert read_last_stage(tmp_path) == RUNTIME_STAGES[-1]

    reset_spawn_id()

    assert read_last_stage(tmp_path) is None


def test_reset_spawn_id_produces_new_spawn_unreadable_by_old_spawn(
    tmp_path: Path,
) -> None:
    record(tmp_path, RUNTIME_STAGES[3])
    assert read_last_stage(tmp_path) == RUNTIME_STAGES[3]

    old_path = stage_file_path(tmp_path)
    assert old_path.exists()

    reset_spawn_id()

    new_path = stage_file_path(tmp_path)
    assert new_path != old_path

    assert read_last_stage(tmp_path) is None

    record(tmp_path, RUNTIME_STAGES[-1])
    assert read_last_stage(tmp_path) == RUNTIME_STAGES[-1]


def test_consecutive_reset_spawn_id_produces_unique_ids(tmp_path: Path) -> None:
    ids = set()
    for _ in range(5):
        reset_spawn_id()
        ids.add(stage_file_path(tmp_path).name)
    assert len(ids) == 5


def test_record_creates_parent_directories(tmp_path: Path) -> None:
    agent_dir = tmp_path / ".agent"
    assert not agent_dir.exists()
    record(tmp_path, RUNTIME_STAGES[0])
    assert agent_dir.is_dir()


def test_record_is_flushed_before_return(tmp_path: Path) -> None:
    stage = RUNTIME_STAGES[5]
    record(tmp_path, stage)

    path = stage_file_path(tmp_path)
    fd = os.open(path, os.O_RDONLY)
    try:
        data = os.read(fd, 1024)
    finally:
        os.close(fd)

    assert data == stage.encode("ascii")


def test_stage_file_path_differs_across_spawn_ids(tmp_path: Path) -> None:
    path_a = stage_file_path(tmp_path)
    reset_spawn_id()
    path_b = stage_file_path(tmp_path)
    assert path_a != path_b


def test_record_replaces_file_atomically_identity_preserved(tmp_path: Path) -> None:
    record(tmp_path, RUNTIME_STAGES[0])
    path = stage_file_path(tmp_path)
    siblings = [p for p in path.parent.iterdir() if p.name.endswith(".tmp")]
    assert siblings == []
    record(tmp_path, RUNTIME_STAGES[3])
    assert path.read_text(encoding="ascii") == RUNTIME_STAGES[3]
    assert path.stat().st_size == len(RUNTIME_STAGES[3])


def test_record_mid_write_failure_leaves_previous_stage_intact(
    tmp_path: Path,
) -> None:
    record(tmp_path, RUNTIME_STAGES[1])
    path = stage_file_path(tmp_path)
    with patch("ralph.mcp.server._start_stage_tracker.os.replace") as replace_mock:
        replace_mock.side_effect = OSError("simulated mid-write failure")
        try:
            record(tmp_path, RUNTIME_STAGES[5])
        except OSError as exc:
            assert "simulated mid-write failure" in str(exc)
        else:
            raise AssertionError("record must propagate the write failure")
    assert read_last_stage(tmp_path) == RUNTIME_STAGES[1]
    siblings = [p for p in path.parent.iterdir() if p.name.endswith(".tmp")]
    assert siblings == []
