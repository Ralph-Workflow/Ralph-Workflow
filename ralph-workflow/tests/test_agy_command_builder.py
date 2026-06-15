"""Tests for the AGY command builder's model_flag wiring."""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from ralph.agents.invoke import BuildCommandOptions, build_command
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig


def _make_prompt_file(tmp_path: Path, text: str = "hello") -> Path:
    prompt_file = tmp_path / "task_prompt.md"
    prompt_file.write_text(text, encoding="utf-8")
    return prompt_file


def test_build_agy_command_applies_model_flag_from_config(tmp_path: Path) -> None:
    prompt_file = _make_prompt_file(tmp_path)
    display_name = "Claude Sonnet 4.6 (Thinking)"
    config = AgentConfig(
        cmd="agy",
        print_flag="--print",
        model_flag=f"--model {shlex.quote(display_name)}",
        transport=AgentTransport.AGY,
    )

    cmd = build_command(config, str(prompt_file), options=BuildCommandOptions())

    assert cmd[cmd.index("--model") + 1] == display_name


def test_build_agy_command_omits_model_flag_when_unset(tmp_path: Path) -> None:
    prompt_file = _make_prompt_file(tmp_path)
    config = AgentConfig(
        cmd="agy",
        print_flag="--print",
        transport=AgentTransport.AGY,
    )

    cmd = build_command(config, str(prompt_file), options=BuildCommandOptions())

    assert "--model" not in cmd


def test_build_agy_command_options_model_flag_overrides_config(tmp_path: Path) -> None:
    prompt_file = _make_prompt_file(tmp_path)
    config_display_name = "Claude Sonnet 4.6 (Thinking)"
    option_display_name = "Gemini 3.5 Flash (High)"
    config = AgentConfig(
        cmd="agy",
        print_flag="--print",
        model_flag=f"--model {shlex.quote(config_display_name)}",
        transport=AgentTransport.AGY,
    )

    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(model_flag=f"--model {shlex.quote(option_display_name)}"),
    )

    assert cmd[cmd.index("--model") + 1] == option_display_name
    assert config_display_name not in cmd


def test_agy_command_argv_starts_with_agy(tmp_path: Path) -> None:
    prompt_file = _make_prompt_file(tmp_path)
    config = AgentConfig(
        cmd="agy",
        print_flag="--print",
        transport=AgentTransport.AGY,
    )

    cmd = build_command(config, str(prompt_file), options=BuildCommandOptions())

    assert cmd[0] == "agy"


def test_agy_command_yolo_flag_precedes_print(tmp_path: Path) -> None:
    prompt_file = _make_prompt_file(tmp_path)
    config = AgentConfig(
        cmd="agy",
        print_flag="--print",
        yolo_flag="--dangerously-skip-permissions",
        transport=AgentTransport.AGY,
    )

    cmd = build_command(config, str(prompt_file), options=BuildCommandOptions())

    assert cmd.index("--dangerously-skip-permissions") < cmd.index("--print")


def test_agy_command_model_flag_is_single_argv_with_spaces_and_parens(
    tmp_path: Path,
) -> None:
    prompt_file = _make_prompt_file(tmp_path)
    display_name = "Claude Sonnet 4.6 (Thinking)"
    config = AgentConfig(
        cmd="agy",
        print_flag="--print",
        model_flag=f"--model {shlex.quote(display_name)}",
        transport=AgentTransport.AGY,
    )

    cmd = build_command(config, str(prompt_file), options=BuildCommandOptions())

    model_index = cmd.index("--model")
    assert cmd[model_index + 1] == display_name
    assert cmd[model_index : model_index + 2] == ["--model", display_name]


def test_agy_command_add_dir_present_with_workspace(tmp_path: Path) -> None:
    prompt_file = _make_prompt_file(tmp_path)
    config = AgentConfig(
        cmd="agy",
        print_flag="--print",
        transport=AgentTransport.AGY,
    )

    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(workspace_path=str(tmp_path)),
    )

    add_dir_index = cmd.index("--add-dir")
    assert cmd[add_dir_index + 1] == str(tmp_path.resolve())
