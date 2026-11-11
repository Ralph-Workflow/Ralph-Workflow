from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from ralph.agents.invoke import BuildCommandOptions
from ralph.agents.invoke._command_builders import (
    AgyCommandBuilder,
    CodexCommandBuilder,
    CommandBuilderSpec,
    CursorCommandBuilder,
    DefaultCommandBuilder,
    NanocoderCommandBuilder,
    OpencodeCommandBuilder,
)
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig


@pytest.mark.parametrize(
    ("builder_type", "transport", "command"),
    (
        (OpencodeCommandBuilder, AgentTransport.OPENCODE, "opencode"),
        (NanocoderCommandBuilder, AgentTransport.NANOCODER, "nanocoder"),
        pytest.param(
            AgyCommandBuilder,
            AgentTransport.AGY,
            "agy",
            marks=pytest.mark.smoke,
            id="AgyCommandBuilder-agy-agy",
        ),
        (CursorCommandBuilder, AgentTransport.CURSOR, "agent"),
    ),
)
def test_positional_transports_prepend_master_prompt(
    tmp_path: Path,
    builder_type: type[OpencodeCommandBuilder],
    transport: AgentTransport,
    command: str,
) -> None:
    prompt = tmp_path / "PROMPT.md"
    prompt.write_text("phase prompt", encoding="utf-8")
    master = tmp_path / "MASTER_PROMPT.md"
    master.write_text("durable rules", encoding="utf-8")

    cmd = builder_type().build(
        AgentConfig(cmd=command, transport=transport),
        str(prompt),
        options=BuildCommandOptions(
            workspace_path=tmp_path,
            master_prompt_file=master.name,
        ),
    )

    assert cmd[-1] == "durable rules\n\nphase prompt"


def test_generic_transport_preserves_path_argv_with_combined_master_and_phase_prompt(
    tmp_path: Path,
) -> None:
    prompt = tmp_path / "PROMPT.md"
    prompt.write_text("phase prompt", encoding="utf-8")
    master = tmp_path / "MASTER_PROMPT.md"
    master.write_text("durable rules\n", encoding="utf-8")

    builder = DefaultCommandBuilder()
    options = BuildCommandOptions(
        workspace_path=tmp_path,
        master_prompt_file=master.name,
    )
    cmd = builder.build(
        AgentConfig(cmd="custom-agent", transport=AgentTransport.GENERIC),
        str(prompt),
        options=options,
    )

    combined_prompt = Path(cmd[-1])
    assert cmd[0] == "custom-agent"
    assert combined_prompt.is_file()
    assert combined_prompt.read_text(encoding="utf-8") == "durable rules\n\nphase prompt"
    assert prompt.read_text(encoding="utf-8") == "phase prompt"

    rebuilt = builder.build(
        AgentConfig(cmd="custom-agent", transport=AgentTransport.GENERIC),
        str(prompt),
        options=options,
    )
    assert rebuilt == cmd


