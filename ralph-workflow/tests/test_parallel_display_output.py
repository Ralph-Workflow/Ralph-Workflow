from __future__ import annotations

import io
import queue

import pytest
from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.pipeline.state import PipelineState


def test_default_mode_emit_reduces_rich_markup() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120, color_system=None)
    pd = ParallelDisplay(make_display_context(console=console, env={"CI": "1"}))
    pd.emit("unit-1", "[green]hello[/green]")
    text = buf.getvalue()
    assert "[output][unit-1] hello\n" in text
    assert "[green]hello[/green]" not in text


def test_emit_analysis_result_in_default_mode_records_to_decision_log() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120, color_system=None)
    pd = ParallelDisplay(make_display_context(console=console, env={"CI": "1"}))
    pd.emit_analysis_result("development_analysis", "proceed", "all tests pass")
    text = buf.getvalue()
    assert text == ""
    log = pd.subscriber.decision_log
    assert any(entry[1].lower() == "proceed" and "all tests pass" in entry[2] for entry in log), log


def test_emit_analysis_result_updates_subscriber_state_in_default_mode() -> None:
    console = Console(file=io.StringIO(), force_terminal=True, width=120)
    pd = ParallelDisplay(make_display_context(console=console, env={}))
    pd.emit_analysis_result("development_analysis", "proceed", "all good")
    subscriber = pd.subscriber
    log = subscriber.decision_log
    assert any(entry[1].lower() == "proceed" and "all good" in entry[2] for entry in log), log


@pytest.mark.timeout_seconds(5)
def test_record_activity_updates_snapshot_fields() -> None:
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

    snap = pd.subscriber.build_snapshot(state)
    assert snap is not None
    assert snap.active_agent == "developer"
    assert snap.active_tool == "edit_file"
    assert snap.active_path == "src/foo.py"
    assert snap.active_workdir == "/tmp/project"
    assert snap.active_command == "python -m pytest tests/test_foo.py"
    assert snap.last_activity_line == "I am editing foo.py"


def test_emit_updates_subscriber_snapshot_for_unit_scoped_raw_activity() -> None:
    console = Console(file=io.StringIO(), force_terminal=True, width=120)
    pd = ParallelDisplay(make_display_context(console=console, env={}))

    pd.emit("unit-1", "[green]hello[/green]")

    snap = pd.subscriber.build_snapshot(PipelineState(phase="development"))
    assert snap is not None
    assert snap.active_agent == "unit-1"
    assert snap.last_activity_line == "hello"


def test_long_unit_id_does_not_hide_payload_content() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120, color_system=None)
    pd = ParallelDisplay(make_display_context(console=console, env={}))

    pd.emit("opencode/minimax/MiniMax-M3", "Invoking agent: opencode/minimax/MiniMax-M3")

    out = buf.getvalue()
    assert "Invoking agent:" in out
    assert "[output][opencode/minimax/Mini..." in out


def test_emit_refreshes_visible_activity_when_line_changes_but_unit_is_same() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=160, color_system=None)
    pd = ParallelDisplay(make_display_context(console=console, env={}))

    pd.emit("unit-1", "Invoking agent: dev")
    pd.emit("unit-1", "Agent process started; waiting for first output")

    out = buf.getvalue()
    assert "Invoking agent: dev" in out
    assert "Agent process started; waiting for first output" in out
