"""Activity-stream wiring test for parser.emit_subagent_activity.

The per-parser ``emit_subagent_activity`` hook is the unit; the
activity-stream integration is what feeds it to the per-run watchdog's
subagent sink via the ``invoke_subagent_sink`` contextvar.

This test pins the wiring: ``stream_parsed_agent_activity`` MUST call
``parser.emit_subagent_activity(parsed_line, invoke_subagent_sink)``
after each ``parser.parse`` iteration so the watchdog's
``record_subagent_work`` evidence surface stays fresh for ALL parsers
(Claude, OpenCode, Codex, Gemini, Pi, Agy, Generic, ClaudeInteractive).

The test drives ClaudeParser through ``stream_parsed_agent_activity``
with a captured sink list and asserts the sink received at least one
``tool_use:<name>`` call.  The sink is set via the
``invoke_subagent_sink`` contextvar so the activity stream's own
import resolves to the test's sink.

All tests use the in-memory pipeline fixture; no subprocess, no real
sleep, no FakeClock - the activity stream is a deterministic
generator.
"""

from __future__ import annotations

import json
from io import StringIO
from typing import TYPE_CHECKING

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.mcp.server._activity_sink import (
    invoke_subagent_sink,
    reset_subagent_sink,
    set_subagent_sink,
)
from ralph.pipeline.activity_stream import stream_parsed_agent_activity

if TYPE_CHECKING:
    from pathlib import Path


def _make_display(tmp_path: Path) -> tuple[ParallelDisplay, StringIO]:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=2000)
    pd = ParallelDisplay(
        make_display_context(console=console, env={"CI": "1"}),
        workspace_root=tmp_path,
    )
    return pd, buf


def test_stream_parsed_agent_activity_invokes_parser_emit_subagent(
    tmp_path: Path,
) -> None:
    """``stream_parsed_agent_activity`` MUST call
    ``parser.emit_subagent_activity(parsed_line, invoke_subagent_sink)``
    after each parser iteration so the watchdog's subagent sink receives
    per-tool activity for ALL parsers.

    Drive ClaudeParser with a single content_block_start tool_use line
    that emits a tool_use AgentOutputLine.  The captured sink list MUST
    receive exactly one ``tool_use:<name>`` invocation so the per-run
    watchdog's ``record_subagent_work`` evidence surface is refreshed.
    """
    captured: list[str] = []

    def _capture(line: str) -> None:
        captured.append(line)

    token = set_subagent_sink(_capture)
    try:
        tool_line = json.dumps(
            {
                "type": "content_block_start",
                "content_block": {
                    "type": "tool_use",
                    "name": "Bash",
                    "input": {"command": "ls"},
                },
            }
        )
        pd, _buf = _make_display(tmp_path)
        stream_parsed_agent_activity(
            [tool_line],
            parser_type="claude",
            agent_name="claude/sonnet",
            display=pd,
        )
        # The parser yielded a tool_use AgentOutputLine.  The activity
        # stream forwards it to the subagent sink via the
        # ``invoke_subagent_sink`` contextvar resolver.  At least one
        # sink call MUST have happened with a ``tool_use:`` prefix.
        tool_use_calls = [line for line in captured if line.startswith("tool_use:")]
        assert len(tool_use_calls) >= 1, (
            "stream_parsed_agent_activity MUST invoke the subagent sink"
            f" for tool_use lines; captured={captured}"
        )
    finally:
        reset_subagent_sink(token)


def test_stream_parsed_agent_activity_invokes_sink_for_text_lines(tmp_path: Path) -> None:
    """Text lines also flow through the parser hook so the watchdog's
    subagent channel sees model-text progress between tool calls.
    """
    captured: list[str] = []

    def _capture(line: str) -> None:
        captured.append(line)

    token = set_subagent_sink(_capture)
    try:
        pd, _buf = _make_display(tmp_path)
        # Drive ClaudeParser with content_block_delta text events.
        lines = [
            json.dumps(
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                }
            ),
            json.dumps(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "Hello world"},
                }
            ),
            json.dumps({"type": "content_block_stop", "index": 0}),
        ]
        stream_parsed_agent_activity(
            lines,
            parser_type="claude",
            agent_name="claude/sonnet",
            display=pd,
        )
        text_calls = [line for line in captured if line.startswith("text:")]
        assert len(text_calls) >= 1, (
            "text content_block_delta lines MUST also forward to the"
            f" subagent sink; captured={captured}"
        )
    finally:
        reset_subagent_sink(token)


def test_stream_parsed_agent_activity_renders_raw_content_verbatim() -> None:
    rendered: list[str] = []

    stream_parsed_agent_activity(
        ["meaningful provider output\n"],
        parser_type="generic",
        agent_name="provider/model",
        rendered_output_sink=rendered,
    )

    # After wt-028-display the pipeline runner routes through the single
    # agent-event renderer registry; the output carries the registry's
    # INFO carrier icon plus the agent prefix and body.
    assert len(rendered) == 1
    assert "provider/model" in rendered[0]
    assert "meaningful provider output" in rendered[0]
    # Plain-text path uses the icon (\u2139 for info) so meaning survives color-off.
    assert "\u2139" in rendered[0]


def test_stream_parsed_agent_activity_does_not_invoke_sink_when_none_set(
    tmp_path: Path,
) -> None:
    """When no subagent sink is registered, the parser hook MUST be a
    no-op (the activity stream MUST NOT crash).

    The set_subagent_sink contextvar defaults to None, so the hook's
    try/except wrapper swallows the AttributeError-equivalent and the
    stream continues to render the line normally.
    """
    # Ensure no sink is registered.
    pd, _buf = _make_display(tmp_path)
    tool_line = json.dumps(
        {
            "type": "content_block_start",
            "content_block": {
                "type": "tool_use",
                "name": "Bash",
                "input": {"command": "ls"},
            },
        }
    )
    # No exception = pass.
    stream_parsed_agent_activity(
        [tool_line],
        parser_type="claude",
        agent_name="claude/sonnet",
        display=pd,
    )
    # Direct check: invoke_subagent_sink with no registered sink is a no-op.
    invoke_subagent_sink("test")  # must not raise


def test_stream_parsed_agent_activity_swallows_sink_exception(tmp_path: Path) -> None:
    """A buggy sink that raises MUST NOT crash the activity stream.

    Mirrors the existing OpenCodeExecutionStrategy ``subagent_activity_sink``
    exception-swallow contract: a buggy sink cannot corrupt the line loop.
    """

    def bad_sink(line: str) -> None:
        raise RuntimeError(f"buggy sink for line: {line}")

    token = set_subagent_sink(bad_sink)
    try:
        pd, _buf = _make_display(tmp_path)
        tool_line = json.dumps(
            {
                "type": "content_block_start",
                "content_block": {
                    "type": "tool_use",
                    "name": "Bash",
                    "input": {"command": "ls"},
                },
            }
        )
        # The activity stream MUST swallow the exception and continue.
        stream_parsed_agent_activity(
            [tool_line],
            parser_type="claude",
            agent_name="claude/sonnet",
            display=pd,
        )
    finally:
        reset_subagent_sink(token)
