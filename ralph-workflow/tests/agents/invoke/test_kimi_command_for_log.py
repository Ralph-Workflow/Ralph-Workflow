from __future__ import annotations

from pathlib import Path

from ralph.agents.invoke._commands import _build_command, _command_for_log
from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport

_PLACEHOLDER = "/tmp/PROMPT.md"
_REAL_PROMPT = "/real/workspace/.ralph/prompt-secret-run.md"


def _kimi_config() -> AgentConfig:
    return AgentConfig(
        cmd="kimi",
        output_flag="--output-format=stream-json",
        print_flag="-p",
        session_flag="-S {}",
        transport=AgentTransport.KIMI,
    )


def test_command_for_log_replaces_trailing_prompt_for_kimi() -> None:
    config = _kimi_config()
    cmd = ["kimi", "--output-format=stream-json", "-p", _REAL_PROMPT]

    logged = _command_for_log(config, cmd, _PLACEHOLDER)

    assert logged == f"kimi --output-format=stream-json -p {_PLACEHOLDER}"
    assert _REAL_PROMPT not in logged


def test_command_for_log_redacts_the_built_kimi_argv(tmp_path: Path) -> None:
    prompt_file = tmp_path / "prompt-secret-run.md"
    prompt_file.write_text("do the work\n", encoding="utf-8")
    config = _kimi_config()
    cmd = _build_command(config, str(prompt_file))

    assert cmd[-1] == "do the work\n"

    logged = _command_for_log(config, cmd, _PLACEHOLDER)

    assert "do the work" not in logged
    assert logged.endswith(_PLACEHOLDER)


def test_command_for_log_replaces_trailing_path_for_opencode_regression() -> None:
    config = AgentConfig(cmd="opencode", transport=AgentTransport.OPENCODE)
    cmd = ["opencode", "run", _REAL_PROMPT]

    logged = _command_for_log(config, cmd, _PLACEHOLDER)

    assert _REAL_PROMPT not in logged
    assert logged == f"opencode run {_PLACEHOLDER}"


def test_command_for_log_keeps_non_placeholder_tokens_for_kimi() -> None:
    config = _kimi_config()
    cmd = ["kimi", "--output-format=stream-json", "-S", "session-1", "-p", _REAL_PROMPT]

    logged = _command_for_log(config, cmd, _PLACEHOLDER)

    assert "-S session-1" in logged
    assert logged == f"kimi --output-format=stream-json -S session-1 -p {_PLACEHOLDER}"
