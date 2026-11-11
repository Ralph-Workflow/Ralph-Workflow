from __future__ import annotations

from pathlib import Path

import pytest

from ralph.agents.invoke import (
    AgentInvocationError,
    BuildCommandOptions,
    build_command,
    provider_allowed_mcp_tool_names,
)
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.mcp.protocol.startup import PreflightError

_ENDPOINT = "http://127.0.0.1:9999/mcp"


def _claude_config() -> AgentConfig:
    return AgentConfig(
        cmd="claude -p",
        output_flag="--output-format=stream-json",
        transport=AgentTransport.CLAUDE,
    )


def _claude_argv(config: AgentConfig, workspace: Path) -> list[str]:
    prompt_file = workspace / "PROMPT.md"
    prompt_file.write_text("do the thing", encoding="utf-8")
    allowed = provider_allowed_mcp_tool_names(config, _ENDPOINT)
    return build_command(
        config,
        str(prompt_file),
        options=BuildCommandOptions(
            mcp_endpoint=_ENDPOINT,
            allowed_mcp_tool_names=allowed,
            workspace_path=workspace,
        ),
    )


def test_claude_regression_tool_restriction_fails_closed_when_discovery_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:

    def _raise(endpoint: str) -> list[str]:
        del endpoint
        raise PreflightError("HTTP MCP tools/list failed: connection reset by peer")

    monkeypatch.setattr("ralph.agents.invoke.discover_http_mcp_tool_names", _raise)

    with pytest.raises(AgentInvocationError) as excinfo:
        _claude_argv(_claude_config(), tmp_path)

    message = str(excinfo.value)
    assert "connection reset by peer" in message
    assert _ENDPOINT in message


def test_claude_mcp_permissions_do_not_restrict_native_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "ralph.agents.invoke.discover_http_mcp_tool_names",
        lambda _endpoint: ["read_file", "ralph_submit_md_artifact"],
    )

    argv = _claude_argv(_claude_config(), tmp_path)

    assert "--allowedTools" in argv
    assert "--tools" not in argv


def test_claude_regression_operator_mcp_servers_are_not_stripped(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "ralph.agents.invoke.discover_http_mcp_tool_names",
        lambda _endpoint: ["read_file", "ralph_submit_md_artifact"],
    )

    argv = _claude_argv(_claude_config(), tmp_path)

    assert "--strict-mcp-config" not in argv
    assert "--mcp-config" in argv
