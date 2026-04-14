"""Tests for agent command construction."""

from __future__ import annotations

from ralph.agents.invoke import _build_command
from ralph.config.enums import JsonParserType
from ralph.config.models import AgentConfig


def test_build_command_includes_print_streaming_and_session_flags() -> None:
    config = AgentConfig(
        cmd="ccs work",
        output_flag="--output-format=stream-json",
        yolo_flag="--dangerously-skip-permissions",
        verbose_flag="--verbose",
        print_flag="--print",
        streaming_flag="--include-partial-messages",
        session_flag="--resume {}",
        json_parser=JsonParserType.CLAUDE,
    )

    cmd = _build_command(
        config,
        "PROMPT.md",
        model_flag="--model claude-sonnet-4",
        session_id="abc123",
        verbose=True,
    )

    assert cmd == [
        "ccs",
        "work",
        "--output-format=stream-json",
        "--print",
        "--include-partial-messages",
        "--resume",
        "abc123",
        "--dangerously-skip-permissions",
        "--verbose",
        "--model",
        "claude-sonnet-4",
        "PROMPT.md",
    ]


def test_build_command_omits_optional_flags_when_not_configured() -> None:
    config = AgentConfig(cmd="opencode", output_flag="--json-stream")

    cmd = _build_command(config, "PROMPT.md", session_id="abc123", verbose=False)

    assert cmd == ["opencode", "--json-stream", "PROMPT.md"]
