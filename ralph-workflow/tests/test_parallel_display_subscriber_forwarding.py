"""Tests that ParallelDisplay forwards TOOL_USE metadata to PipelineSubscriber."""

from __future__ import annotations

import json
from io import StringIO
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from rich.console import Console

from ralph.display.activity_model import ActivityProvider
from ralph.display.parallel_display import ParallelDisplay

if TYPE_CHECKING:
    from pathlib import Path


def _make_display(tmp_path: Path) -> tuple[ParallelDisplay, StringIO]:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=2000)
    pd = ParallelDisplay(console, {"CI": "1"}, workspace_root=tmp_path)
    return pd, buf


def _make_mock_state(agent: str = "claude/sonnet") -> MagicMock:
    state = MagicMock()
    state.phase = "development"
    state.iteration = 1
    state.total_iterations = 1
    state.reviewer_pass = 0
    state.total_reviewer_passes = 0
    state.review_issues_found = False
    state.interrupted_by_user = False
    state.last_error = None
    state.pr_url = None
    state.push_count = 0
    state.metrics.total_agent_calls = 0
    state.metrics.total_continuations = 0
    state.metrics.total_fallbacks = 0
    state.metrics.total_retries = 0
    state.worker_states = {}
    state.work_units = []
    state.previous_phase = None
    state.current_agent = MagicMock(return_value=agent)
    return state


def test_parallel_display_tool_use_forwards_to_subscriber(tmp_path: Path) -> None:
    """After a tool_use push_raw_line, subscriber._last_activity_line and tool fields are set."""
    pd, buf = _make_display(tmp_path)
    subscriber = pd.subscriber

    # Prime subscriber with a state so record_activity can publish snapshots
    state = _make_mock_state()
    subscriber.notify(state)
    buf.truncate(0)
    buf.seek(0)

    event = json.dumps(
        {
            "type": "content_block_start",
            "content_block": {
                "type": "tool_use",
                "name": "mcp__ralph__read_file",
                "input": {"path": "ralph/pipeline/runner.py"},
            },
        }
    )
    pd.activity_router.push_raw_line("u", event, provider=ActivityProvider.CLAUDE)

    # (a) last_activity_line contains friendly name and path
    last_line = subscriber._last_activity_line
    assert last_line is not None, "subscriber._last_activity_line must be set after tool_use"
    assert "ralph.read_file" in last_line or "read_file" in last_line, (
        f"Expected friendly tool name in last_activity_line, got: {last_line!r}"
    )
    assert "ralph/pipeline/runner.py" in last_line, (
        f"Expected path in last_activity_line, got: {last_line!r}"
    )

    # (b) structured fields: original tool name and path
    assert subscriber.last_tool_name == "mcp__ralph__read_file", (
        f"Expected last_tool_name='mcp__ralph__read_file', got: {subscriber.last_tool_name!r}"
    )
    assert subscriber.last_tool_path == "ralph/pipeline/runner.py", (
        f"Expected last_tool_path='ralph/pipeline/runner.py', got: {subscriber.last_tool_path!r}"
    )

    # (c) rendered output contains exactly one [activity] line from the snapshot
    out = buf.getvalue()
    activity_lines = [line for line in out.splitlines() if "[activity]" in line]
    assert len(activity_lines) >= 1, (
        f"Expected at least one [activity] line in output:\n{out}"
    )
    assert "[activity-line]" not in out, f"[activity-line] must not appear:\n{out}"


def test_subscriber_not_updated_for_non_tool_use_events(tmp_path: Path) -> None:
    """Text events do not alter subscriber._active_tool or last_tool_name."""
    pd, _buf = _make_display(tmp_path)
    subscriber = pd.subscriber

    state = _make_mock_state()
    subscriber.notify(state)

    text_event = json.dumps(
        {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "hello"},
        }
    )
    pd.activity_router.push_raw_line("u", text_event, provider=ActivityProvider.CLAUDE)

    assert subscriber.last_tool_name is None, (
        f"Text events must not set last_tool_name, got: {subscriber.last_tool_name!r}"
    )


def test_tool_use_workdir_and_command_forwarded(tmp_path: Path) -> None:
    """workdir and command from tool input are forwarded to the subscriber."""
    pd, _buf = _make_display(tmp_path)
    subscriber = pd.subscriber

    state = _make_mock_state()
    subscriber.notify(state)

    event = json.dumps(
        {
            "type": "content_block_start",
            "content_block": {
                "type": "tool_use",
                "name": "mcp__ralph__exec",
                "input": {"command": "make verify", "workdir": "/project"},
            },
        }
    )
    pd.activity_router.push_raw_line("u", event, provider=ActivityProvider.CLAUDE)

    assert subscriber.last_tool_name == "mcp__ralph__exec"
    assert subscriber._active_command == "make verify"
    assert subscriber._active_workdir == "/project"
