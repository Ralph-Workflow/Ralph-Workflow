from __future__ import annotations

from pathlib import Path
from queue import Queue
from typing import TYPE_CHECKING
from unittest.mock import patch

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.subscriber import PipelineSubscriber
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
