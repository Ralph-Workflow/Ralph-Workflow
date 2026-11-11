from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from loguru import logger

from ralph.agents.invoke import BuildCommandOptions, build_command
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig

if TYPE_CHECKING:
    from pathlib import Path


def _claude_config(transport: AgentTransport) -> AgentConfig:
    return AgentConfig(
        cmd="claude -p",
        output_flag="--output-format=stream-json",
        yolo_flag="--dangerously-skip-permissions",
        print_flag="--print",
        streaming_flag="--include-partial-messages",
        json_parser=JsonParserType.CLAUDE,
        transport=transport,
    )


@pytest.mark.parametrize(
    "transport",
    (AgentTransport.CLAUDE, AgentTransport.CLAUDE_INTERACTIVE),
)
def test_command_builders_regression_claude_argv_never_strips_operator_mcp_servers(
    transport: AgentTransport,
    tmp_path: Path,
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("prompt", encoding="utf-8")

    cmd = build_command(
        _claude_config(transport),
        str(prompt_file),
        options=BuildCommandOptions(
            mcp_endpoint="http://127.0.0.1:9999/mcp",
            workspace_path=tmp_path,
        ),
    )

    assert "--mcp-config" in cmd
    assert "--strict-mcp-config" not in cmd


def test_command_builders_regression_building_claude_argv_does_not_shell_out(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("prompt", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    calls: list[int] = []

    def _counting_lister() -> tuple[str, ...]:
        calls.append(1)
        return ("claude.ai Notion",)

    monkeypatch.setattr("ralph.mcp.transport.claude.claude_cli_mcp_server_lister", _counting_lister)

    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    try:
        for _ in range(4):
            build_command(
                _claude_config(AgentTransport.CLAUDE),
                str(prompt_file),
                options=BuildCommandOptions(
                    mcp_endpoint="http://127.0.0.1:9999/mcp",
                    workspace_path=tmp_path,
                ),
            )
    finally:
        logger.remove(sink_id)

    assert calls == []
    assert records == []
