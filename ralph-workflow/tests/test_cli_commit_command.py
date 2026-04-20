"""Focused tests for commit command activity rendering."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    from pathlib import Path

from rich.console import Console
from rich.text import Text

from ralph.agents.parsers import AgentOutputLine
from ralph.cli.commands import commit as commit_module
from ralph.cli.commands.commit import CommitAttemptContext, _invoke_commit_agent_attempt
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig


def _claude_commit_agent() -> AgentConfig:
    return AgentConfig(
        cmd="claude -p",
        output_flag="--output-format=stream-json",
        yolo_flag="--permission-mode auto",
        can_commit=True,
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE,
    )


def test_commit_invocation_passes_default_current_prompt_to_materialize_system_prompt(
    tmp_path: Path,
) -> None:
    prompt_file = tmp_path / ".agent" / "tmp" / "commit_prompt.md"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text("Generate a commit message.", encoding="utf-8")

    with (
        patch("ralph.cli.commands.commit.materialize_system_prompt") as mock_materialize,
        patch("ralph.cli.commands.commit.invoke_agent", return_value=iter([])),
        patch("ralph.cli.commands.commit.delete_commit_message_artifacts"),
        patch("ralph.cli.commands.commit.read_commit_message_artifact", return_value=None),
    ):
        mock_materialize.return_value = str(tmp_path / ".agent" / "tmp" / "commit_system_prompt.md")
        _invoke_commit_agent_attempt(
            _claude_commit_agent(),
            prompt_file=str(prompt_file),
            attempt_context=CommitAttemptContext(
                repo_root=tmp_path,
                verbose=False,
                extra_env={},
            ),
        )

    mock_materialize.assert_called_once()
    _, kwargs = mock_materialize.call_args
    assert "default_current_prompt" in kwargs
    assert kwargs["default_current_prompt"]


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
