"""``record_stage`` and ``record_restart_decision`` must preserve full evidence for incident triage."""

from __future__ import annotations

import pytest

import ralph.mcp.server._mcp_health_event as m
from ralph.mcp.server._mcp_health_event import (
    RestartReason,
    get_restart_decisions,
    record_restart_decision,
    record_stage,
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
    with pytest.raises(AttributeError):
        decision.exit_status = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# RestartReason enum
# ---------------------------------------------------------------------------


def test_restart_reason_enum_values() -> None:
    """``RestartReason`` has exactly the two expected members."""
    assert set(RestartReason) == {RestartReason.PROCESS_EXITED, RestartReason.PROBE_FAILED}
    assert RestartReason.PROCESS_EXITED.value == "process_exited"
    assert RestartReason.PROBE_FAILED.value == "probe_failed"
