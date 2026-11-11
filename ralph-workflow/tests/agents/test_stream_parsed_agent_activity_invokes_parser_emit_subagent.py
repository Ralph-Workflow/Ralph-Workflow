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
        tool_use_calls = [line for line in captured if line.startswith("tool_use:")]
        assert len(tool_use_calls) >= 1, (
            "stream_parsed_agent_activity MUST invoke the subagent sink"
            f" for tool_use lines; captured={captured}"
        )
    finally:
        reset_subagent_sink(token)


def test_stream_parsed_agent_activity_invokes_sink_for_text_lines(tmp_path: Path) -> None:
    captured: list[str] = []

    def _capture(line: str) -> None:
        captured.append(line)

    token = set_subagent_sink(_capture)
    try:
        pd, _buf = _make_display(tmp_path)
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

    assert len(rendered) == 1
    assert "INFO" in rendered[0]
    assert "WARN" not in rendered[0]
    assert "meaningful provider output" in rendered[0]


def test_stream_parsed_agent_activity_does_not_invoke_sink_when_none_set(
    tmp_path: Path,
) -> None:
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
    stream_parsed_agent_activity(
        [tool_line],
        parser_type="claude",
        agent_name="claude/sonnet",
        display=pd,
    )
    invoke_subagent_sink("test")


def test_stream_parsed_agent_activity_swallows_sink_exception(tmp_path: Path) -> None:

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
        stream_parsed_agent_activity(
            [tool_line],
            parser_type="claude",
            agent_name="claude/sonnet",
            display=pd,
        )
    finally:
        reset_subagent_sink(token)
