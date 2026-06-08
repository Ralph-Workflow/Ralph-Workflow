"""Anti-drift guard: the two activity feed paths converge on one renderer.

Agent output reaches the display through two entry points by necessity (different
I/O contexts):
  - ``ParallelDisplay.activity_router.push_raw_line`` (per raw line, with overflow
    ref) — used by the subprocess executor;
  - ``stream_parsed_agent_activity`` → ``ParallelDisplay.emit_parsed_event`` (an
    iterable with output sinks) — used by the effect executor / smoke.

Both must classify with the SAME ``map_parser_type_to_kind`` and render through the
SAME ``_emit_activity_event``, so a given event renders identically regardless of
path. These pins fail if the two paths drift apart.
"""

from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from rich.console import Console

from ralph.display import activity_router
from ralph.display.activity_model import ActivityEventKind, ActivityProvider
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.pipeline import activity_stream
from ralph.pipeline.activity_stream import stream_parsed_agent_activity


def _display() -> tuple[ParallelDisplay, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=200)
    pd = ParallelDisplay(
        make_display_context(console=console, env={"CI": "1"}),
        workspace_root=Path(tempfile.mkdtemp()),
    )
    return pd, buf


def _tool_lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if "[tool]" in ln]


def _strip_ts(line: str) -> str:
    """Drop the leading wall-clock timestamp so two renders can be compared."""
    return line.split(" ", 1)[1] if " " in line else line


def test_kind_mapping_is_single_source() -> None:
    # activity_stream must reuse activity_router's map_parser_type_to_kind, not a copy.
    assert activity_stream.map_parser_type_to_kind is activity_router.map_parser_type_to_kind


def test_both_feed_paths_render_tool_use_identically() -> None:
    """A tool_use event renders the same [tool] line whether it arrives as raw
    NDJSON via the router or pre-parsed via emit_parsed_event."""
    # Path A: raw NDJSON through the router (subprocess-executor path).
    pd_a, buf_a = _display()
    raw = json.dumps(
        {
            "type": "content_block_start",
            "content_block": {
                "type": "tool_use",
                "name": "mcp__ralph__read_file",
                "input": {"path": "a/b.py"},
            },
        }
    )
    pd_a.activity_router.push_raw_line("u1", raw, provider=ActivityProvider.CLAUDE)
    pd_a.stop()

    # Path B: the pre-parsed event through emit_parsed_event (stream path).
    pd_b, buf_b = _display()
    pd_b.emit_parsed_event(
        "u1",
        ActivityEventKind.TOOL_USE,
        "mcp__ralph__read_file",
        {"input": {"path": "a/b.py"}},
    )
    pd_b.stop()

    tool_a = _tool_lines(buf_a.getvalue())
    tool_b = _tool_lines(buf_b.getvalue())
    assert tool_a, f"router path produced no [tool] line:\n{buf_a.getvalue()}"
    # Compare without the leading timestamp (which differs by wall-clock).
    assert [_strip_ts(x) for x in tool_a] == [_strip_ts(x) for x in tool_b]


def _mock_state(agent: str = "opencode/minimax") -> MagicMock:
    s = MagicMock()
    s.phase = "development"
    s.budget_caps = {"iteration": 1}
    s.outer_progress = {"iteration": 1}
    s.review_outcome = None
    s.interrupted_by_user = False
    s.last_error = None
    s.pr_url = None
    s.metrics.total_agent_calls = 0
    s.metrics.total_continuations = 0
    s.metrics.total_fallbacks = 0
    s.metrics.total_retries = 0
    s.worker_states = {}
    s.work_units = []
    s.previous_phase = None
    s.current_agent = MagicMock(return_value=agent)
    return s


def test_stream_path_records_each_tool_call_once() -> None:
    """Regression: stream_parsed_agent_activity must not double-record a tool_use
    on the display's subscriber (which would inflate the repeat count so a SINGLE
    call renders (x2))."""
    pd, buf = _display()
    pd.subscriber.notify(_mock_state())
    line = json.dumps(
        {
            "type": "content_block_start",
            "content_block": {
                "type": "tool_use",
                "name": "mcp__ralph__read_file",
                "input": {"path": "a/b.py"},
            },
        }
    )
    stream_parsed_agent_activity([line], "claude", "opencode/minimax", pd)
    pd.stop()

    # A single tool call must count as 1, not 2.
    assert pd.subscriber._active_tool_repeat == 1
    assert "(x2)" not in buf.getvalue(), f"single call must not render (x2):\n{buf.getvalue()}"
