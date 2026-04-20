"""Integration tests for ParallelDisplay emit_* helpers and lines-mode parity."""

from __future__ import annotations

import io
import queue

from rich.console import Console

from ralph.display.parallel_display import ParallelDisplay, _strip_markup
from ralph.pipeline.state import PipelineState


def test_strip_markup_removes_rich_tags() -> None:
    assert _strip_markup("[green]ok[/green]") == "ok"
    assert _strip_markup("plain text") == "plain text"


def test_lines_mode_emit_strips_rich_markup() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120, color_system=None)
    pd = ParallelDisplay(console, {"CI": "1"}, mode="lines")
    assert pd.mode == "lines"
    pd.emit("unit-1", "[green]hello[/green]")
    text = buf.getvalue()
    assert "hello" in text
    assert "[/green]" not in text
    assert "[green]" not in text


def test_emit_analysis_result_in_lines_mode_emits_structured_string() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120, color_system=None)
    pd = ParallelDisplay(console, {"CI": "1"}, mode="lines")
    pd.emit_analysis_result("development_analysis", "proceed", "all tests pass")
    text = buf.getvalue()
    assert "[analysis]" in text
    assert "development_analysis" in text
    assert "proceed" in text
    assert "all tests pass" in text


def test_emit_analysis_result_updates_subscriber_state_in_dashboard_mode() -> None:
    console = Console(file=io.StringIO(), force_terminal=True, width=120)
    pd = ParallelDisplay(console, {}, mode="dashboard")
    pd.emit_analysis_result("development_analysis", "proceed", "all good")
    # subscriber state should reflect the analysis result
    subscriber = pd.subscriber
    log = subscriber.decision_log
    assert any(entry[1].lower() == "proceed" and "all good" in entry[2] for entry in log), log


def test_emit_phase_transition_records_into_decision_log() -> None:
    console = Console(file=io.StringIO(), force_terminal=False, width=120, color_system=None)
    pd = ParallelDisplay(console, {"CI": "1"}, mode="lines")
    pd.emit_phase_transition("planning", "development")
    log = pd.subscriber.decision_log
    assert any(entry[0] == "planning" and "development" in entry[1] for entry in log), log


def test_emit_phase_transition_writes_banner_in_lines_mode() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120, color_system=None)
    pd = ParallelDisplay(console, {"CI": "1"}, mode="lines")
    pd.emit_phase_transition("planning", "development")
    text = buf.getvalue()
    assert "Planning" in text
    assert "Development" in text


def test_record_activity_updates_snapshot_fields() -> None:
    """record_activity propagates to snapshot fields and build_snapshot mirrors notify."""
    console = Console(file=io.StringIO(), force_terminal=True, width=120)
    pd = ParallelDisplay(console, env={}, mode="dashboard")

    pd.subscriber.record_activity(
        unit_id="developer",
        agent_name="developer",
        line="I am editing foo.py",
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
