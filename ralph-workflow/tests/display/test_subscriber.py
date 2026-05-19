"""Tests for kind-specific waiting-status rendering in PipelineSubscriber."""

from __future__ import annotations

import queue
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ralph.agents.idle_watchdog import WaitingStatusEvent, WaitingStatusKind
from ralph.display.subscriber import PipelineSubscriber
from ralph.pipeline.state import PipelineState

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.display.snapshot import PipelineSnapshot


@dataclass
class _EventOptions:
    kind: Any
    cumulative: float = 120.0
    run: float = 60.0
    ceiling: float = 1800.0
    suspect: Any = 600.0
    diagnostic: Any = None


def _make_subscriber(tmp_path: Path) -> PipelineSubscriber:
    q: queue.Queue[PipelineSnapshot] = queue.Queue(maxsize=64)
    return PipelineSubscriber(queue=q, workspace_root=tmp_path, run_id="test-run")


def _last_line(sub: PipelineSubscriber) -> str | None:
    return getattr(sub, "_waiting_status_line", None)


def _event(opts: _EventOptions) -> WaitingStatusEvent:
    return WaitingStatusEvent(
        kind=opts.kind,
        cumulative_seconds=opts.cumulative,
        current_run_seconds=opts.run,
        idle_elapsed_seconds=15.0,
        ceiling_seconds=opts.ceiling,
        suspect_threshold_seconds=opts.suspect,
        diagnostic=opts.diagnostic or {},
    )


