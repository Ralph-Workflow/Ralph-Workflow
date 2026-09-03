"""Pin the generic :class:`CommandBuilderSpec` seams behind AGY's flags.

The AGY-specific argv behaviour (multi-token ``config.cmd`` override,
yolo-before-session flag order, ``--add-dir`` workspace flag) is spec
data any future agent can declare — not an agent-name branch.  These
tests construct a *non-AGY* spec with each generic flag set to its AGY
value and assert the matching argv through ``builder.build``.
"""

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
    extra_flags_before_prompt=("--print-timeout", "1h"),
    cmd_argv_override=True,
    yolo_before_session=True,
    workspace_dir_flag=("--add-dir",),
)


def _future_spec(**overrides: object) -> CommandBuilderSpec:
    """AGY-shaped spec for a fictional ``futureagent`` binary."""
    return dataclasses.replace(_FUTURE_BASE, **overrides)


def test_cmd_argv_override_preserves_wrapper_tokens(tmp_path: Path) -> None:
    """``cmd_argv_override`` splits ``config.cmd`` into separate argv tokens."""
    builder = ConfigurableCommandBuilder(_future_spec())
    argv = builder.build(
        AgentConfig(cmd="/opt/wrapper/futureagent --telemetry on", transport=AgentTransport.GENERIC),
        _write_prompt(tmp_path),
        options=BuildCommandOptions(workspace_path=tmp_path),
    )
    assert argv[:3] == ["/opt/wrapper/futureagent", "--telemetry", "on"]


def test_without_cmd_argv_override_only_the_binary_token_is_used(tmp_path: Path) -> None:
    """The default spec keeps the binary-name behaviour: one token from ``cmd``."""
    builder = ConfigurableCommandBuilder(_future_spec(cmd_argv_override=False))
    argv = builder.build(
        AgentConfig(cmd="/opt/wrapper/futureagent --telemetry on", transport=AgentTransport.GENERIC),
        _write_prompt(tmp_path),
        options=BuildCommandOptions(workspace_path=tmp_path),
    )
    assert argv[0] == "/opt/wrapper/futureagent"
    assert "--telemetry" not in argv


def test_yolo_before_session_orders_yolo_first(tmp_path: Path) -> None:
    """``yolo_before_session`` emits the autonomy flag before the session flag."""
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
    """Without ``yolo_before_session`` the session flag comes first (historical order)."""
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
    """``workspace_dir_flag`` emits the flag pair with the workspace path."""
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
    """A spec without ``workspace_dir_flag`` never emits a workspace directory flag."""
    builder = ConfigurableCommandBuilder(_future_spec(workspace_dir_flag=None))
    argv = builder.build(
        AgentConfig(cmd="futureagent", transport=AgentTransport.GENERIC),
        _write_prompt(tmp_path),
        options=BuildCommandOptions(workspace_path=tmp_path),
    )
    assert "--add-dir" not in argv


def test_print_flag_precedes_positional_prompt(tmp_path: Path) -> None:
    """``print_flag`` is emitted immediately before the positional prompt text."""
    builder = ConfigurableCommandBuilder(_future_spec())
    argv = builder.build(
        AgentConfig(cmd="futureagent", transport=AgentTransport.GENERIC),
        _write_prompt(tmp_path),
        options=BuildCommandOptions(workspace_path=tmp_path),
    )
    assert argv[-2:] == ["--print", _PROMPT_TEXT]


def test_future_agent_argv_matches_agy_builder_modulo_command(tmp_path: Path) -> None:
    """The same spec data on a non-AGY name reproduces the AGY argv verbatim.

    This is the core anti-special-casing pin: AGY's argv is fully
    described by ``CommandBuilderSpec`` data, so an unrelated agent
    declaring the same flags gets the same command line (modulo the
    binary name).
    """
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
