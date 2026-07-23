"""Behavior tests for ParallelDisplay emit_* helpers."""

from __future__ import annotations

import io
import queue

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay, strip_markup
from ralph.pipeline.state import PipelineState


def test_strip_markup_strips_valid_markup_and_preserves_literal_brackets() -> None:
    assert strip_markup("[green]ok[/green]") == "ok"
    assert strip_markup("[see foo/bar") == "[see foo/bar"
    assert strip_markup("plain text") == "plain text"


def test_default_mode_emit_reduces_rich_markup() -> None:
    """In the single default mode, Rich markup is reduced before rendering."""
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120, color_system=None)
    pd = ParallelDisplay(make_display_context(console=console, env={"CI": "1"}))
    pd.emit("unit-1", "[green]hello[/green]")
    text = buf.getvalue()
    assert "[content][unit-1] hello\n" in text
    assert "[green]hello[/green]" not in text


def test_emit_analysis_result_in_default_mode_records_to_decision_log() -> None:
    """emit_analysis_result records to decision_log but does NOT emit to console.

    The analysis decision is rendered as a titled block by render_analysis_decision
    in the phase handler, not by emit_analysis_result. This avoids double-rendering.
    """
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120, color_system=None)
    pd = ParallelDisplay(make_display_context(console=console, env={"CI": "1"}))
    pd.emit_analysis_result("development_analysis", "proceed", "all tests pass")
    text = buf.getvalue()
    # emit_analysis_result should NOT emit to console - the titled block is
    # rendered separately by render_analysis_decision in development.py/review.py
    assert text == ""
    # But it SHOULD record to decision_log for the completion summary
    log = pd.subscriber.decision_log
    assert any(entry[1].lower() == "proceed" and "all tests pass" in entry[2] for entry in log), log


def test_emit_analysis_result_updates_subscriber_state_in_default_mode() -> None:
    """emit_analysis_result updates subscriber state in the single default mode."""
    console = Console(file=io.StringIO(), force_terminal=True, width=120)
    pd = ParallelDisplay(make_display_context(console=console, env={}))
    pd.emit_analysis_result("development_analysis", "proceed", "all good")
    # subscriber state should reflect the analysis result
    subscriber = pd.subscriber
    log = subscriber.decision_log
    assert any(entry[1].lower() == "proceed" and "all good" in entry[2] for entry in log), log


def test_record_activity_updates_snapshot_fields() -> None:
    """record_activity propagates to snapshot fields and build_snapshot mirrors notify."""
    console = Console(file=io.StringIO(), force_terminal=True, width=120)
    pd = ParallelDisplay(make_display_context(console=console, env={}))

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


def test_emit_updates_subscriber_snapshot_for_unit_scoped_raw_activity() -> None:
    """Unit-scoped raw emits must update the subscriber state as well as the console."""
    console = Console(file=io.StringIO(), force_terminal=True, width=120)
    pd = ParallelDisplay(make_display_context(console=console, env={}))

    pd.emit("unit-1", "[green]hello[/green]")

    snap = pd.subscriber.build_snapshot(PipelineState(phase="development"))
    assert snap is not None
    assert snap.active_agent == "unit-1"
    assert snap.last_activity_line == "hello"


def test_long_unit_id_does_not_hide_payload_content() -> None:
    """Long unit ids are elided so the payload remains visible."""
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120, color_system=None)
    pd = ParallelDisplay(make_display_context(console=console, env={}))

    pd.emit("opencode/minimax/MiniMax-M3", "Invoking agent: opencode/minimax/MiniMax-M3")

    out = buf.getvalue()
    assert "Invoking agent:" in out
    assert "[content][opencode/minimax/Mini..." in out


def test_emit_refreshes_visible_activity_when_line_changes_but_unit_is_same() -> None:
    """Later activity lines for the same unit must still render when the payload changes."""
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=160, color_system=None)
    pd = ParallelDisplay(make_display_context(console=console, env={}))

    pd.emit("unit-1", "Invoking agent: dev")
    pd.emit("unit-1", "Agent process started; waiting for first output")

    out = buf.getvalue()
    assert "Invoking agent: dev" in out
    assert "Agent process started; waiting for first output" in out
