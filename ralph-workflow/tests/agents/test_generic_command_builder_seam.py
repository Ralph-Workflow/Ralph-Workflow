from __future__ import annotations

import dataclasses
from pathlib import Path

from ralph.agents.invoke import BuildCommandOptions
from ralph.agents.invoke._command_builders import (
    AgyCommandBuilder,
    CommandBuilderSpec,
    ConfigurableCommandBuilder,
)
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig

_PROMPT_TEXT = "phase prompt"


def _write_prompt(tmp_path: Path) -> str:
    prompt = tmp_path / "task_prompt.md"
    prompt.write_text(_PROMPT_TEXT, encoding="utf-8")
    return prompt.name


_FUTURE_BASE = CommandBuilderSpec(
    base_argv=("futureagent",),
    format_flag=None,
    output_flag="--output-format stream-json",
    yolo_flag="--dangerously-skip-permissions",
    model_flag_template=None,
    positional_prompt=True,
    print_flag="--print",
    extra_flags_before_prompt=("--print-timeout", "2h"),
    cmd_argv_override=True,
    yolo_before_session=True,
    workspace_dir_flag=("--add-dir",),
)


def _future_spec(**overrides: object) -> CommandBuilderSpec:
    return dataclasses.replace(_FUTURE_BASE, **overrides)


def test_cmd_argv_override_preserves_wrapper_tokens(tmp_path: Path) -> None:
    builder = ConfigurableCommandBuilder(_future_spec())
    argv = builder.build(
        AgentConfig(
            cmd="/opt/wrapper/futureagent --telemetry on", transport=AgentTransport.GENERIC
        ),
        _write_prompt(tmp_path),
        options=BuildCommandOptions(workspace_path=tmp_path),
    )
    assert argv[:3] == ["/opt/wrapper/futureagent", "--telemetry", "on"]


def test_without_cmd_argv_override_only_the_binary_token_is_used(tmp_path: Path) -> None:
    builder = ConfigurableCommandBuilder(_future_spec(cmd_argv_override=False))
    argv = builder.build(
        AgentConfig(
            cmd="/opt/wrapper/futureagent --telemetry on", transport=AgentTransport.GENERIC
        ),
        _write_prompt(tmp_path),
        options=BuildCommandOptions(workspace_path=tmp_path),
    )
    assert argv[0] == "/opt/wrapper/futureagent"
    assert "--telemetry" not in argv


def test_yolo_before_session_orders_yolo_first(tmp_path: Path) -> None:
    builder = ConfigurableCommandBuilder(_future_spec())
    argv = builder.build(
        AgentConfig(
            cmd="futureagent",
            transport=AgentTransport.GENERIC,
            session_flag="--session {}",
        ),
        _write_prompt(tmp_path),
        options=BuildCommandOptions(workspace_path=tmp_path, session_id="sess-1"),
    )
    assert argv.index("--dangerously-skip-permissions") < argv.index("--session")


def test_default_spec_orders_session_first(tmp_path: Path) -> None:
    builder = ConfigurableCommandBuilder(_future_spec(yolo_before_session=False))
    argv = builder.build(
        AgentConfig(
            cmd="futureagent",
            transport=AgentTransport.GENERIC,
            session_flag="--session {}",
        ),
        _write_prompt(tmp_path),
        options=BuildCommandOptions(workspace_path=tmp_path, session_id="sess-1"),
    )
    assert argv.index("--session") < argv.index("--dangerously-skip-permissions")


def test_workspace_dir_flag_appends_workspace_path(tmp_path: Path) -> None:
    builder = ConfigurableCommandBuilder(_future_spec())
    argv = builder.build(
        AgentConfig(cmd="futureagent", transport=AgentTransport.GENERIC),
        _write_prompt(tmp_path),
        options=BuildCommandOptions(workspace_path=tmp_path),
    )
    pair = ["--add-dir", str(tmp_path)]
    for index in range(len(argv) - 1):
        if argv[index : index + 2] == pair:
            break
    else:
        raise AssertionError(f"{pair} not found consecutively in {argv}")


def test_workspace_flag_absent_without_spec_field(tmp_path: Path) -> None:
    builder = ConfigurableCommandBuilder(_future_spec(workspace_dir_flag=None))
    argv = builder.build(
        AgentConfig(cmd="futureagent", transport=AgentTransport.GENERIC),
        _write_prompt(tmp_path),
        options=BuildCommandOptions(workspace_path=tmp_path),
    )
    assert "--add-dir" not in argv


def test_print_flag_precedes_positional_prompt(tmp_path: Path) -> None:
    builder = ConfigurableCommandBuilder(_future_spec())
    argv = builder.build(
        AgentConfig(cmd="futureagent", transport=AgentTransport.GENERIC),
        _write_prompt(tmp_path),
        options=BuildCommandOptions(workspace_path=tmp_path),
    )
    assert argv[-2:] == ["--print", _PROMPT_TEXT]


def test_future_agent_argv_matches_agy_builder_modulo_command(tmp_path: Path) -> None:
    future_argv = ConfigurableCommandBuilder(_future_spec()).build(
        AgentConfig(cmd="futureagent", transport=AgentTransport.GENERIC),
        _write_prompt(tmp_path),
        options=BuildCommandOptions(workspace_path=tmp_path),
    )
    agy_argv = AgyCommandBuilder().build(
        AgentConfig(cmd="agy", transport=AgentTransport.AGY),
        _write_prompt(tmp_path),
        options=BuildCommandOptions(workspace_path=tmp_path),
    )
    assert future_argv[1:] == agy_argv[1:]
    assert agy_argv[0] == "agy"
    assert future_argv[0] == "futureagent"
