"""Regression tests: visit_url is enabled out-of-the-box for non-commit drains."""

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

# Commit-class drains are read-only and do not receive web capabilities.
_COMMIT_CLASS_DRAINS = (
    SessionDrain.DEVELOPMENT_COMMIT,
    SessionDrain.REVIEW_COMMIT,
    SessionDrain.COMMIT,
)


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


_NON_COMMIT_DRAINS = [d.value for d in SessionDrain if d not in _COMMIT_CLASS_DRAINS]


@pytest.mark.parametrize("drain", _NON_COMMIT_DRAINS)
def test_non_commit_drains_have_web_visit_in_defaults(drain: str) -> None:
    session_drain = SessionDrain(drain)
    caps = DEFAULT_CAPABILITIES.get(session_drain, ())
    assert Capability.WEB_VISIT in caps, (
        f"SessionDrain.{session_drain.name} is missing Capability.WEB_VISIT in DEFAULT_CAPABILITIES"
    )


@pytest.mark.parametrize("drain", [d.value for d in _COMMIT_CLASS_DRAINS])
def test_commit_drains_do_not_have_web_visit_in_defaults(drain: str) -> None:
    session_drain = SessionDrain(drain)
    caps = DEFAULT_CAPABILITIES.get(session_drain, ())
    assert Capability.WEB_VISIT not in caps, (
        f"SessionDrain.{session_drain.name} should not have Capability.WEB_VISIT "
        "in DEFAULT_CAPABILITIES (commit-class drains are web-restricted)"
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
