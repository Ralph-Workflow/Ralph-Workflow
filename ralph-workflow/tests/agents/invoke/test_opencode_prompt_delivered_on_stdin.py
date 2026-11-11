from __future__ import annotations

from pathlib import Path

from ralph.agents.invoke import BuildCommandOptions, build_command
from ralph.agents.invoke._process_reader import split_prompt_for_stdin_delivery
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig

_PROMPT = 'Submit the artifact.\nExample: {"type": "plan"}\n'


def _opencode_config() -> AgentConfig:
    return AgentConfig(
        cmd="opencode",
        transport=AgentTransport.OPENCODE,
        session_flag="--session {}",
    )


def test_opencode_regression_prompt_moves_off_argv_onto_stdin() -> None:
    argv = ["opencode", "run", "--format", "json", _PROMPT]

    command, stdin_text = split_prompt_for_stdin_delivery(argv, _opencode_config())

    assert command == ["opencode", "run", "--format", "json"]
    assert stdin_text == _PROMPT


def test_codex_regression_oversized_prompt_moves_off_argv_onto_stdin() -> None:
    prompt = _PROMPT * 16_384
    config = AgentConfig(cmd="codex exec", transport=AgentTransport.CODEX)

    for argv in (
        ["codex", "exec", "--json", prompt],
        ["codex", "exec", "resume", "session-1", prompt],
    ):
        command, stdin_text = split_prompt_for_stdin_delivery(argv, config)

        assert command == [*argv[:-1], "-"]
        assert prompt not in command
        assert stdin_text == prompt


def test_opencode_regression_other_transports_keep_their_positional_prompt() -> None:
    argv = ["claude", "--print", _PROMPT]
    config = AgentConfig(cmd="claude --print", transport=AgentTransport.CLAUDE)

    command, stdin_text = split_prompt_for_stdin_delivery(argv, config)

    assert command == argv
    assert stdin_text is None


def test_opencode_regression_the_builder_really_puts_the_prompt_last(tmp_path: Path) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text(_PROMPT, encoding="utf-8")

    argv = build_command(
        _opencode_config(),
        str(prompt_file),
        options=BuildCommandOptions(session_id="ses_1", workspace_path=tmp_path),
    )

    assert argv[-1].endswith(_PROMPT)
    assert "ses_1" in argv[:-1]


def test_opencode_regression_unattended_run_auto_approves_permissions() -> None:
    argv = build_command(
        _opencode_config(),
        "PROMPT.md",
        options=BuildCommandOptions(),
    )

    assert "--auto" in argv
