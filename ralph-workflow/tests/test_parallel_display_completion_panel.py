"""Tests for ParallelDisplay emit_run_end: [run-end] block wiring only.

Completion panels are now emitted by _emit_final_summary in runner.py,
not by emit_run_end.  These tests verify the [run-end] block behaviour
without asserting that the completion panel appears here.
"""

from __future__ import annotations

from pathlib import Path
from queue import Queue

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.subscriber import PipelineSubscriber
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy

_DEFAULT_POLICY = load_policy(Path(__file__).parent.parent / "ralph" / "policy" / "defaults")


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
        pipeline_policy=_DEFAULT_POLICY.pipeline,
    )
    display = ParallelDisplay(
        make_display_context(console=console, env={}),
        workspace_root=tmp_path,
        subscriber=subscriber,
    )
    return display, console


def test_emit_run_end_complete_state_emits_run_end_block_only(tmp_path: Path) -> None:
    display, console = _make_display(tmp_path)
    state = PipelineState(phase="complete")
    display.subscriber.notify(state)
    display.emit_run_end(phase="complete", total_agent_calls=1)
    out = console.export_text()
    assert "[run-end]" in out
    # Completion panel is emitted by _emit_final_summary, not here
    assert "Pipeline Complete" not in out


def test_emit_run_end_without_last_state_still_emits_run_end_lines(tmp_path: Path) -> None:
    display, console = _make_display(tmp_path)
    display.emit_run_end(phase="complete", total_agent_calls=0)
    out = console.export_text()
    assert "[run-end]" in out
    assert "◆ Ralph Workflow run end" in out


def test_emit_run_end_failed_state_emits_run_end_block_only(tmp_path: Path) -> None:
    failure_phase = _DEFAULT_POLICY.pipeline.recovery.failed_route
    display, console = _make_display(tmp_path)
    state = PipelineState(phase=failure_phase, last_error="something broke")
    display.subscriber.notify(state)
    display.emit_run_end(phase=failure_phase, total_agent_calls=0)
    out = console.export_text()
    assert "[run-end]" in out
    # Completion panel is emitted by _emit_final_summary, not here
    assert "Pipeline Failed" not in out


def test_emit_run_end_non_terminal_phase_no_panel(tmp_path: Path) -> None:
    display, console = _make_display(tmp_path)
    state = PipelineState(phase="planning")
    display.subscriber.notify(state)
    display.emit_run_end(phase="planning", total_agent_calls=0)
    out = console.export_text()
    assert "Pipeline Complete" not in out
    assert "Pipeline Failed" not in out
    assert "[run-end]" in out
