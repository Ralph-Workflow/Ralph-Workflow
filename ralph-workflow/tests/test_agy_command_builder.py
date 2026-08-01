"""Tests for the AGY command builder's measured v1.1.8 flag wiring."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.invoke import BuildCommandOptions, build_command
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig

if TYPE_CHECKING:
    from pathlib import Path


def _make_prompt_file(tmp_path: Path, text: str = "hello") -> Path:
    prompt_file = tmp_path / "task_prompt.md"
    prompt_file.write_text(text, encoding="utf-8")
    return prompt_file


def test_build_agy_command_applies_model_and_effort_from_config(tmp_path: Path) -> None:
    config = AgentConfig(
        cmd="agy",
        print_flag="--print",
        model_flag="--model gemini-3.6-flash-low --effort medium",
        transport=AgentTransport.AGY,
    )

    cmd = build_command(config, str(_make_prompt_file(tmp_path)), options=BuildCommandOptions())

    assert cmd[cmd.index("--model") : cmd.index("--model") + 2] == [
        "--model",
        "gemini-3.6-flash-low",
    ]
    assert cmd[cmd.index("--effort") : cmd.index("--effort") + 2] == ["--effort", "medium"]


def test_build_agy_command_options_model_flag_overrides_config(tmp_path: Path) -> None:
    config = AgentConfig(
        cmd="agy",
        print_flag="--print",
        model_flag="--model gemini-3.6-flash-low",
        transport=AgentTransport.AGY,
    )

    cmd = build_command(
        config,
        str(_make_prompt_file(tmp_path)),
        options=BuildCommandOptions(model_flag="--model claude-sonnet-4-6"),
    )

    assert cmd[cmd.index("--model") + 1] == "claude-sonnet-4-6"
    assert "gemini-3.6-flash-low" not in cmd


def test_agy_command_yolo_flag_precedes_model_and_print(tmp_path: Path) -> None:
    config = AgentConfig(
        cmd="agy",
        print_flag="--print",
        model_flag="--model gemini-3.6-flash-low",
        yolo_flag="--dangerously-skip-permissions",
        transport=AgentTransport.AGY,
    )

    cmd = build_command(config, str(_make_prompt_file(tmp_path)), options=BuildCommandOptions())

    assert cmd[0] == "agy"
    output_format_index = cmd.index("--output-format")
    assert cmd[output_format_index : output_format_index + 2] == ["--output-format", "stream-json"]
    assert cmd.count("--output-format") == 1
    assert (
        output_format_index
        < cmd.index("--dangerously-skip-permissions")
        < cmd.index("--model")
        < cmd.index("--print")
    )


def test_agy_command_add_dir_present_with_workspace(tmp_path: Path) -> None:
    config = AgentConfig(cmd="agy", print_flag="--print", transport=AgentTransport.AGY)

    cmd = build_command(
        config,
        str(_make_prompt_file(tmp_path)),
        options=BuildCommandOptions(workspace_path=str(tmp_path)),
    )

    assert cmd[cmd.index("--add-dir") + 1] == str(tmp_path.resolve())
