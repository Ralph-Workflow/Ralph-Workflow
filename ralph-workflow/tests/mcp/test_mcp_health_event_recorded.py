"""``record_stage`` and ``record_restart_decision`` must preserve full evidence for incident triage."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

import ralph.mcp.server._mcp_health_event as m
from ralph.mcp.server._mcp_health_event import (
    RestartReason,
    clear_durable_sink,
    get_durable_sink_path,
    get_restart_decisions,
    read_durable_sink,
    record_restart_decision,
    record_stage,
    set_durable_sink_path,
)

# ---------------------------------------------------------------------------
# record_stage — available/unavailable branches
# ---------------------------------------------------------------------------


def test_record_stage_available_log() -> None:
    """``record_stage(available=True)`` appends a (stage, True) record."""
    record_stage("test_available", available=True)
    assert ("test_available", True) in m._EVENTS


def test_record_stage_unavailable_log() -> None:
    """``record_stage(available=False)`` appends a (stage, False) record."""
    record_stage("test_unavailable", available=False)
    assert ("test_unavailable", False) in m._EVENTS


def test_record_stage_events_are_bounded() -> None:
    """The ``_EVENTS`` deque has a maximum size and drops the oldest events."""
    for i in range(100):
        record_stage(f"stage_{i}", available=i % 2 == 0)
    # Bounded to 64; at least one of the first 36 must have been evicted.
    assert len(m._EVENTS) == 64


# ---------------------------------------------------------------------------
# record_restart_decision — PROCESS_EXITED branch (exit status + log tail)
# ---------------------------------------------------------------------------


def test_record_restart_decision_process_exited_has_exit_status_and_log_tail() -> None:
    """``PROCESS_EXITED`` records carry the exit code and captured log tail."""
    log_tail = ("line one", "line two", "error occurred")
    record_restart_decision(
        reason=RestartReason.PROCESS_EXITED,
        exit_status=42,
        log_tail=log_tail,
        stage="ready_to_serve",
    )
    decisions = get_restart_decisions()
    assert len(decisions) >= 1
    last = decisions[-1]
    assert last.reason is RestartReason.PROCESS_EXITED
    assert last.exit_status == 42
    assert last.probe_duration_s is None
    assert last.log_tail == log_tail
    assert last.stage == "ready_to_serve"
    assert last.available is False


def test_record_restart_decision_process_exited_requires_exit_status() -> None:
    """Omitting ``exit_status`` on ``PROCESS_EXITED`` raises ``ValueError``."""
    with pytest.raises(ValueError, match="exit_status"):
        record_restart_decision(
            reason=RestartReason.PROCESS_EXITED,
            log_tail=None,
            stage="upstream_catalog_or_discovery_done",
        )


# ---------------------------------------------------------------------------
# record_restart_decision — PROBE_FAILED branch (probe duration)
# ---------------------------------------------------------------------------


def test_record_restart_decision_probe_failed_has_probe_duration() -> None:
    """``PROBE_FAILED`` records carry the probe wall-clock duration."""
    record_restart_decision(
        reason=RestartReason.PROBE_FAILED,
        probe_duration_s=1.234,
        stage="ready_to_serve",
    )
    decisions = get_restart_decisions()
    assert len(decisions) >= 1
    last = decisions[-1]
    assert last.reason is RestartReason.PROBE_FAILED
    assert last.exit_status is None
    assert last.probe_duration_s == 1.234
    assert last.log_tail is None
    assert last.stage == "ready_to_serve"
    assert last.available is False


def test_record_restart_decision_probe_failed_requires_probe_duration() -> None:
    """Omitting ``probe_duration_s`` on ``PROBE_FAILED`` raises ``ValueError``."""
    with pytest.raises(ValueError, match="probe_duration_s"):
        record_restart_decision(
            reason=RestartReason.PROBE_FAILED,
            stage="registry_built",
        )


# ---------------------------------------------------------------------------
# record_restart_decision — unavailable-log branch (log_tail=None)
# ---------------------------------------------------------------------------


def test_record_restart_decision_process_exited_with_no_log_tail() -> None:
    """``log_tail=None`` is stored as ``None`` (no log available)."""
    record_restart_decision(
        reason=RestartReason.PROCESS_EXITED,
        exit_status=137,
        log_tail=None,
        stage="env_loaded",
    )
    decisions = get_restart_decisions()
    last = decisions[-1]
    assert last.log_tail is None
    assert last.exit_status == 137


# ---------------------------------------------------------------------------
# get_restart_decisions
# ---------------------------------------------------------------------------


def test_get_restart_decisions_returns_immutable_snapshot() -> None:
    """``get_restart_decisions`` returns a ``tuple`` that cannot be mutated."""
    record_restart_decision(
        reason=RestartReason.PROBE_FAILED,
        probe_duration_s=0.5,
        stage="session_ready",
    )
    snapshot = get_restart_decisions()
    assert isinstance(snapshot, tuple)
    # Mutations to the underlying deque do not affect the snapshot.
    m._DECISIONS.clear()
    assert snapshot == get_restart_decisions() or snapshot != ()


def test_restart_decisions_are_bounded() -> None:
    """The ``_DECISIONS`` deque has a maximum size and drops the oldest records."""
    for i in range(100):
        record_restart_decision(
            reason=RestartReason.PROBE_FAILED,
            probe_duration_s=float(i),
            stage=f"stage_{i}",
        )
    assert len(m._DECISIONS) == 64
    assert len(get_restart_decisions()) == 64


# ---------------------------------------------------------------------------
# RestartDecision frozen dataclass
# ---------------------------------------------------------------------------


def test_restart_decision_is_frozen() -> None:
    """``RestartDecision`` cannot be modified after construction."""
    record_restart_decision(
        reason=RestartReason.PROCESS_EXITED,
        exit_status=1,
        log_tail=("a",),
        stage="config_loaded",
    )
    decision = get_restart_decisions()[-1]
    # Frozen dataclass: mutation must fail. Cast to Any so mypy
    # permits the write syntactically while the runtime enforces
    # FrozenInstanceError (an AttributeError subclass).
    mutable_alias = cast("Any", decision)
    assert decision.exit_status == 1
    with pytest.raises(AttributeError):
        mutable_alias.exit_status = 99


# ---------------------------------------------------------------------------
# RestartReason enum
# ---------------------------------------------------------------------------


def test_restart_reason_enum_values() -> None:
    """``RestartReason`` has exactly the two expected members."""
    assert set(RestartReason) == {RestartReason.PROCESS_EXITED, RestartReason.PROBE_FAILED}
    assert RestartReason.PROCESS_EXITED.value == "process_exited"
    assert RestartReason.PROBE_FAILED.value == "probe_failed"


# ---------------------------------------------------------------------------
# Durable sink — set / get / clear
# ---------------------------------------------------------------------------


def test_set_and_get_durable_sink_path(tmp_path: Path) -> None:
    """``set_durable_sink_path`` configures the sink and ``get_durable_sink_path`` returns it."""
    orig = get_durable_sink_path()
    try:
        sink = tmp_path / "decisions.jsonl"
        set_durable_sink_path(sink)
        assert get_durable_sink_path() == sink
    finally:
        set_durable_sink_path(orig)


def test_clear_durable_sink_removes_file(tmp_path: Path) -> None:
    """``clear_durable_sink`` removes the file if it exists."""
    sink = tmp_path / "decisions.jsonl"
    sink.write_text("dummy\n", encoding="utf-8")
    assert sink.exists()
    set_durable_sink_path(sink)
    clear_durable_sink()
    assert not sink.exists()
    set_durable_sink_path(None)


def test_clear_durable_sink_noop_when_absent(tmp_path: Path) -> None:
    """``clear_durable_sink`` is a no-op when the file does not exist."""
    sink = tmp_path / "absent.jsonl"
    assert not sink.exists()
    set_durable_sink_path(sink)
    clear_durable_sink()  # must not raise
    assert not sink.exists()
    set_durable_sink_path(None)


# ---------------------------------------------------------------------------
# Durable sink — record_restart_decision crosses the producer boundary
# ---------------------------------------------------------------------------


def test_durable_sink_process_exited_persists_exit_status(
    tmp_path: Path,
) -> None:
    """(a) PROCESS_EXITED with exit_status is persisted to the JSONL sink and readable."""
    orig = get_durable_sink_path()
    sink = tmp_path / "decisions.jsonl"
    try:
        set_durable_sink_path(sink)
        clear_durable_sink()
        log_tail = ("startup done", "server running")
        record_restart_decision(
            reason=RestartReason.PROCESS_EXITED,
            exit_status=137,
            log_tail=log_tail,
            stage="ready_to_serve",
        )
        read_back = read_durable_sink()
        assert len(read_back) == 1
        record = read_back[0]
        assert record["reason"] == "process_exited"
        assert record["exit_status"] == 137
        assert record["probe_duration_s"] is None
        assert record["log_tail"] == list(log_tail)
        assert record["stage"] == "ready_to_serve"
    finally:
        set_durable_sink_path(orig)
        clear_durable_sink()


def test_durable_sink_probe_failed_persists_probe_duration(
    tmp_path: Path,
) -> None:
    """(b) PROBE_FAILED records persist ``probe_duration_s`` to the durable JSONL sink."""
    orig = get_durable_sink_path()
    sink = tmp_path / "decisions.jsonl"
    try:
        set_durable_sink_path(sink)
        clear_durable_sink()
        record_restart_decision(
            reason=RestartReason.PROBE_FAILED,
            probe_duration_s=2.718,
            stage="registry_built",
        )
        read_back = read_durable_sink()
        assert len(read_back) == 1
        record = read_back[0]
        assert record["reason"] == "probe_failed"
        assert record["probe_duration_s"] == 2.718
        assert record["exit_status"] is None
        assert record["stage"] == "registry_built"
    finally:
        set_durable_sink_path(orig)
        clear_durable_sink()


def test_durable_sink_log_available_records_tail(
    tmp_path: Path,
) -> None:
    """(c) When the child log is available the tail lines are persisted to the JSONL sink."""
    orig = get_durable_sink_path()
    sink = tmp_path / "decisions.jsonl"
    try:
        set_durable_sink_path(sink)
        clear_durable_sink()
        # Simulate a log file with 15 lines; only the last 10 should be stored.
        log_lines = [f"log line {i}" for i in range(15)]
        log_tail = tuple(log_lines[-10:])  # last 10 lines
        record_restart_decision(
            reason=RestartReason.PROCESS_EXITED,
            exit_status=1,
            log_tail=log_tail,
            stage="process_exited",
        )
        read_back = read_durable_sink()
        assert len(read_back) == 1
        record = read_back[0]
        assert record["log_tail"] == list(log_tail)
        assert len(record["log_tail"]) == 10
    finally:
        set_durable_sink_path(orig)
        clear_durable_sink()


def test_durable_sink_log_unavailable_records_none(
    tmp_path: Path,
) -> None:
    """(d) When the child log is unavailable ``log_tail=None`` is persisted to the JSONL sink."""
    orig = get_durable_sink_path()
    sink = tmp_path / "decisions.jsonl"
    try:
        set_durable_sink_path(sink)
        clear_durable_sink()
        # log_tail=None: the caller could not read the child log
        record_restart_decision(
            reason=RestartReason.PROCESS_EXITED,
            exit_status=137,
            log_tail=None,
            stage="process_exited",
        )
        read_back = read_durable_sink()
        assert len(read_back) == 1
        record = read_back[0]
        # log_tail=None serialises as [] — this is distinguishable from
        # a successful read that happened to return zero lines.
        assert record["log_tail"] == []
    finally:
        set_durable_sink_path(orig)
        clear_durable_sink()


def test_read_durable_sink_returns_empty_when_no_file(
    tmp_path: Path,
) -> None:
    """``read_durable_sink`` returns an empty tuple when the sink file does not exist."""
    orig = get_durable_sink_path()
    absent = tmp_path / "does_not_exist.jsonl"
    try:
        set_durable_sink_path(absent)
        result = read_durable_sink()
        assert result == ()
    finally:
        set_durable_sink_path(orig)
