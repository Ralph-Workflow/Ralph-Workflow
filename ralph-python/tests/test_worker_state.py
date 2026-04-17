"""Unit tests for WorkerState and WorkerStatus models."""

from __future__ import annotations

import pytest

from ralph.pipeline.worker_state import WorkerState, WorkerStatus


def test_worker_status_enum_values() -> None:
    """WorkerStatus has the required enum members."""
    assert WorkerStatus.PENDING.value == "PENDING"
    assert WorkerStatus.RUNNING.value == "RUNNING"
    assert WorkerStatus.SUCCEEDED.value == "SUCCEEDED"
    assert WorkerStatus.FAILED.value == "FAILED"
    assert WorkerStatus.CANCELLED.value == "CANCELLED"


def test_worker_state_defaults() -> None:
    """WorkerState has correct defaults for optional fields."""
    ws = WorkerState(unit_id="u1")

    assert ws.unit_id == "u1"
    assert ws.status == WorkerStatus.PENDING
    assert ws.started_at is None
    assert ws.finished_at is None
    assert ws.exit_code is None
    assert ws.error_message is None
    assert ws.commit_sha is None
    assert ws.worktree_path is None
    assert ws.log_file is None


def test_worker_state_frozen_immutable() -> None:
    """WorkerState raises on mutation attempt (frozen=True)."""
    ws = WorkerState(unit_id="u1")

    with pytest.raises(Exception):
        ws.status = WorkerStatus.RUNNING  # type: ignore[misc]


def test_worker_state_round_trip_json() -> None:
    """WorkerState round-trips through model_dump_json / model_validate_json."""
    from datetime import datetime, timezone

    ws = WorkerState(
        unit_id="unit-42",
        status=WorkerStatus.SUCCEEDED,
        started_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 1, 1, 12, 5, 0, tzinfo=timezone.utc),
        exit_code=0,
        error_message=None,
        commit_sha="abc123",
        worktree_path="/tmp/wt-unit-42",
        log_file="/tmp/logs/unit-42.log",
    )

    loaded = WorkerState.model_validate_json(ws.model_dump_json())

    assert loaded == ws


def test_worker_state_all_status_values_serialize() -> None:
    """All WorkerStatus values round-trip through JSON."""
    for status in WorkerStatus:
        ws = WorkerState(unit_id="u", status=status)
        loaded = WorkerState.model_validate_json(ws.model_dump_json())
        assert loaded.status == status


def test_worker_state_unit_id_required() -> None:
    """WorkerState requires unit_id."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        WorkerState()  # type: ignore[call-arg]


def test_worker_state_none_optionals_round_trip() -> None:
    """WorkerState with all None optionals round-trips cleanly."""
    ws = WorkerState(unit_id="u99")
    loaded = WorkerState.model_validate_json(ws.model_dump_json())
    assert loaded == ws
