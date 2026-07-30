from __future__ import annotations

from pathlib import Path
from queue import Queue
from typing import TYPE_CHECKING
from unittest.mock import patch

from rich.console import Console

from ralph.agents.idle_watchdog import WaitingStatusEvent, WaitingStatusKind
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.subscriber import PipelineSubscriber
from ralph.pipeline.state import PipelineState
from ralph.pipeline.worker_state import WorkerStatus

if TYPE_CHECKING:
    from ralph.display.snapshot import PipelineSnapshot


def test_no_args_constructs_in_non_tty_env() -> None:
    pd = ParallelDisplay(make_display_context())
    assert pd._ctx.width > 0


def test_no_args_constructs_in_tty_env() -> None:
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(make_display_context(console=console, env={}))
    assert pd._ctx.width == 120


def test_emit_lines_mode_writes_to_console() -> None:
    console = Console(force_terminal=False, width=120, record=True)
    pd = ParallelDisplay(make_display_context(console=console, env={"CI": "1"}))
    pd.emit("u1", "hi from lines mode")
    text = console.export_text()
    assert "hi from lines mode" in text
    assert "[u1]" in text


def test_set_status_does_not_call_subscriber_notify() -> None:
    console = Console(force_terminal=True, width=120)

    q: Queue[PipelineSnapshot] = Queue(maxsize=64)
    sub = PipelineSubscriber(
        queue=q,
        workspace_root=Path("/tmp"),
        run_id="test-run",
    )

    with patch.object(sub, "notify", wraps=sub.notify) as notify_mock:
        pd = ParallelDisplay(
            make_display_context(console=console, env={}),
            subscriber=sub,
        )
        pd.set_status("u1", WorkerStatus.RUNNING)

    notify_mock.assert_not_called()


def test_subscriber_property_exposed() -> None:
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(make_display_context(console=console, env={}))
    assert isinstance(pd.subscriber, PipelineSubscriber)


def test_injected_subscriber_used_directly() -> None:
    console = Console(force_terminal=True, width=120)
    q: Queue[PipelineSnapshot] = Queue(maxsize=8)
    sub = PipelineSubscriber(
        queue=q,
        workspace_root=Path("/tmp"),
        run_id="injected",
    )
    pd = ParallelDisplay(make_display_context(console=console, env={}), subscriber=sub)
    assert pd.subscriber is sub


# ---------------------------------------------------------------------------
# wt-047-stall-label (DA-001): the injected-subscriber path must populate
# the host's watchdog_attention surface so the bar mirrors STALLED.
#
# Pre-fix the constructor bound the sink only when it CONSTRUCTED its own
# subscriber; the supported ``subscriber=`` path left the supplied
# subscriber's ``watchdog_attention_sink`` slot unset, so a watchdog
# STALLED transition was silently dropped on that path.
# ---------------------------------------------------------------------------

def test_injected_subscriber_receives_stalled_event(tmp_path: Path) -> None:
    """DA-001 regression: an injected subscriber + STALLED event drives the host.

    Sends a real ``WaitingStatusKind.STALLED`` event through an
    injected subscriber and asserts the host's
    ``watchdog_attention`` is ``"stalled"``. Pre-fix the supplied
    subscriber's sink was never bound and ``display.watchdog_attention``
    stayed ``None``.
    """
    console = Console(force_terminal=True, width=120)
    q: Queue[PipelineSnapshot] = Queue(maxsize=8)
    sub = PipelineSubscriber(
        queue=q,
        workspace_root=tmp_path,
        run_id="injected-stall",
    )
    pd = ParallelDisplay(make_display_context(console=console, env={}), subscriber=sub)

    # Prime the subscriber with a state so record_waiting_status has
    # something to snapshot from.
    state = PipelineState(
        phase="development",
        budget_caps={"iteration": 1, "reviewer_pass": 1},
    )
    sub.notify(state)

    # Send a real STALLED event through the injected subscriber and
    # assert the host picks it up.
    sub.record_waiting_status(
        WaitingStatusEvent(
            kind=WaitingStatusKind.STALLED,
            cumulative_seconds=1800.0,
            current_run_seconds=0.0,
            idle_elapsed_seconds=42.0,
            ceiling_seconds=1800.0,
            suspect_threshold_seconds=600.0,
            diagnostic={},
            stall_active=True,
        )
    )
    assert pd.watchdog_attention == "stalled"

    # Clear via STALL_RESUMED.
    sub.record_waiting_status(
        WaitingStatusEvent(
            kind=WaitingStatusKind.STALL_RESUMED,
            cumulative_seconds=1800.0,
            current_run_seconds=0.0,
            idle_elapsed_seconds=43.0,
            ceiling_seconds=1800.0,
            suspect_threshold_seconds=600.0,
            diagnostic={},
            stall_active=False,
        )
    )
    assert pd.watchdog_attention is None
