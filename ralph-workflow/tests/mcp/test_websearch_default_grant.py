"""Regression tests: web_search is enabled out-of-the-box with no user config."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.config.mcp_loader import load_mcp_config
from ralph.config.mcp_models import McpConfig
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.tools.bridge import build_ralph_tool_registry
from ralph.mcp.tools.names import WEB_SEARCH_TOOL

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


class _StubWorkspace:
    def absolute_path(self, path: str) -> str:
        return path


def _default_session() -> AgentSession:
    return AgentSession(
        session_id="test",
        run_id="run-1",
        drain="development",
        capabilities={"WebSearch", "WorkspaceRead"},
    )


def test_default_mcp_config_enables_web_search(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    config = load_mcp_config(config_path=None)

    assert config.web_search.enabled is True
    assert config.web_search.backend == "ddgs"


def test_build_registry_includes_web_search_tool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    config = load_mcp_config(config_path=None)
    session = _default_session()
    bridge = build_ralph_tool_registry(session, _StubWorkspace(), mcp_config=config)

    tool_names = {t.name for t in bridge.list_definitions()}
    assert WEB_SEARCH_TOOL in tool_names


def test_web_search_capability_approved_in_default_session() -> None:
    session = _default_session()
    assert session.check_capability("WebSearch") == "approved"


def test_web_search_tool_not_listed_without_capability() -> None:
    session = AgentSession(
        session_id="test",
        run_id="run-1",
        drain="development",
        capabilities={"WorkspaceRead"},
    )

    bridge = build_ralph_tool_registry(session, _StubWorkspace(), mcp_config=McpConfig())
    tool_names = {t.name for t in bridge.list_definitions()}
    assert WEB_SEARCH_TOOL not in tool_names
