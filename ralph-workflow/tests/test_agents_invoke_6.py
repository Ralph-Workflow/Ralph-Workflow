"""Tests for AGY command construction."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.invoke import BuildCommandOptions, build_command
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig

if TYPE_CHECKING:
    from pathlib import Path


def test_agy_command_includes_add_dir_workspace_path(tmp_path: Path) -> None:
    prompt_text = "Build the feature.\n"
    prompt_file = tmp_path / "task_prompt.md"
    prompt_file.write_text(prompt_text, encoding="utf-8")
    config = AgentConfig(cmd="agy", transport=AgentTransport.AGY, print_flag="--print")

    cmd = build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(workspace_path=tmp_path),
    )

    add_dir_index = cmd.index("--add-dir")
    print_index = cmd.index("--print")
    assert add_dir_index < print_index
    assert cmd[add_dir_index + 1] == str(tmp_path)
    assert cmd[-1] == prompt_text
