"""Behavior tests for ParallelDisplay emit_* helpers."""

from __future__ import annotations

import io
import queue

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay, strip_markup
from ralph.pipeline.state import PipelineState


def test_strip_markup_removes_rich_tags() -> None:
    assert strip_markup("[green]ok[/green]") == "ok"
    assert strip_markup("plain text") == "plain text"


def test_medium_mode_emit_strips_rich_markup() -> None:
    """In medium mode, emit strips rich markup from lines."""
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120, color_system=None)
    pd = ParallelDisplay(
        make_display_context(console=console, env={"CI": "1"}, force_mode="medium")
    )
    assert pd.mode == "medium"
    pd.emit("unit-1", "[green]hello[/green]")
    text = buf.getvalue()
    assert "hello" in text
    assert "[/green]" not in text
    assert "[green]" not in text


def test_emit_analysis_result_in_medium_mode_records_to_decision_log() -> None:
    """emit_analysis_result records to decision_log but does NOT emit to console.

    The analysis decision is rendered as a titled block by render_analysis_decision
    in the phase handler, not by emit_analysis_result. This avoids double-rendering.
    """
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120, color_system=None)
    pd = ParallelDisplay(
        make_display_context(console=console, env={"CI": "1"}, force_mode="medium")
    )
    pd.emit_analysis_result("development_analysis", "proceed", "all tests pass")
    text = buf.getvalue()
    # emit_analysis_result should NOT emit to console - the titled block is
    # rendered separately by render_analysis_decision in development.py/review.py
    assert text == ""
    # But it SHOULD record to decision_log for the completion summary
    log = pd.subscriber.decision_log
    assert any(entry[1].lower() == "proceed" and "all tests pass" in entry[2] for entry in log), log


def test_emit_analysis_result_updates_subscriber_state_in_medium_mode() -> None:
    """emit_analysis_result updates subscriber state in medium mode."""
    console = Console(file=io.StringIO(), force_terminal=True, width=120)
    pd = ParallelDisplay(make_display_context(console=console, env={}, force_mode="medium"))
    pd.emit_analysis_result("development_analysis", "proceed", "all good")
    # subscriber state should reflect the analysis result
    subscriber = pd.subscriber
    log = subscriber.decision_log
    assert any(entry[1].lower() == "proceed" and "all good" in entry[2] for entry in log), log


def test_record_activity_updates_snapshot_fields() -> None:
    """record_activity propagates to snapshot fields and build_snapshot mirrors notify."""
    console = Console(file=io.StringIO(), force_terminal=True, width=120)
    pd = ParallelDisplay(make_display_context(console=console, env={}, force_mode="medium"))

    pd.subscriber.record_activity(
        unit_id="developer",
        line="I am editing foo.py",
        agent_name="developer",
        tool_name="edit_file",
        path="src/foo.py",
        workdir="/tmp/project",
        command="python -m pytest tests/test_foo.py",
    )
    state = PipelineState(phase="development")
    pd.subscriber.notify(state)

    drained = None
    while True:
        try:
            drained = pd.subscriber.queue.get_nowait()
        except queue.Empty:
            break
    assert drained is not None
    assert drained.active_agent == "developer"
    assert drained.active_tool == "edit_file"
    assert drained.active_path == "src/foo.py"
    assert drained.active_workdir == "/tmp/project"
    assert drained.active_command == "python -m pytest tests/test_foo.py"
    assert drained.last_activity_line == "I am editing foo.py"

    # build_snapshot exposes the same projection without going through the queue.
    snap = pd.subscriber.build_snapshot(state)
    assert snap is not None
    assert snap.active_agent == "developer"
    assert snap.active_tool == "edit_file"
    assert snap.active_path == "src/foo.py"
    assert snap.active_workdir == "/tmp/project"
    assert snap.active_command == "python -m pytest tests/test_foo.py"
    assert snap.last_activity_line == "I am editing foo.py"
