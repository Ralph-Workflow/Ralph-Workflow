"""Regression tests: visit_url is enabled out-of-the-box for all drains."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.config.mcp_loader import load_mcp_config
from ralph.mcp.protocol.capability_mapping import Capability, SessionDrain
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.tools.bridge import build_ralph_tool_registry
from ralph.mcp.tools.names import VISIT_URL_TOOL
from ralph.prompts.template_variables import DEFAULT_CAPABILITIES

if TYPE_CHECKING:
    from pathlib import Path


class _StubWorkspace:
    def absolute_path(self, path: str) -> str:
        return path


def _session_with_web_visit(drain: str) -> AgentSession:
    return AgentSession(
        session_id="test",
        run_id="run-1",
        drain=drain,
        capabilities={"WebVisit"},
    )


@pytest.mark.parametrize("drain", [d.value for d in SessionDrain])
def test_all_drains_have_web_visit_in_defaults(drain: str) -> None:
    session_drain = SessionDrain(drain)
    caps = DEFAULT_CAPABILITIES.get(session_drain, ())
    assert Capability.WEB_VISIT in caps, (
        f"SessionDrain.{session_drain.name} is missing Capability.WEB_VISIT in DEFAULT_CAPABILITIES"
    )


def test_default_mcp_config_enables_web_visit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    config = load_mcp_config(config_path=None)

    assert config.web_visit.enabled is True


def test_build_registry_includes_visit_url_tool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    config = load_mcp_config(config_path=None)
    session = _session_with_web_visit("development")
    bridge = build_ralph_tool_registry(session, _StubWorkspace(), mcp_config=config)

    tool_names = {t.name for t in bridge.list_definitions()}
    assert VISIT_URL_TOOL in tool_names


def test_web_visit_capability_approved_in_default_session() -> None:
    session = _session_with_web_visit("development")
    assert session.check_capability("WebVisit") == "approved"


def test_visit_url_not_listed_without_capability() -> None:
    session = AgentSession(
        session_id="test",
        run_id="run-1",
        drain="development",
        capabilities={"WorkspaceRead"},
    )
    from ralph.config.mcp_models import McpConfig  # noqa: PLC0415

    bridge = build_ralph_tool_registry(session, _StubWorkspace(), mcp_config=McpConfig())
    tool_names = {t.name for t in bridge.list_definitions()}
    assert VISIT_URL_TOOL not in tool_names
