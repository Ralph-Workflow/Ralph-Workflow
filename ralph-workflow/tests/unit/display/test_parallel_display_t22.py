from __future__ import annotations

from pathlib import Path
from queue import Queue

from rich.console import Console

from ralph.display.mode import NARROW_THRESHOLD as _EXPECTED_NARROW_THRESHOLD
from ralph.display.mode import detect_mode as _detect_mode_from_mode
from ralph.display.parallel_display import NARROW_THRESHOLD, ParallelDisplay, detect_mode
from ralph.display.subscriber import PipelineSubscriber
from ralph.pipeline.worker_state import WorkerStatus


def test_no_args_constructs_in_non_tty_env() -> None:
    pd = ParallelDisplay()
    assert pd.mode == "lines"


def test_no_args_constructs_in_tty_env() -> None:
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(console, {})
    assert pd.mode == "lines"


def test_mode_override_dashboard_is_coerced_to_lines() -> None:
    console = Console(force_terminal=False, width=40)
    pd = ParallelDisplay(console, {"CI": "1"}, mode="lines")
    assert pd.mode == "lines"


def test_mode_override_lines_stays_lines() -> None:
    console = Console(force_terminal=True, width=200)
    pd = ParallelDisplay(console, {}, mode="lines")
    assert pd.mode == "lines"


def test_emit_lines_mode_writes_to_console() -> None:
    console = Console(force_terminal=False, width=120, record=True)
    pd = ParallelDisplay(console, {"CI": "1"})
    assert pd.mode == "lines"
    pd.emit("u1", "hi from lines mode")
    text = console.export_text()
    assert "hi from lines mode" in text
    assert "[u1]" in text


def test_set_status_does_not_call_subscriber_notify() -> None:
    console = Console(force_terminal=True, width=120)
    notify_calls: list[object] = []

    q: Queue[object] = Queue(maxsize=64)
    sub = PipelineSubscriber(
        queue=q,  # type: ignore[arg-type]
        workspace_root=Path("/tmp"),
        run_id="test-run",
    )
    original_notify = sub.notify

    def tracking_notify(state: object) -> None:
        notify_calls.append(state)
        original_notify(state)  # type: ignore[arg-type]

    sub.notify = tracking_notify  # type: ignore[method-assign]

    pd = ParallelDisplay(console, {}, mode="lines", subscriber=sub)
    pd.set_status("u1", WorkerStatus.RUNNING)

    assert notify_calls == []


def test_narrow_threshold_unchanged() -> None:
    assert NARROW_THRESHOLD == _EXPECTED_NARROW_THRESHOLD


def test_detect_mode_importable_from_parallel_display() -> None:
    assert detect_mode is _detect_mode_from_mode


def test_subscriber_property_exposed() -> None:
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(console, {})
    assert isinstance(pd.subscriber, PipelineSubscriber)


def test_injected_subscriber_used_directly() -> None:
    console = Console(force_terminal=True, width=120)
    q: Queue[object] = Queue(maxsize=8)
    sub = PipelineSubscriber(
        queue=q,  # type: ignore[arg-type]
        workspace_root=Path("/tmp"),
        run_id="injected",
    )
    pd = ParallelDisplay(console, {}, subscriber=sub)
    assert pd.subscriber is sub
