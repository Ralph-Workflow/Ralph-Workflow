from __future__ import annotations

from pathlib import Path

from ralph.agents.invoke import BuildCommandOptions, build_command
from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport
from ralph.recovery.canary_probe_launch import (
    PROBE_NONINTERACTIVE_FLAGS,
    probe_command_builder,
    probe_launch_override,
)

_PROMPT_FILE = "/tmp/canary-probe-prompt.md"


def _interactive_config(**overrides: object) -> AgentConfig:
    values: dict[str, object] = {
        "cmd": "claude",
        "transport": AgentTransport.CLAUDE_INTERACTIVE,
        "yolo_flag": "--dangerously-skip-permissions",
        "verbose_flag": "--verbose",
        "session_flag": "--resume {}",
    }
    values.update(overrides)
    return AgentConfig(**values)


def test_ordinary_interactive_argv_is_unchanged_and_carries_no_print_flag() -> None:
    argv = build_command(
        _interactive_config(),
        _PROMPT_FILE,
        options=BuildCommandOptions(),
    )
    assert argv == ["claude", "--dangerously-skip-permissions", _PROMPT_FILE]


def test_probe_argv_adds_the_print_mode_flag_before_the_prompt() -> None:
    argv = probe_command_builder(
        _interactive_config(),
        _PROMPT_FILE,
        options=BuildCommandOptions(),
    )
    assert argv == ["claude", "--dangerously-skip-permissions", "-p", _PROMPT_FILE]


def test_probe_argv_keeps_the_permission_and_model_plumbing() -> None:
    argv = probe_command_builder(
        _interactive_config(model_flag="--model claude-sonnet-4-5"),
        _PROMPT_FILE,
        options=BuildCommandOptions(),
    )
    assert argv == [
        "claude",
        "--dangerously-skip-permissions",
        "--model",
        "claude-sonnet-4-5",
        "-p",
        _PROMPT_FILE,
    ]


def test_probe_argv_inserts_the_print_flag_before_the_prompt_separator(tmp_path: Path) -> None:
    prompt_file = tmp_path / "canary_probe_prompt.md"
    prompt_file.write_text("canary probe task", encoding="utf-8")
    argv = probe_command_builder(
        _interactive_config(),
        str(prompt_file),
        options=BuildCommandOptions(
            mcp_endpoint="http://127.0.0.1:9/mcp",
            workspace_path=tmp_path,
        ),
    )
    assert argv[-3] == "-p"
    assert argv[-2] == "--"
    assert argv[-1] == "canary probe task"


def test_probe_argv_does_not_invent_a_print_flag_for_a_transport_without_one() -> None:
    argv = probe_command_builder(
        _interactive_config(transport=AgentTransport.GENERIC),
        _PROMPT_FILE,
        options=BuildCommandOptions(),
    )
    assert argv == ["claude", "--dangerously-skip-permissions", _PROMPT_FILE]


def test_only_documented_transports_carry_a_probe_print_flag() -> None:
    assert dict(PROBE_NONINTERACTIVE_FLAGS) == {
        AgentTransport.CLAUDE_INTERACTIVE: "-p",
        AgentTransport.CLAUDE: "-p",
    }


def test_probe_launch_override_only_rewrites_the_overridden_build() -> None:
    config = _interactive_config()
    with probe_launch_override():
        overridden = build_command(config, _PROMPT_FILE, options=BuildCommandOptions())
    ordinary = build_command(config, _PROMPT_FILE, options=BuildCommandOptions())
    assert overridden == ["claude", "--dangerously-skip-permissions", "-p", _PROMPT_FILE]
    assert ordinary == ["claude", "--dangerously-skip-permissions", _PROMPT_FILE]
