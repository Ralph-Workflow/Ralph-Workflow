"""Concurrency regressions for native MCP transports with private config roots."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ralph.agents.invoke._runtime_resolvers import RUNTIME_RESOLVERS, ResolvedInvocationRuntime
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig

_NATIVE_TRANSPORTS = (AgentTransport.AGY, AgentTransport.CURSOR, AgentTransport.KIMI)
_ENDPOINTS = ("http://127.0.0.1:41001/mcp", "http://127.0.0.1:41002/mcp")


def _resolve(transport: AgentTransport, workspace: Path, endpoint: str) -> ResolvedInvocationRuntime:
    return RUNTIME_RESOLVERS[transport]().resolve(
        config=AgentConfig(cmd=transport.value.lower(), transport=transport),
        extra_env={"RALPH_MCP_ENDPOINT": endpoint, "RALPH_MCP_RUN_ID": endpoint.rsplit(":", 1)[-1]},
        workspace_path=workspace,
        base_env={},
    )


def _config_path(transport: AgentTransport, runtime: ResolvedInvocationRuntime) -> Path:
    env = runtime.agent_env
    assert env is not None
    match transport:
        case AgentTransport.AGY:
            return Path(env["HOME"]) / ".gemini" / "config" / "mcp_config.json"
        case AgentTransport.CURSOR:
            return Path(env["HOME"]) / ".cursor" / "mcp.json"
        case AgentTransport.KIMI:
            return Path(env["KIMI_CODE_HOME"]) / "mcp.json"
        case unexpected:
            raise AssertionError(f"unsupported native transport: {unexpected}")


@pytest.mark.parametrize("transport", _NATIVE_TRANSPORTS)
def test_overlapping_native_sessions_keep_distinct_private_endpoints(
    transport: AgentTransport, tmp_path: Path
) -> None:
    """Two simultaneously live runtimes never share or mutate operator configuration."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    operator_home = tmp_path / "operator-home"
    operator_home.mkdir()
    operator_marker = operator_home / "operator-config.json"
    operator_marker.write_text('{"owner":"operator"}', encoding="utf-8")

    runtimes = tuple(_resolve(transport, workspace, endpoint) for endpoint in _ENDPOINTS)
    try:
        paths = tuple(_config_path(transport, runtime) for runtime in runtimes)
        assert paths[0] != paths[1]
        for path, endpoint in zip(paths, _ENDPOINTS, strict=True):
            payload = json.loads(path.read_text(encoding="utf-8"))
            ralph_server = payload["mcpServers"]["ralph"]
            assert endpoint in ralph_server.values()
        assert operator_marker.read_text(encoding="utf-8") == '{"owner":"operator"}'
        assert not (workspace / ".cursor").exists()
        assert not (workspace / ".kimi-code").exists()
        assert not (workspace / ".agents").exists()
    finally:
        for runtime in runtimes:
            cleanup = runtime.cleanup
            assert cleanup is not None
            cleanup()
