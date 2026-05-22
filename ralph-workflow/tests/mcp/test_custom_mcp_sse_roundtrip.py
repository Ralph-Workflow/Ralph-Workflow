"""Round-trip integration test coverage for legacy HTTP+SSE upstream entries."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.config.enums import AgentTransport
from ralph.mcp.tools.names import custom_proxy_tool_name
from ralph.mcp.transport.common import mcp_toml_as_upstreams
from ralph.mcp.upstream.agent_probe import probe_agent_transports
from ralph.mcp.upstream.registry import UpstreamRegistry
from tests.fixtures.mcp_test_harness import FAKE_TOOL, SSE_CALL_RESULT, make_stub_client_factory

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

_FAKE_SSE_URL = "http://127.0.0.1:9999/sse"
_EXPECTED_TRANSPORT_COUNT = len(
    [AgentTransport.CLAUDE, AgentTransport.CODEX, AgentTransport.OPENCODE, AgentTransport.AGY]
)


def _write_mcp_toml(workspace: Path, server_name: str, url: str) -> None:
    agent_dir = workspace / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "mcp.toml").write_text(
        f'[mcp_servers.{server_name}]\ntransport = "http"\nurl = "{url}"\n',
        encoding="utf-8",
    )


def test_sse_entry_surfaces_in_upstream_registry(tmp_path: Path) -> None:
    _write_mcp_toml(tmp_path, "docs-sse", _FAKE_SSE_URL)

    upstreams = mcp_toml_as_upstreams(tmp_path)
    assert len(upstreams) == 1
    assert upstreams[0].name == "docs-sse"

    registry = UpstreamRegistry.build(
        upstreams, client_factory=make_stub_client_factory(call_result=SSE_CALL_RESULT)
    )
    aliases = {t.alias for t in registry.tool_definitions()}

    expected = custom_proxy_tool_name("docs-sse", FAKE_TOOL.name)
    assert expected in aliases


def test_sse_entry_probe_agent_transports_sees_server_as_reachable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    monkeypatch.setattr("ralph.mcp.upstream.agent_probe.http_handshake", lambda _endpoint: None)
    monkeypatch.setattr("ralph.mcp.upstream.agent_probe.server_handshake", lambda _server: None)

    _write_mcp_toml(tmp_path, "docs-sse", _FAKE_SSE_URL)

    reports = probe_agent_transports(
        mcp_toml_as_upstreams(tmp_path),
        transports=(
            AgentTransport.CLAUDE,
            AgentTransport.CODEX,
            AgentTransport.OPENCODE,
            AgentTransport.AGY,
        ),
        workspace_path=tmp_path,
    )

    assert len(reports) == _EXPECTED_TRANSPORT_COUNT
    for report in reports:
        assert report.ok is True, f"{report.transport}: {report.error}"


def test_sse_registry_call_tool_round_trip(tmp_path: Path) -> None:
    _write_mcp_toml(tmp_path, "docs-sse", _FAKE_SSE_URL)

    upstreams = mcp_toml_as_upstreams(tmp_path)
    registry = UpstreamRegistry.build(
        upstreams, client_factory=make_stub_client_factory(call_result=SSE_CALL_RESULT)
    )
    definitions = list(registry.tool_definitions())
    alias = custom_proxy_tool_name("docs-sse", FAKE_TOOL.name)
    assert any(item.alias == alias for item in definitions)

    result = registry.call_tool(alias, {})

    assert isinstance(result, dict)
    content = result.get("content")
    assert isinstance(content, list)
    first = content[0]
    assert isinstance(first, dict)
    assert first.get("text") == "fake-sse-result"
