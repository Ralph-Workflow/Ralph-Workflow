"""Tests for ParallelDisplay completion panel wiring in emit_run_end."""

from __future__ import annotations

from queue import Queue
from typing import TYPE_CHECKING
from unittest.mock import patch

from rich.console import Console

from ralph.display.parallel_display import ParallelDisplay
from ralph.display.subscriber import PipelineSubscriber
from ralph.pipeline.state import PipelineState

if TYPE_CHECKING:
    from pathlib import Path


def _make_display(tmp_path: Path) -> tuple[ParallelDisplay, Console]:
    console = Console(
        record=True,
        width=120,
        force_terminal=False,
        color_system=None,
        highlight=False,
    )
    snapshot_q: Queue = Queue(maxsize=64)
    subscriber = PipelineSubscriber(
        queue=snapshot_q,
        workspace_root=tmp_path,
        run_id="test-run",
    )
    display = ParallelDisplay(console=console, workspace_root=tmp_path, subscriber=subscriber)
    return display, console


def test_emit_run_end_prints_completion_panel_when_state_complete(tmp_path: Path) -> None:
    display, console = _make_display(tmp_path)
    state = PipelineState(phase="complete")
    display.subscriber.notify(state)
    display.emit_run_end(phase="complete", total_agent_calls=1)
    out = console.export_text()
    assert "Pipeline Complete" in out
    assert "[run-end]" in out
    # [run-end] lines appear before the completion panel
    assert out.index("[run-end]") < out.index("Pipeline Complete")


def test_emit_run_end_without_last_state_still_emits_run_end_lines(tmp_path: Path) -> None:
    display, console = _make_display(tmp_path)
    display.emit_run_end(phase="complete", total_agent_calls=0)
    out = console.export_text()
    assert "[run-end]" in out
    assert "◆ Ralph run end" in out


def test_emit_run_end_failed_state_prints_pipeline_failed_panel(tmp_path: Path) -> None:
    display, console = _make_display(tmp_path)
    state = PipelineState(phase="failed", last_error="something broke")
    display.subscriber.notify(state)
    display.emit_run_end(phase="failed", total_agent_calls=0)
    out = console.export_text()
    assert "Pipeline Failed" in out
    assert "[run-end]" in out


def test_emit_run_end_non_terminal_phase_no_panel(tmp_path: Path) -> None:
    display, console = _make_display(tmp_path)
    state = PipelineState(phase="planning")
    display.subscriber.notify(state)
    display.emit_run_end(phase="planning", total_agent_calls=0)
    out = console.export_text()
    assert "Pipeline Complete" not in out
    assert "Pipeline Failed" not in out
    assert "[run-end]" in out


def test_emit_run_end_does_not_crash_on_summary_error(tmp_path: Path) -> None:
    display, console = _make_display(tmp_path)
    state = PipelineState(phase="complete")
    display.subscriber.notify(state)

    with patch(
        "ralph.display.parallel_display.emit_completion_summary",
        side_effect=RuntimeError("panel boom"),
    ):
        display.emit_run_end(phase="complete", total_agent_calls=0)

    out = console.export_text()
    # The [run-end] block still appears despite the summary failure
    assert "[run-end]" in out
    # An observable diagnostic is emitted instead of silently swallowing the error
    assert "completion panel failed" in out
