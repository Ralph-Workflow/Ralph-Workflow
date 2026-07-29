"""Live tool-result preview regressions."""

from __future__ import annotations

import io
import re

from rich.console import Console

from ralph.display.activity_event_kind import ActivityEventKind
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay


def _make_truecolor_display() -> tuple[ParallelDisplay, io.StringIO]:
    buffer = io.StringIO()
    console = Console(
        file=buffer,
        force_terminal=True,
        color_system="truecolor",
        width=120,
        highlight=False,
    )
    return ParallelDisplay(make_display_context(console=console, env={})), buffer


def test_unpreviewable_successful_results_keep_their_inline_body() -> None:
    """DA-002: result content is suppressed only when a preview prints."""
    for tool_name, body in (
        ("grep_files", '{"matches": [], "truncated": false}'),
        ("search_files", "SENTINEL_LOST_BODY: 0 matches for the query"),
        ("read_multiple_files", "SENTINEL_LOST_BODY: unavailable"),
    ):
        display, buffer = _make_truecolor_display()
        display.emit_parsed_event(
            unit_id="dev-1",
            kind=ActivityEventKind.TOOL_RESULT,
            content=body,
            metadata={"tool_name": tool_name, "exit_code": 0},
        )
        display.stop()
        plain = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", buffer.getvalue())
        assert plain.count(body) == 1


def test_nested_grep_pattern_reaches_result_emphasis() -> None:
    """DA-003: production-shaped nested tool input keeps match emphasis."""
    display, buffer = _make_truecolor_display()
    display.emit_parsed_event(
        unit_id="dev-1",
        kind=ActivityEventKind.TOOL_USE,
        content="mcp__ralph__grep_files",
        metadata={"input": {"path": "src", "pattern": "needle_token"}},
    )
    display.emit_parsed_event(
        unit_id="dev-1",
        kind=ActivityEventKind.TOOL_RESULT,
        content='{"matches": [{"path": "src/a.py", "line": 12, "text": "x = needle_token()"}]}',
        metadata={"tool_name": "mcp__ralph__grep_files", "exit_code": 0},
    )
    display.stop()
    output = buffer.getvalue()
    assert "\x1b[1;" in output
    assert "needle_token" in output