def test_record_waiting_status_kind_specific_lines(tmp_path: Path) -> None:
    """Kind-specific lines are emitted for each WaitingStatusKind."""
    sub = _make_subscriber(tmp_path)

    # ENTERED
    sub.record_waiting_status(_event(_EventOptions(kind=WaitingStatusKind.ENTERED)))
    line = _last_line(sub)
    assert line is not None
    assert "started waiting" in line
    assert "cumulative=" in line
    assert "ceiling=" in line

    # PROGRESS without workspace delta
    sub.record_waiting_status(_event(_EventOptions(kind=WaitingStatusKind.PROGRESS)))
    line = _last_line(sub)
    assert line is not None
    assert "still active" in line
    assert "run=" in line
    assert "cumulative=" in line

    # PROGRESS with workspace delta
    sub.record_waiting_status(
        _event(
            _EventOptions(kind=WaitingStatusKind.PROGRESS, diagnostic={"workspace_event_delta": 3})
        )
    )
    line = _last_line(sub)
    assert line is not None
    assert "still active" in line
    assert "workspace_events_since_wait=3" in line

    # SUSPECTED_FROZEN
    sub.record_waiting_status(
        _event(
            _EventOptions(
                kind=WaitingStatusKind.SUSPECTED_FROZEN,
                diagnostic={"evidence": "time_and_workspace_quiet"},
            )
        )
    )
    line = _last_line(sub)
    assert line is not None
    assert "may be frozen" in line
    assert "time_and_workspace_quiet" in line

    # EXITED publishes a resumed-activity line, then clears the field.
    # Seed _last_state so the subscriber can build snapshots.

    state = PipelineState(
        phase="development",
        budget_caps={"iteration": 1, "reviewer_pass": 1},
    )
    sub.notify(state)
    while not sub.queue.empty():
        sub.queue.get_nowait()  # drain previous snapshots
    sub.record_waiting_status(_event(_EventOptions(kind=WaitingStatusKind.EXITED)))
    line = _last_line(sub)
    assert line is None  # field is cleared after one-shot publication
    # Queue must contain a snapshot with the resumed-activity line
    published = []
    while not sub.queue.empty():
        published.append(sub.queue.get_nowait())
    assert any(
        s.waiting_status_line is not None and "resumed activity" in s.waiting_status_line
        for s in published
    ), "EXITED must publish a snapshot with 'resumed activity' before clearing"

    # HARD_STOP with scoped_child_active and oldest_child_seconds
    sub.record_waiting_status(
        _event(
            _EventOptions(
                kind=WaitingStatusKind.HARD_STOP,
                diagnostic={
                    "scoped_child_active": True,
                    "oldest_child_seconds": 720.0,
                    "cumulative": 1800.0,
                },
            )
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
        sub.record_waiting_status(_event(_EventOptions(kind=kind)))
    assert len(sub.decision_log) == initial_len

    # SUSPECTED_FROZEN SHOULD add to decision log
    sub.record_waiting_status(_event(_EventOptions(kind=WaitingStatusKind.SUSPECTED_FROZEN)))
    assert len(sub.decision_log) == initial_len + 1

    # HARD_STOP SHOULD add to decision log
    sub.record_waiting_status(_event(_EventOptions(kind=WaitingStatusKind.HARD_STOP)))
    assert len(sub.decision_log) == initial_len + 2


def test_hard_stop_without_oldest_child_seconds(tmp_path: Path) -> None:
    """HARD_STOP line renders without oldest_child_seconds when not in diagnostic."""
    sub = _make_subscriber(tmp_path)
    sub.record_waiting_status(
        _event(
            _EventOptions(
                kind=WaitingStatusKind.HARD_STOP, diagnostic={"scoped_child_active": False}
            )
        )
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


def test_record_waiting_status_writes_to_waiting_field_not_activity_line(tmp_path: Path) -> None:
    """record_waiting_status writes to _waiting_status_line, not _last_activity_line."""
    sub = _make_subscriber(tmp_path)
    activity_before = getattr(sub, "_last_activity_line", None)
    sub.record_waiting_status(_event(_EventOptions(kind=WaitingStatusKind.PROGRESS)))
    assert getattr(sub, "_waiting_status_line", None) is not None
    assert "still active" in (getattr(sub, "_waiting_status_line", "") or "")
    assert getattr(sub, "_last_activity_line", None) == activity_before


def test_record_waiting_status_clears_field_on_exited(tmp_path: Path) -> None:
    """EXITED event surfaces a resumed-activity line then clears _waiting_status_line."""

    sub = _make_subscriber(tmp_path)
    # Seed state so the subscriber can build snapshots.
    state = PipelineState(
        phase="development",
        budget_caps={"iteration": 1, "reviewer_pass": 1},
    )
    sub.notify(state)
    sub.record_waiting_status(_event(_EventOptions(kind=WaitingStatusKind.ENTERED)))
    assert getattr(sub, "_waiting_status_line", None) is not None
    # Drain queue from ENTERED/notify snapshots so we can inspect only EXITED output.
    while not sub.queue.empty():
        sub.queue.get_nowait()
    sub.record_waiting_status(_event(_EventOptions(kind=WaitingStatusKind.EXITED)))
    # Field must be cleared after publication.
    assert getattr(sub, "_waiting_status_line", None) is None
    # At least one published snapshot must carry the resumed-activity line.
    published = []
    while not sub.queue.empty():
        published.append(sub.queue.get_nowait())
    assert any(
        s.waiting_status_line is not None and "resumed activity" in s.waiting_status_line
        for s in published
    ), "EXITED must publish a snapshot with 'resumed activity' before clearing"


def test_snapshot_includes_waiting_status_field(tmp_path: Path) -> None:
    """Snapshot built after a PROGRESS event has waiting_status_line populated."""

    sub = _make_subscriber(tmp_path)
    sub.record_waiting_status(_event(_EventOptions(kind=WaitingStatusKind.PROGRESS)))
    # Provide a minimal state so build_snapshot succeeds.
    state = PipelineState(
        phase="development",
        budget_caps={"iteration": 1, "reviewer_pass": 1},
    )
    snapshot = sub.build_snapshot(state)
    assert snapshot is not None
    assert snapshot.waiting_status_line is not None
    assert "still active" in snapshot.waiting_status_line


# ---------------------------------------------------------------------------
# Task 7: alive_by diagnostic rendering in subscriber
# ---------------------------------------------------------------------------


def test_progress_event_with_alive_by_includes_it_in_line(tmp_path: Path) -> None:
    """PROGRESS event with alive_by diagnostic shows it in the waiting status line."""
    sub = _make_subscriber(tmp_path)
    sub.record_waiting_status(
        _event(
            _EventOptions(
                kind=WaitingStatusKind.PROGRESS, diagnostic={"alive_by": "fresh_progress"}
            )
        )
    )
    line = _last_line(sub)
    assert line is not None
    assert "alive_by=fresh_progress" in line


def test_suspected_frozen_event_with_stale_label_includes_alive_by(tmp_path: Path) -> None:
    """SUSPECTED_FROZEN with alive_by=stale_label_only includes it in the status line."""
    sub = _make_subscriber(tmp_path)
    sub.record_waiting_status(
        _event(
            _EventOptions(
                kind=WaitingStatusKind.SUSPECTED_FROZEN,
                diagnostic={
                    "evidence": "time_only",
                    "alive_by": "stale_label_only",
                },
            )
        )
    )
    line = _last_line(sub)
    assert line is not None
    assert "alive_by=stale_label_only" in line


def test_waiting_status_line_property_returns_current_value(tmp_path: Path) -> None:
    """PipelineSubscriber.waiting_status_line property returns the internal status line."""
    sub = _make_subscriber(tmp_path)
    assert sub.waiting_status_line is None
    sub.record_waiting_status(_event(_EventOptions(kind=WaitingStatusKind.PROGRESS)))
    assert sub.waiting_status_line is not None
    assert "still active" in sub.waiting_status_line


def test_record_permission_prompt_action_updates_activity_and_decision_log(tmp_path: Path) -> None:

    sub = _make_subscriber(tmp_path)
    sub.notify(PipelineState(phase="development"))
    sub.record_permission_prompt_action(
        agent_name="claude/sonnet",
        prompt_summary="Allow this action?",
        selected_option="Allow once",
    )

    snapshot = sub.build_snapshot(PipelineState(phase="development"))
    assert snapshot is not None
    assert snapshot.last_activity_line is not None
    assert "auto-answered permission prompt" in snapshot.last_activity_line
    assert "Allow once" in snapshot.last_activity_line
    assert any(
        entry[1] == "permission_prompt_auto_answered" and "Allow this action?" in entry[2]
        for entry in sub.decision_log
    )
