"""Focused tests for commit command activity rendering."""

from __future__ import annotations

import io

from rich.console import Console
from rich.text import Text

from ralph.agents.parsers import AgentOutputLine
from ralph.cli.commands import commit as commit_module


def test_commit_tool_render_escapes_markup_like_input_before_console_render() -> None:
    output = AgentOutputLine(
        type="tool_use",
        content="Write",
        metadata={
            "input": {
                "file_path": "/tmp/[unsafe].py",
                "newText": "[/{color}]",
            }
        },
    )

    rendered = commit_module._render_commit_agent_activity_line(output, "claude")

    assert rendered is not None
    assert isinstance(rendered, Text)

    console = Console(file=io.StringIO(), force_terminal=False, color_system=None)
    console.print(rendered)
