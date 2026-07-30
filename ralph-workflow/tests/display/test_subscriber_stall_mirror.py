"""Regression coverage for watchdog stall assessment mirroring (S-1)."""

from __future__ import annotations

from queue import Queue

from ralph.agents.idle_watchdog import WaitingStatusEvent, WaitingStatusKind
from ralph.display.snapshot import PipelineSnapshot
from ralph.display.subscriber import PipelineSubscriber
from ralph.pipeline.state import PipelineState


def _event(kind: WaitingStatusKind, *, stall_active: bool) -> WaitingStatusEvent:
    return WaitingStatusEvent(
        kind=kind,
        cumulative_seconds=0.0,
        current_run_seconds=0.0,
        idle_elapsed_seconds=0.0,
        ceiling_seconds=60.0,
        suspect_threshold_seconds=None,
        stall_active=stall_active,
    )


def test_stall_mirror_regression_healthy_watchdog_event_clears_latched_sink(tmp_path) -> None:
    """S-1: a healthy event clears the host slot even without STALL_RESUMED kind."""
    received: list[str | None] = []
    subscriber = PipelineSubscriber(
        queue=Queue[PipelineSnapshot](maxsize=4),
        workspace_root=tmp_path,
        run_id="stall-mirror",
        watchdog_attention_sink=received.append,
    )
    subscriber.notify(PipelineState(phase="development", budget_caps={"iteration": 1}))

    subscriber.record_waiting_status(_event(WaitingStatusKind.STALLED, stall_active=True))
    subscriber.record_waiting_status(_event(WaitingStatusKind.PROGRESS, stall_active=False))

    assert received == ["stalled", None]
