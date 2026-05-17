"""Focused tests for commit command activity rendering."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    from pathlib import Path

from rich.console import Console
from rich.text import Text

from ralph.agents.invoke import build_invoke_options_from_config
from ralph.agents.parsers import AgentOutputLine
from ralph.cli.commands import commit as commit_module
from ralph.cli.commands.commit import (
    CommitAttemptContext,
    _collect_commit_agent_output,
    _invoke_commit_agent_attempt,
)
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig, GeneralConfig
from ralph.display.context import make_display_context
from ralph.mcp.tools.names import SUBMIT_ARTIFACT_TOOL, claude_tool_name

_OUTPUT_BATCH = 400


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

    # Create a minimal display context for the internal function
    display_context = make_display_context()

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
            display_context=display_context,
        )

    mock_materialize.assert_called_once()
    _, kwargs = mock_materialize.call_args
    assert "default_current_prompt" in kwargs
    assert kwargs["default_current_prompt"]


def test_submit_artifact_tool_name_claude_interactive() -> None:
    assert commit_module.submit_artifact_tool_name_for_transport(
        AgentTransport.CLAUDE_INTERACTIVE
    ) == claude_tool_name(SUBMIT_ARTIFACT_TOOL)


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

    rendered = commit_module.render_commit_agent_activity_line(output, "claude")

    assert rendered is not None
    assert isinstance(rendered, Text)

    console = Console(file=io.StringIO(), force_terminal=False, color_system=None)
    console.print(rendered)


def test_build_invoke_options_from_config_maps_all_timeout_fields() -> None:
    config = GeneralConfig(
        agent_idle_timeout_seconds=42.0,
        agent_idle_drain_window_seconds=1.5,
        agent_idle_max_waiting_on_child_seconds=900.0,
        agent_idle_poll_interval_seconds=0.1,
        agent_parent_exit_grace_seconds=3.0,
        agent_descendant_wait_timeout_seconds=20.0,
        agent_descendant_wait_poll_seconds=0.3,
        agent_process_exit_wait_seconds=10.0,
        agent_max_session_seconds=7200.0,
        agent_waiting_status_interval_seconds=60.0,
        agent_suspect_waiting_on_child_seconds=300.0,
        agent_idle_no_progress_waiting_on_child_seconds=120.0,
        agent_child_progress_ttl_seconds=30.0,
        agent_child_heartbeat_ttl_seconds=8.0,
        agent_child_stale_label_ttl_seconds=5.0,
        agent_child_exit_reconcile_seconds=2.0,
    )
    opts = build_invoke_options_from_config(config)

    assert opts.idle_timeout_seconds == config.agent_idle_timeout_seconds
    assert opts.drain_window_seconds == config.agent_idle_drain_window_seconds
    assert opts.max_waiting_on_child_seconds == config.agent_idle_max_waiting_on_child_seconds
    assert opts.idle_poll_interval_seconds == config.agent_idle_poll_interval_seconds
    assert opts.parent_exit_grace_seconds == config.agent_parent_exit_grace_seconds
    assert opts.descendant_wait_timeout_seconds == config.agent_descendant_wait_timeout_seconds
    assert opts.descendant_wait_poll_seconds == config.agent_descendant_wait_poll_seconds
    assert opts.process_exit_wait_seconds == config.agent_process_exit_wait_seconds
    assert opts.max_session_seconds == config.agent_max_session_seconds
    assert opts.waiting_status_interval_seconds == config.agent_waiting_status_interval_seconds
    assert opts.suspect_waiting_on_child_seconds == config.agent_suspect_waiting_on_child_seconds
    assert (
        opts.max_waiting_on_child_no_progress_seconds
        == config.agent_idle_no_progress_waiting_on_child_seconds
    )
    assert opts.child_progress_ttl_seconds == config.agent_child_progress_ttl_seconds
    assert opts.child_heartbeat_ttl_seconds == config.agent_child_heartbeat_ttl_seconds
    assert opts.child_stale_label_ttl_seconds == config.agent_child_stale_label_ttl_seconds
    assert opts.child_exit_reconcile_seconds == config.agent_child_exit_reconcile_seconds


def test_commit_invocation_passes_full_timeout_bundle(tmp_path: Path) -> None:
    prompt_file = tmp_path / ".agent" / "tmp" / "commit_prompt.md"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text("Generate a commit message.", encoding="utf-8")

    general_config = GeneralConfig(
        agent_idle_timeout_seconds=99.0,
        agent_idle_no_progress_waiting_on_child_seconds=55.0,
    )
    attempt_context = CommitAttemptContext(
        repo_root=tmp_path,
        verbose=False,
        extra_env={},
        general_config=general_config,
    )
    display_context = make_display_context()

    captured_options = []

    def fake_invoke_agent(agent: object, prompt_file: object, *, options: object=None) -> object:
        captured_options.append(options)
        return iter([])

    with (
        patch("ralph.cli.commands.commit.materialize_system_prompt", return_value=None),
        patch("ralph.cli.commands.commit.invoke_agent", side_effect=fake_invoke_agent),
        patch("ralph.cli.commands.commit.delete_commit_message_artifacts"),
        patch("ralph.cli.commands.commit.read_commit_message_artifact", return_value=None),
    ):
        _invoke_commit_agent_attempt(
            _claude_commit_agent(),
            prompt_file=str(prompt_file),
            attempt_context=attempt_context,
            display_context=display_context,
        )

    assert len(captured_options) == 1
    opts = captured_options[0]
    assert opts.idle_timeout_seconds == general_config.agent_idle_timeout_seconds
    assert (
        opts.max_waiting_on_child_no_progress_seconds
        == general_config.agent_idle_no_progress_waiting_on_child_seconds
    )


def test_collect_commit_agent_output_keeps_early_session_id_with_bounded_tail() -> None:
    display_context = make_display_context()
    session_line = '{"session_id":"sess-early"}'
    filler = ["x" * 8192 for _ in range(_OUTPUT_BATCH)]

    parsed_output, raw_output, resume_session_id = _collect_commit_agent_output(
        [session_line, *filler],
        parser_type="generic",
        agent_name="claude",
        verbose=False,
        display_context=display_context,
    )

    assert resume_session_id == "sess-early"
    assert len(raw_output) < _OUTPUT_BATCH
    assert len(parsed_output) < _OUTPUT_BATCH
