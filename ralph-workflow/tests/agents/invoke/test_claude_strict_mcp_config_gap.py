"""Ralph must never strip an MCP server the operator installed.

Ralph used to hand Claude a ``--mcp-config`` holding only its own endpoint
plus ``--strict-mcp-config``, which per ``claude --help`` makes Claude "Only
use MCP servers from --mcp-config, ignoring all other MCP configurations".
The bargain was that Ralph discovers the operator's servers itself and
re-exposes them as ``ralph_upstream__*`` proxies -- nothing lost, just routed
through Ralph's capability gate.

That bargain cannot be kept for a server with no on-disk definition. A
claude.ai account connector is delivered by the signed-in account and appears
in no file under ``~/.claude``, ``~/.claude.json``, or ``~/.config``; the same
goes for a session-only ``--plugin-dir`` / ``--plugin-url`` plugin. Measured
on the maintainer's machine, ``claude mcp list`` reports four such connectors
(Notion, Gmail, Google Drive, Google Calendar) that Ralph could neither
discover nor proxy back -- they were simply gone for every run.

These tests used to pin a WARNING naming what the flag took away. Naming the
loss is not a substitute for not causing it, so the flag is gone instead and
they now pin the stronger contract: Ralph ADDS its MCP server via
``--mcp-config`` and takes nothing away. Without the strict flag Claude loads
Ralph's file in addition to its own sources.
"""

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
    """Both Claude transports add Ralph's MCP server without removing any other."""
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
    """Nothing is taken away, so nothing consults ``claude mcp list`` to say so.

    The old build path ran the Claude CLI once per run to name the servers
    ``--strict-mcp-config`` was about to delete, and logged a WARNING. With
    the flag gone that report would be false, so the call site is gone too:
    building the command must neither list the operator's servers nor warn
    about them.
    """
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("prompt", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    calls: list[int] = []

    def _counting_lister() -> tuple[str, ...]:
        calls.append(1)
        return ("claude.ai Notion",)

    monkeypatch.setattr(
        "ralph.mcp.transport.claude.claude_cli_mcp_server_lister", _counting_lister
    )

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
