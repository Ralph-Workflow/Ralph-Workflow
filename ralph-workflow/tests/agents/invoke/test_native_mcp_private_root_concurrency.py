"""Concurrency regressions for native MCP transports with private config roots."""

from __future__ import annotations

import importlib.util
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
    transport: AgentTransport, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two simultaneously live runtimes never share or mutate operator configuration."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    operator_home = tmp_path / "operator-home"
    operator_home.mkdir()
    monkeypatch.setenv("HOME", str(operator_home))
    monkeypatch.setenv("KIMI_CODE_HOME", str(operator_home / ".kimi-code"))
    operator_paths = {
        AgentTransport.AGY: operator_home / ".gemini" / "antigravity-cli" / "mcp_config.json",
        AgentTransport.CURSOR: operator_home / ".cursor" / "mcp.json",
        AgentTransport.KIMI: operator_home / ".kimi-code" / "mcp.json",
    }
    operator_config = operator_paths[transport]
    operator_config.parent.mkdir(parents=True)
    operator_config.write_text('{"owner":"operator"}', encoding="utf-8")

    runtimes = tuple(_resolve(transport, workspace, endpoint) for endpoint in _ENDPOINTS)
    try:
        paths = tuple(_config_path(transport, runtime) for runtime in runtimes)
        assert paths[0] != paths[1]
        for path, endpoint in zip(paths, _ENDPOINTS, strict=True):
            payload = json.loads(path.read_text(encoding="utf-8"))
            ralph_server = payload["mcpServers"]["ralph"]
            assert endpoint in ralph_server.values()
        assert operator_config.read_text(encoding="utf-8") == '{"owner":"operator"}'
        assert not operator_config.with_name(f"{operator_config.name}.ralph.lock").exists()
        assert not tuple(operator_home.rglob("*.ralph.lock"))
        assert not (workspace / ".cursor").exists()
        assert not (workspace / ".kimi-code").exists()
        assert not (workspace / ".agents").exists()
    finally:
        for runtime in runtimes:
            cleanup = runtime.cleanup
            assert cleanup is not None
            cleanup()


def test_shared_config_overlay_module_is_absent() -> None:
    """The serialized operator-global overlay design cannot return unnoticed."""
    assert importlib.util.find_spec("ralph.mcp.transport.config_overlay") is None
