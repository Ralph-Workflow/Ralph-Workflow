"""Round-trip integration test: a fresh mcp.toml entry surfaces to every agent."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from ralph.config.enums import AgentTransport
from ralph.mcp.tools.names import custom_proxy_tool_name
from ralph.mcp.transport.agy import agy_mcp_config
from ralph.mcp.transport.claude import claude_mcp_config
from ralph.mcp.transport.codex import prepare_codex_home_with_upstreams
from ralph.mcp.transport.common import mcp_toml_as_upstreams
from ralph.mcp.transport.opencode import build_opencode_provider_config
from ralph.mcp.upstream.agent_probe import probe_agent_transports
from ralph.mcp.upstream.config import UpstreamMcpServer
from ralph.mcp.upstream.registry import UpstreamRegistry
from tests.fixtures.mcp_test_harness import FAKE_TOOL, make_stub_client_factory

if TYPE_CHECKING:
    import pytest

_FAKE_URL = "http://127.0.0.1:9999/mcp"
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


def test_mcp_toml_entry_surfaces_in_upstream_registry(
    tmp_path: Path,
) -> None:
    _write_mcp_toml(tmp_path, "angular-docs", _FAKE_URL)

    upstreams = mcp_toml_as_upstreams(tmp_path)
    assert len(upstreams) == 1
    assert upstreams[0].name == "angular-docs"

    registry = UpstreamRegistry.build(
        upstreams,
        client_factory=cast("Any", make_stub_client_factory()),
    )
    aliases = {t.alias for t in registry.tool_definitions()}

    expected = custom_proxy_tool_name("angular-docs", FAKE_TOOL.name)
    assert expected in aliases


def test_mcp_toml_entry_appears_in_claude_config(
    tmp_path: Path,
) -> None:
    _write_mcp_toml(tmp_path, "angular-docs", _FAKE_URL)

    ralph_endpoint = "http://127.0.0.1:9999/mcp"
    config_json = claude_mcp_config(ralph_endpoint, workspace_path=tmp_path)
    parsed = json.loads(config_json)
    assert "ralph" in parsed["mcpServers"]


def test_mcp_toml_entry_appears_in_codex_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    _write_mcp_toml(tmp_path, "angular-docs", _FAKE_URL)

    ralph_endpoint = "http://127.0.0.1:9999/mcp"
    codex_home, _resolved_upstreams = prepare_codex_home_with_upstreams(
        ralph_endpoint,
        workspace_path=tmp_path,
        existing_home=None,
        master_prompt_file=None,
    )
    config_path = Path(codex_home) / "config.toml"
    parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert "ralph" in parsed.get("mcp_servers", {})


def test_mcp_toml_entry_appears_in_opencode_config(
    tmp_path: Path,
) -> None:
    _write_mcp_toml(tmp_path, "angular-docs", _FAKE_URL)

    ralph_endpoint = "http://127.0.0.1:9999/mcp"
    config_json, _resolved_upstreams = build_opencode_provider_config(None, ralph_endpoint)
    parsed = json.loads(config_json)
    assert "ralph" in parsed.get("mcp", {})


def test_mcp_toml_entry_appears_in_agy_config() -> None:
    ralph_endpoint = "http://127.0.0.1:9999/mcp"
    config_json = agy_mcp_config(ralph_endpoint)
    parsed = json.loads(config_json)
    assert "ralph" in parsed["mcpServers"]
    assert parsed["mcpServers"]["ralph"]["serverUrl"] == ralph_endpoint


def test_probe_agent_transports_sees_server_as_reachable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    monkeypatch.setattr("ralph.mcp.upstream.agent_probe.http_handshake", lambda _endpoint: None)
    monkeypatch.setattr("ralph.mcp.upstream.agent_probe.server_handshake", lambda _server: None)

    server = UpstreamMcpServer(name="angular-docs", transport="http", url=_FAKE_URL)
    reports = probe_agent_transports(
        [server],
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
