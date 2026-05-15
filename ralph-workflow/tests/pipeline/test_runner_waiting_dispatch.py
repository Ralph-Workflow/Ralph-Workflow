"""Tests for _dispatch_waiting_event subscriber forwarding."""

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


def test_dispatch_calls_subscriber_for_all_kinds(tmp_path: Path, monkeypatch) -> None:
    calls: list[WaitingStatusEvent] = []
    sub = _make_subscriber(tmp_path)

    def _record(event: WaitingStatusEvent, *, unit_id=None, agent_name=None) -> None:
        calls.append(event)

    monkeypatch.setattr(sub, "record_waiting_status", _record)

    for kind in WaitingStatusKind:
        _dispatch_waiting_event(
            _event(kind),
            subscriber=sub,
            unit_id="test-agent",
            agent_name="test-agent",
        )

    assert len(calls) == len(WaitingStatusKind)


def test_dispatch_with_none_subscriber_does_not_raise() -> None:
    for kind in WaitingStatusKind:
        _dispatch_waiting_event(
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

    _dispatch_waiting_event(
        _event(WaitingStatusKind.SUSPECTED_FROZEN),
        subscriber=sub,
        unit_id="test-agent",
        agent_name="test-agent",
    )
