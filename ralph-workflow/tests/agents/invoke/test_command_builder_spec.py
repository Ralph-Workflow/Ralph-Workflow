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

    # Old logic:
    # cmd = [cmd_name, "--mode", "yolo", "run"]
    # model -> "--provider", "openai", "--model", "gpt-4"
    # prompt -> "hello world"
    expected = [
        "nanocoder",
        "--mode",
        "yolo",
        "run",
        "--provider",
        "openai",
        "--model",
        "gpt-4",
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
