"""Tests for _dispatch_waiting_event cloud progress forwarding."""

from __future__ import annotations

import queue
from typing import TYPE_CHECKING

from ralph.agents.idle_watchdog import WaitingStatusEvent, WaitingStatusKind
from ralph.display.subscriber import PipelineSubscriber
from ralph.pipeline.runner import _dispatch_waiting_event

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.display.snapshot import PipelineSnapshot


def _event(
    kind: WaitingStatusKind,
    *,
    cumulative: float = 120.0,
    run: float = 60.0,
    ceiling: float = 1800.0,
    diagnostic: dict[str, str | int | float | bool] | None = None,
) -> WaitingStatusEvent:
    return WaitingStatusEvent(
        kind=kind,
        cumulative_seconds=cumulative,
        current_run_seconds=run,
        idle_elapsed_seconds=15.0,
        ceiling_seconds=ceiling,
        suspect_threshold_seconds=600.0,
        diagnostic=diagnostic or {},
    )


def _make_subscriber(tmp_path: Path) -> PipelineSubscriber:
    q: queue.Queue[PipelineSnapshot] = queue.Queue(maxsize=64)
    return PipelineSubscriber(queue=q, workspace_root=tmp_path, run_id="test-run")


def test_progress_event_does_not_call_cloud_report_progress(tmp_path: Path) -> None:
    """PROGRESS events do not trigger cloud reporting (avoids cloud chatter)."""
    calls: list[object] = []
    sub = _make_subscriber(tmp_path)

    _dispatch_waiting_event(
        _event(WaitingStatusKind.PROGRESS),
        subscriber=sub,
        unit_id="test-agent",
        agent_name="test-agent",
        cloud_progress=calls.append,
    )
    assert calls == []


def test_suspected_frozen_calls_cloud_report_progress(tmp_path: Path) -> None:
    """SUSPECTED_FROZEN events are forwarded to cloud_progress callable."""
    calls: list[object] = []
    sub = _make_subscriber(tmp_path)

    _dispatch_waiting_event(
        _event(
            WaitingStatusKind.SUSPECTED_FROZEN,
            diagnostic={"evidence": "time_and_workspace_quiet"},
        ),
        subscriber=sub,
        unit_id="test-agent",
        agent_name="test-agent",
        cloud_progress=calls.append,
    )
    assert len(calls) == 1
    dispatched = calls[0]
    assert isinstance(dispatched, WaitingStatusEvent)
    assert dispatched.kind == WaitingStatusKind.SUSPECTED_FROZEN


def test_hard_stop_calls_cloud_report_progress(tmp_path: Path) -> None:
    """HARD_STOP events are forwarded to cloud_progress callable."""
    calls: list[object] = []
    sub = _make_subscriber(tmp_path)

    _dispatch_waiting_event(
        _event(WaitingStatusKind.HARD_STOP, diagnostic={"scoped_child_active": True}),
        subscriber=sub,
        unit_id="test-agent",
        agent_name="test-agent",
        cloud_progress=calls.append,
    )
    assert len(calls) == 1
    dispatched = calls[0]
    assert isinstance(dispatched, WaitingStatusEvent)
    assert dispatched.kind == WaitingStatusKind.HARD_STOP


def test_entered_event_does_not_call_cloud_report_progress(tmp_path: Path) -> None:
    """ENTERED events do not trigger cloud reporting."""
    calls: list[object] = []
    sub = _make_subscriber(tmp_path)

    _dispatch_waiting_event(
        _event(WaitingStatusKind.ENTERED),
        subscriber=sub,
        unit_id="test-agent",
        agent_name="test-agent",
        cloud_progress=calls.append,
    )
    assert calls == []


def test_cloud_failure_does_not_propagate(tmp_path: Path) -> None:
    """An exception raised by cloud_progress does not propagate; subscriber is still called."""
    sub = _make_subscriber(tmp_path)
    record_calls: list[str] = []

    def _bad_cloud(evt: object) -> None:
        msg = "cloud error"
        raise RuntimeError(msg)

    original_record = sub.record_waiting_status

    def _capturing_record(
        event: object,
        *,
        unit_id: str | None = None,
        agent_name: str | None = None,
    ) -> None:
        record_calls.append("called")
        original_record(event, unit_id=unit_id, agent_name=agent_name)

    sub.record_waiting_status = _capturing_record

    _dispatch_waiting_event(
        _event(WaitingStatusKind.SUSPECTED_FROZEN),
        subscriber=sub,
        unit_id="test-agent",
        agent_name="test-agent",
        cloud_progress=_bad_cloud,
    )
    assert record_calls == ["called"]


def test_no_cloud_progress_when_none(tmp_path: Path) -> None:
    """When cloud_progress=None, no error occurs for any event kind."""
    sub = _make_subscriber(tmp_path)
    for kind in WaitingStatusKind:
        _dispatch_waiting_event(
            _event(kind),
            subscriber=sub,
            unit_id="test-agent",
            agent_name="test-agent",
            cloud_progress=None,
        )
