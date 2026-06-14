"""Tests for the AGY command builder's model_flag wiring."""

from __future__ import annotations

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
    config = AgentConfig(
        cmd="agy",
        print_flag="--print",
        model_flag="--model gemini-3.5-flash-low",
        transport=AgentTransport.AGY,
    )

    cmd = build_command(config, str(prompt_file), options=BuildCommandOptions())

    assert "--model" in cmd
    assert "gemini-3.5-flash-low" in cmd


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
    config = AgentConfig(
        cmd="agy",
        print_flag="--print",
        model_flag="--model gemini-3.5-flash-low",
        transport=AgentTransport.AGY,
    )

    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(model_flag="--model gemini-3.5-flash-high"),
    )

    assert "--model" in cmd
    assert "gemini-3.5-flash-high" in cmd
    assert "gemini-3.5-flash-low" not in cmd
