"""Tests for _dispatch_waiting_event subscriber forwarding."""

from __future__ import annotations

import queue
from typing import TYPE_CHECKING

from ralph.agents.idle_watchdog import WaitingStatusEvent, WaitingStatusKind
from ralph.display.subscriber import PipelineSubscriber
from ralph.pipeline.waiting_dispatch import dispatch_waiting_event

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

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


def test_dispatch_calls_subscriber_for_all_kinds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[WaitingStatusEvent] = []
    sub = _make_subscriber(tmp_path)

    def _record(
        event: WaitingStatusEvent, *, unit_id: object = None, agent_name: object = None
    ) -> None:
        calls.append(event)

    monkeypatch.setattr(sub, "record_waiting_status", _record)

    for kind in WaitingStatusKind:
        dispatch_waiting_event(
            _event(kind),
            subscriber=sub,
            unit_id="test-agent",
            agent_name="test-agent",
        )

    assert len(calls) == len(WaitingStatusKind)


def test_dispatch_with_none_subscriber_does_not_raise() -> None:
    for kind in WaitingStatusKind:
        dispatch_waiting_event(
            _event(kind),
            subscriber=None,
            unit_id="test-agent",
            agent_name="test-agent",
        )


def test_subscriber_exception_does_not_propagate(tmp_path: Path) -> None:
    sub = _make_subscriber(tmp_path)

    def _boom(event: object, *, unit_id: str | None = None, agent_name: str | None = None) -> None:
        raise RuntimeError("boom")

    sub.record_waiting_status = _boom

    dispatch_waiting_event(
        _event(WaitingStatusKind.SUSPECTED_FROZEN),
        subscriber=sub,
        unit_id="test-agent",
        agent_name="test-agent",
    )


def test_dispatch_preserves_subagent_activity_for_hard_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A HARD_STOP event with subagent_activity is forwarded to the subscriber.

    Dispatching a HARD_STOP event with ``subagent_activity='active task'``
    invokes the subscriber with the same ``subagent_activity`` value
    preserved on the ``WaitingStatusEvent`` (the subscriber then formats
    the ``subagent=`` suffix into the waiting status line).
    """
    calls: list[WaitingStatusEvent] = []
    sub = _make_subscriber(tmp_path)

    def _record(
        event: WaitingStatusEvent, *, unit_id: object = None, agent_name: object = None
    ) -> None:
        calls.append(event)

    monkeypatch.setattr(sub, "record_waiting_status", _record)

    event = WaitingStatusEvent(
        kind=WaitingStatusKind.HARD_STOP,
        cumulative_seconds=200.0,
        current_run_seconds=180.0,
        idle_elapsed_seconds=15.0,
        ceiling_seconds=1800.0,
        suspect_threshold_seconds=None,
        diagnostic={"scoped_child_active": True, "oldest_child_seconds": 200.0},
        subagent_activity="active task",
    )
    dispatch_waiting_event(event, subscriber=sub, unit_id="test-agent", agent_name="test-agent")

    assert len(calls) == 1
    assert calls[0].subagent_activity == "active task"
    assert calls[0].kind == WaitingStatusKind.HARD_STOP
