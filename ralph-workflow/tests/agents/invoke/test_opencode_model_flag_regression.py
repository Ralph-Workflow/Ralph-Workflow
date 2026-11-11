from __future__ import annotations

from pathlib import Path

import pytest

from ralph.agents.invoke import BuildCommandOptions, build_command
from ralph.agents.registry import AgentRegistry
from ralph.api.opencode import opencode_model_id_from_flag
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig


def _argv_for_alias(alias: str, tmp_path: Path) -> list[str]:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("do the thing", encoding="utf-8")
    config = AgentRegistry().get(alias)
    assert config is not None, f"registry did not resolve {alias!r}"
    return build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(workspace_path=tmp_path),
    )


def _model_value(argv: list[str]) -> str:
    assert "-m" in argv, argv
    return argv[argv.index("-m") + 1]


def test_opencode_regression_provider_named_opencode_stays_selectable(
    tmp_path: Path,
) -> None:
    argv = _argv_for_alias("opencode/opencode/big-pickle", tmp_path)

    assert _model_value(argv) == "opencode/big-pickle"


def test_opencode_regression_third_party_provider_alias_keeps_both_halves(
    tmp_path: Path,
) -> None:
    argv = _argv_for_alias("opencode/anthropic/claude-sonnet-4-5", tmp_path)

    assert _model_value(argv) == "anthropic/claude-sonnet-4-5"


def test_opencode_regression_model_flag_cannot_smuggle_extra_argv_tokens(
    tmp_path: Path,
) -> None:
    argv = _argv_for_alias("opencode/minimax/MiniMax-M3 --agent plan", tmp_path)

    assert _model_value(argv) == "minimax/MiniMax-M3 --agent plan"
    assert "--agent" not in argv
    assert "plan" not in argv


def test_opencode_regression_preflight_model_id_keeps_provider_prefix() -> None:
    assert opencode_model_id_from_flag("-m opencode/big-pickle") == "opencode/big-pickle"
    assert opencode_model_id_from_flag("--model anthropic/claude-sonnet-4-5") == (
        "anthropic/claude-sonnet-4-5"
    )
    assert opencode_model_id_from_flag("opencode/big-pickle") == "opencode/big-pickle"
    assert opencode_model_id_from_flag(None) is None
    assert opencode_model_id_from_flag("") is None


def test_opencode_regression_model_flag_rejects_a_second_flag_as_its_value(
    tmp_path: Path,
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("do the thing", encoding="utf-8")
    config = AgentConfig(cmd="opencode", transport=AgentTransport.OPENCODE)

    with pytest.raises(ValueError, match="flag-injection"):
        build_command(
            config,
            str(prompt_file),
            options=BuildCommandOptions(
                model_flag="-m --agent",
                workspace_path=tmp_path,
            ),
        )
