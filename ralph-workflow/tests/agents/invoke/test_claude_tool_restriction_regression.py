"""Regression coverage for additive Claude MCP permissions.

Ralph pre-approves its MCP tools without restricting Claude's native tools.
MCP discovery must still fail closed because an incomplete allow-list can
silently prevent access to required Ralph tools.
"""

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
    """Compose argv exactly the way ``invoke_agent`` does."""
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
    """A failed ``tools/list`` must abort the launch, not silently unrestrict."""

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
    """Ralph ADDS its MCP server; it must never remove the operator's.

    ``claude --help``: ``--strict-mcp-config  Only use MCP servers from
    --mcp-config, ignoring all other MCP configurations``. Ralph used to
    pass it, which deleted every MCP source the operator had configured for
    the run. Some of those cannot be given back at all: ``claude mcp list``
    on this machine reports four claude.ai ACCOUNT CONNECTORS (Notion,
    Gmail, Google Drive, Google Calendar) that exist in no config file
    anywhere -- they come from the signed-in account, so Ralph's discovery
    cannot see them and no proxy can restore them.

    Without the strict flag Claude loads Ralph's ``--mcp-config`` file IN
    ADDITION to its own sources, which is the intended posture: add ours,
    keep theirs. Ralph's additive ``--allowedTools`` approvals still apply.
    """
    monkeypatch.setattr(
        "ralph.agents.invoke.discover_http_mcp_tool_names",
        lambda _endpoint: ["read_file", "ralph_submit_md_artifact"],
    )

    argv = _claude_argv(_claude_config(), tmp_path)

    assert "--strict-mcp-config" not in argv
    assert "--mcp-config" in argv
