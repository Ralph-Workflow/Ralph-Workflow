"""`--strict-mcp-config` must never strip an MCP server behind the operator's back.

Ralph hands Claude a ``--mcp-config`` holding only its own endpoint and adds
``--strict-mcp-config``, which per ``claude --help`` makes Claude "Only use MCP
servers from --mcp-config, ignoring all other MCP configurations". The
documented bargain is that Ralph discovers the operator's servers itself and
re-exposes them as ``ralph_upstream__*`` proxies -- nothing lost, just routed
through Ralph's capability gate.

That bargain cannot be kept for a server with no on-disk definition. A
claude.ai account connector is delivered by the signed-in account and appears
in no file under ``~/.claude``, ``~/.claude.json``, or ``~/.config``; the same
goes for a session-only ``--plugin-dir`` / ``--plugin-url`` plugin. Those
servers are simply gone for the run, and Ralph used to say nothing at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.agents.invoke import BuildCommandOptions, build_command
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig
from ralph.mcp.transport.claude import reset_claude_mcp_proxy_report

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _claude_config() -> AgentConfig:
    return AgentConfig(
        cmd="claude -p",
        output_flag="--output-format=stream-json",
        yolo_flag="--dangerously-skip-permissions",
        print_flag="--print",
        streaming_flag="--include-partial-messages",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE,
    )


def _lister_returning(names: tuple[str, ...]) -> object:
    def _lister() -> tuple[str, ...]:
        return names

    return _lister


def test_command_builders_regression_strict_mcp_config_names_the_servers_it_strips(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Building the Claude command must report, by name, what it takes away."""
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("prompt", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "ralph.mcp.transport.claude.claude_cli_mcp_server_lister",
        _lister_returning(("claude.ai Google Drive", "claude.ai Notion")),
    )
    reset_claude_mcp_proxy_report()

    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    try:
        cmd = build_command(
            _claude_config(),
            str(prompt_file),
            options=BuildCommandOptions(
                mcp_endpoint="http://127.0.0.1:9999/mcp",
                workspace_path=tmp_path,
            ),
        )
    finally:
        logger.remove(sink_id)

    assert "--strict-mcp-config" in cmd
    warning = "\n".join(records)
    assert "claude.ai Google Drive" in warning
    assert "claude.ai Notion" in warning


def test_command_builders_regression_strict_mcp_config_report_is_not_per_invocation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The Claude CLI is consulted once per run, not once per agent cycle."""
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
    reset_claude_mcp_proxy_report()

    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    try:
        for _ in range(4):
            build_command(
                _claude_config(),
                str(prompt_file),
                options=BuildCommandOptions(
                    mcp_endpoint="http://127.0.0.1:9999/mcp",
                    workspace_path=tmp_path,
                ),
            )
    finally:
        logger.remove(sink_id)

    assert len(calls) == 1
    assert len(records) == 1
