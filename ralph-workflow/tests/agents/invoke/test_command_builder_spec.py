"""Tests for CommandBuilderSpec and ConfigurableCommandBuilder."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

import pytest

from ralph.agents.invoke import BuildCommandOptions
from ralph.agents.invoke._command_builders import (
    AgyCommandBuilder,
    CodexCommandBuilder,
    CommandBuilderSpec,
    CursorCommandBuilder,
    NanocoderCommandBuilder,
    OpencodeCommandBuilder,
)
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig

if TYPE_CHECKING:
    from pathlib import Path


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
    # Check that it's frozen
    with pytest.raises(dataclasses.FrozenInstanceError):
        attr = "base_argv"
        setattr(spec, attr, ("other",))

    # Check that it's hashable
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
        pure=True,
        session_id="sess-123",
        verbose=True,
        model_flag="--model opencode/some-model",
        workspace_path=tmp_path,
    )

    # Old logic:
    # cmd = [cmd_name, "run"]
    # pure -> "--pure"
    # "--format", "json"
    # session -> "--session", "sess-123"
    # yolo -> "--skip-stuff"
    # verbose -> "--verbose"
    # model -> "--model", "some-model" (normalized)
    # prompt -> "hello world" (content of PROMPT.md)
    expected = [
        "opencode",
        "run",
        "--pure",
        "--format",
        "json",
        "--session",
        "sess-123",
        "--skip-stuff",
        "--verbose",
        "--model",
        "some-model",
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

    # Nanocoder uses the PTY-backed Ink runtime; Ralph must not drive the TUI editor.
    # cmd = [cmd_name]
    # yolo -> "--mode", "yolo"
    # model -> "--provider", "openai", "--model", "gpt-4"
    # run -> prompt content
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

    # Old logic:
    # cmd = ["codex", "exec"]
    # output -> "--json"
    # yolo -> "--bypass"
    # model -> "--model", "gpt-4"
    # prompt -> "hello world"
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

    # Old logic:
    # cmd = ["agy"]
    # yolo -> "--skip-perms"
    # session -> "--session", "sess-agy"
    # workspace -> "--add-dir", str(tmp_path)
    # verbose -> "--verbose"
    # model -> "--model", "claude-3"
    # print -> "--print"
    # prompt -> "hello world"
    expected = [
        "agy",
        "--skip-perms",
        "--session",
        "sess-agy",
        "--add-dir",
        str(tmp_path),
        "--verbose",
        "--model",
        "claude-3",
        "--print",
        "hello world",
    ]

    builder = AgyCommandBuilder()
    cmd = builder.build(config, str(prompt_file), options=options)
    assert cmd == expected


class TestCursorCommandBuilder:
    """Pin the documented ``agent --print --output-format stream-json`` argv shape."""

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

        # Canonical binary is `agent` (the first argv token after the cmd
        # override is honored).
        assert cmd[0] == "agent"
        # ``--print`` is the headless print flag.
        assert "--print" in cmd
        # ``--output-format stream-json`` (output flag emitted as two tokens).
        assert "--output-format" in cmd
        assert "stream-json" in cmd
        # ``--trust`` and ``--approve-mcps`` are the documented
        # unattended-runner overrides that skip the interactive
        # workspace-trust and MCP-approval prompts.
        assert "--trust" in cmd
        assert "--approve-mcps" in cmd
        # ``--yolo`` is the documented autonomy flag.
        assert "--yolo" in cmd
        # ``--model`` emitted as a separate ``--model <value>`` pair.
        assert "--model" in cmd
        assert "gpt-5.3-codex-high" in cmd
        # Session flag (--resume) is emitted via the session flag template.
        assert "--resume" in cmd
        assert "sess-cursor" in cmd
        # Prompt is a positional argument at the end.
        assert cmd[-1] == "hello world"

    def test_init_cmd_honors_config_cmd_override_with_flags(
        self, tmp_path: Path
    ) -> None:
        """An operator-supplied ``[agents.cursor].cmd`` with extra wrapper flags
        (e.g. ``/opt/wrapper/agent --telemetry-flag``) MUST be honored verbatim.
        Mirrors the AGY override contract (see
        ``ConfigurableCommandBuilder._init_cmd`` and the agy branch).
        """
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

        # The full wrapper path is preserved (not stripped to ``agent``).
        assert cmd[0] == "/opt/wrapper/agent"
        # The wrapper's extra flag is preserved as a separate argv token.
        assert "--telemetry-flag" in cmd

    def test_init_cmd_default_path_when_no_override(self, tmp_path: Path) -> None:
        """Without a ``[agents.cursor].cmd`` override, the argv starts with
        the built-in canonical command ``agent``.
        """
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

        # Default fall-through starts with the canonical ``agent`` command.
        assert cmd[0] == "agent"
