"""Tests for kind-specific waiting-status rendering in PipelineSubscriber."""

from __future__ import annotations

import queue
from typing import TYPE_CHECKING

from ralph.agents.idle_watchdog import WaitingStatusEvent, WaitingStatusKind
from ralph.display.subscriber import PipelineSubscriber

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.display.snapshot import PipelineSnapshot


def _make_subscriber(tmp_path: Path) -> PipelineSubscriber:
    q: queue.Queue[PipelineSnapshot] = queue.Queue(maxsize=64)
    return PipelineSubscriber(queue=q, workspace_root=tmp_path, run_id="test-run")


def _last_line(sub: PipelineSubscriber) -> str | None:
    return getattr(sub, "_last_activity_line", None)


def _event(  # noqa: PLR0913
    kind: WaitingStatusKind,
    *,
    cumulative: float = 120.0,
    run: float = 60.0,
    ceiling: float = 1800.0,
    suspect: float | None = 600.0,
    diagnostic: dict[str, str | int | float | bool] | None = None,
) -> WaitingStatusEvent:
    return WaitingStatusEvent(
        kind=kind,
        cumulative_seconds=cumulative,
        current_run_seconds=run,
        idle_elapsed_seconds=15.0,
        ceiling_seconds=ceiling,
        suspect_threshold_seconds=suspect,
        diagnostic=diagnostic or {},
    )


def test_record_waiting_status_kind_specific_lines(tmp_path: Path) -> None:
    """Kind-specific lines are emitted for each WaitingStatusKind."""
    sub = _make_subscriber(tmp_path)

    # ENTERED
    sub.record_waiting_status(_event(WaitingStatusKind.ENTERED))
    line = _last_line(sub)
    assert line is not None
    assert "started waiting" in line
    assert "cumulative=" in line
    assert "ceiling=" in line

    # PROGRESS without workspace delta
    sub.record_waiting_status(_event(WaitingStatusKind.PROGRESS))
    line = _last_line(sub)
    assert line is not None
    assert "still active" in line
    assert "run=" in line
    assert "cumulative=" in line

    # PROGRESS with workspace delta
    sub.record_waiting_status(
        _event(WaitingStatusKind.PROGRESS, diagnostic={"workspace_event_delta": 3})
    )
    line = _last_line(sub)
    assert line is not None
    assert "still active" in line
    assert "workspace_events_since_wait=3" in line

    # SUSPECTED_FROZEN
    sub.record_waiting_status(
        _event(
            WaitingStatusKind.SUSPECTED_FROZEN,
            diagnostic={"evidence": "time_and_workspace_quiet"},
        )
    )
    line = _last_line(sub)
    assert line is not None
    assert "may be frozen" in line
    assert "time_and_workspace_quiet" in line

    # EXITED
    sub.record_waiting_status(_event(WaitingStatusKind.EXITED))
    line = _last_line(sub)
    assert line is not None
    assert "resumed activity" in line
    assert "run=" in line

    # HARD_STOP with scoped_child_active and oldest_child_seconds
    sub.record_waiting_status(
        _event(
            WaitingStatusKind.HARD_STOP,
            diagnostic={
                "scoped_child_active": True,
                "oldest_child_seconds": 720.0,
                "cumulative": 1800.0,
            },
        )
    )
    line = _last_line(sub)
    assert line is not None
    assert "hit hard ceiling" in line
    assert "scoped_child_active=True" in line
    assert "oldest_child_seconds=" in line



def test_decision_log_only_for_suspected_frozen_and_hard_stop(tmp_path: Path) -> None:
    """Decision log entries are appended only for SUSPECTED_FROZEN and HARD_STOP."""
    sub = _make_subscriber(tmp_path)
    initial_len = len(sub.decision_log)

    # These should NOT add to decision log
    for kind in (WaitingStatusKind.ENTERED, WaitingStatusKind.PROGRESS, WaitingStatusKind.EXITED):
        sub.record_waiting_status(_event(kind))
    assert len(sub.decision_log) == initial_len

    # SUSPECTED_FROZEN SHOULD add to decision log
    sub.record_waiting_status(_event(WaitingStatusKind.SUSPECTED_FROZEN))
    assert len(sub.decision_log) == initial_len + 1

    # HARD_STOP SHOULD add to decision log
    sub.record_waiting_status(_event(WaitingStatusKind.HARD_STOP))
    assert len(sub.decision_log) == initial_len + 2


def test_hard_stop_without_oldest_child_seconds(tmp_path: Path) -> None:
    """HARD_STOP line renders without oldest_child_seconds when not in diagnostic."""
    sub = _make_subscriber(tmp_path)
    sub.record_waiting_status(
        _event(WaitingStatusKind.HARD_STOP, diagnostic={"scoped_child_active": False})
    )
    line = _last_line(sub)
    assert line is not None
    assert "hit hard ceiling" in line
    assert "scoped_child_active=False" in line
    assert "oldest_child_seconds" not in line


def test_non_waiting_status_event_is_ignored(tmp_path: Path) -> None:
    """Non-WaitingStatusEvent objects passed to record_waiting_status are silently ignored."""
    sub = _make_subscriber(tmp_path)
    initial_line = _last_line(sub)
    sub.record_waiting_status("not an event")
    sub.record_waiting_status(None)
    sub.record_waiting_status(42)
    assert _last_line(sub) == initial_line