def test_spec_frozen_and_hashable() -> None:
    spec = CommandBuilderSpec(
        base_argv=("test",),
        format_flag=None,
        output_flag=None,
        yolo_flag=None,
        model_flag_template=None,
        positional_prompt=True,
        print_flag=None,
        extra_flags_before_prompt=(),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        attr = "base_argv"
        setattr(spec, attr, ("other",))

    assert hash(spec) is not None


def test_opencode_command_builder_parity(tmp_path: Path) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello world", encoding="utf-8")

    config = AgentConfig(
        cmd="opencode",
        session_flag="--session {}",
        yolo_flag="--skip-stuff",
        verbose_flag="--verbose",
        transport=AgentTransport.OPENCODE,
    )
    options = BuildCommandOptions(
        session_id="sess-123",
        verbose=True,
        model_flag="--model opencode/some-model",
        workspace_path=tmp_path,
    )

    expected = [
        "opencode",
        "run",
        "--format",
        "json",
        "--session",
        "sess-123",
        "--skip-stuff",
        "--verbose",
        "--model",
        "opencode/some-model",
        "hello world",
    ]

    builder = OpencodeCommandBuilder()
    cmd = builder.build(config, str(prompt_file), options=options)
    assert cmd == expected


def test_nanocoder_command_builder_parity(tmp_path: Path) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello world", encoding="utf-8")

    config = AgentConfig(
        cmd="nanocoder",
        transport=AgentTransport.NANOCODER,
    )
    options = BuildCommandOptions(
        model_flag="--provider openai --model gpt-4",
        workspace_path=tmp_path,
    )

    expected = [
        "nanocoder",
        "--mode",
        "yolo",
        "--provider",
        "openai",
        "--model",
        "gpt-4",
        "--no-plain",
        "run",
        "hello world",
    ]

    builder = NanocoderCommandBuilder()
    cmd = builder.build(config, str(prompt_file), options=options)
    assert cmd == expected


def test_codex_command_builder_parity(tmp_path: Path) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello world", encoding="utf-8")

    config = AgentConfig(
        cmd="codex exec",
        output_flag="--json",
        yolo_flag="--bypass",
        transport=AgentTransport.CODEX,
    )
    options = BuildCommandOptions(
        model_flag="--model gpt-4",
        workspace_path=tmp_path,
    )

    expected = [
        "codex",
        "exec",
        "--json",
        "--bypass",
        "--model",
        "gpt-4",
        "hello world",
    ]

    builder = CodexCommandBuilder()
    cmd = builder.build(config, str(prompt_file), options=options)
    assert cmd == expected


def test_codex_command_builder_keeps_quoted_command_path_as_one_argv_token(tmp_path: Path) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello world", encoding="utf-8")
    wrapper = tmp_path / "codex wrapper"

    cmd = CodexCommandBuilder().build(
        AgentConfig(cmd=f"'{wrapper}' exec", transport=AgentTransport.CODEX),
        str(prompt_file),
        options=BuildCommandOptions(workspace_path=tmp_path),
    )

    assert cmd[:2] == [str(wrapper), "exec"]


def test_codex_command_builder_preserves_quoted_effort_override(tmp_path: Path) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello world", encoding="utf-8")
    config = AgentConfig(
        cmd="codex exec",
        transport=AgentTransport.CODEX,
        model_flag="--model gpt-5.3-codex -c 'model_reasoning_effort = \"high\"'",
    )

    cmd = CodexCommandBuilder().build(
        config,
        str(prompt_file),
        options=BuildCommandOptions(workspace_path=tmp_path),
    )

    assert cmd == [
        "codex",
        "exec",
        "--json",
        "--dangerously-bypass-approvals-and-sandbox",
        "--model",
        "gpt-5.3-codex",
        "-c",
        'model_reasoning_effort = "high"',
        "hello world",
    ]


@pytest.mark.smoke
def test_agy_command_builder_parity(tmp_path: Path) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("hello world", encoding="utf-8")

    config = AgentConfig(
        cmd="agy",
        yolo_flag="--skip-perms",
        session_flag="--session {}",
        verbose_flag="--verbose",
        print_flag="--print",
        transport=AgentTransport.AGY,
    )
    options = BuildCommandOptions(
        session_id="sess-agy",
        workspace_path=tmp_path,
        verbose=True,
        model_flag="--model claude-3",
    )

    expected = [
        "agy",
        "--output-format",
        "stream-json",
        "--skip-perms",
        "--session",
        "sess-agy",
        "--add-dir",
        str(tmp_path),
        "--verbose",
        "--model",
        "claude-3",
        "--print-timeout",
        "2h",
        "--print",
        "hello world",
    ]

    builder = AgyCommandBuilder()
    cmd = builder.build(config, str(prompt_file), options=options)
    assert cmd == expected


class TestCursorCommandBuilder:
    def test_build_argv_with_print_stream_json_and_model(self, tmp_path: Path) -> None:
        prompt_file = tmp_path / "PROMPT.md"
        prompt_file.write_text("hello world", encoding="utf-8")

        config = AgentConfig(
            cmd="agent",
            output_flag="--output-format stream-json",
            yolo_flag="--yolo",
            session_flag="--resume {}",
            model_flag="--model gpt-5.3-codex-high",
            transport=AgentTransport.CURSOR,
        )
        options = BuildCommandOptions(
            session_id="sess-cursor",
            workspace_path=tmp_path,
        )

        builder = CursorCommandBuilder()
        cmd = builder.build(config, str(prompt_file), options=options)

        assert cmd[0] == "agent"
        assert "--print" in cmd
        assert "--output-format" in cmd
        assert "stream-json" in cmd
        assert "--trust" in cmd
        assert "--approve-mcps" in cmd
        assert "--yolo" in cmd
        assert "--model" in cmd
        assert "gpt-5.3-codex-high" in cmd
        assert "--resume" in cmd
        assert "sess-cursor" in cmd
        assert cmd[-1] == "hello world"

    def test_init_cmd_honors_config_cmd_override_with_flags(self, tmp_path: Path) -> None:
        prompt_file = tmp_path / "PROMPT.md"
        prompt_file.write_text("hello world", encoding="utf-8")

        config = AgentConfig(
            cmd="/opt/wrapper/agent --telemetry-flag",
            output_flag="--output-format stream-json",
            yolo_flag="--yolo",
            transport=AgentTransport.CURSOR,
        )
        options = BuildCommandOptions(workspace_path=tmp_path)

        builder = CursorCommandBuilder()
        cmd = builder.build(config, str(prompt_file), options=options)

        assert cmd[0] == "/opt/wrapper/agent"
        assert "--telemetry-flag" in cmd

    def test_init_cmd_default_path_when_no_override(self, tmp_path: Path) -> None:
        prompt_file = tmp_path / "PROMPT.md"
        prompt_file.write_text("hello world", encoding="utf-8")

        config = AgentConfig(
            cmd="agent",
            output_flag="--output-format stream-json",
            yolo_flag="--yolo",
            transport=AgentTransport.CURSOR,
        )
        options = BuildCommandOptions(workspace_path=tmp_path)

        builder = CursorCommandBuilder()
        cmd = builder.build(config, str(prompt_file), options=options)

        assert cmd[0] == "agent"
