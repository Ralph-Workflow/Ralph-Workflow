"""Concurrent Cursor runtime resolution keeps private MCP homes isolated."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import TYPE_CHECKING

import ralph.agents.invoke._runtime_resolvers as resolver_module
from ralph.agents.invoke._runtime_resolvers import CursorRuntimeResolver
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.mcp.protocol.env import MCP_ENDPOINT_ENV

if TYPE_CHECKING:
    import pytest

    from ralph.agents.invoke._resolved_invocation_runtime import ResolvedInvocationRuntime


def test_concurrent_cursor_resolves_isolate_mcp_config_and_project_operator_xdg_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operator_home = tmp_path / "operator-home"
    operator_cursor = operator_home / ".cursor"
    operator_cursor.mkdir(parents=True)
    volatile = operator_cursor / "volatile.json"
    volatile.write_text("transient", encoding="utf-8")
    operator_xdg = tmp_path / "operator-xdg"
    operator_cursor_config = operator_xdg / "cursor"
    operator_cursor_config.mkdir(parents=True)
    operator_auth = operator_cursor_config / "auth.json"
    operator_auth.write_text("credential", encoding="utf-8")
    mutable_sibling = operator_cursor_config / "state.vscdb"
    mutable_sibling.write_text("transient state", encoding="utf-8")
    barrier = Barrier(2)
    original_mirror = resolver_module._mirror_cursor_home

    def synchronized_mirror(source: Path, destination: Path) -> None:
        barrier.wait(timeout=0.5)
        volatile.unlink(missing_ok=True)
        original_mirror(source, destination)

    monkeypatch.setattr(
        "ralph.agents.invoke._runtime_resolvers._mirror_cursor_home", synchronized_mirror
    )
    config = AgentConfig(cmd="agent", transport=AgentTransport.CURSOR)

    def resolve(endpoint: str) -> ResolvedInvocationRuntime:
        return CursorRuntimeResolver().resolve(
            config,
            extra_env={str(MCP_ENDPOINT_ENV): endpoint},
            workspace_path=tmp_path,
            base_env={
                "HOME": str(operator_home),
                "XDG_CONFIG_HOME": str(operator_xdg),
            },
        )

    endpoints = ("http://127.0.0.1:9101/mcp", "http://127.0.0.1:9102/mcp")
    with ThreadPoolExecutor(max_workers=2) as executor:
        runtimes = tuple(executor.map(resolve, endpoints))

    homes = tuple(Path(runtime.agent_env["HOME"]) for runtime in runtimes if runtime.agent_env)
    config_homes = tuple(
        Path(runtime.agent_env["XDG_CONFIG_HOME"]) for runtime in runtimes if runtime.agent_env
    )
    try:
        assert len(homes) == 2
        assert len(config_homes) == 2
        assert homes[0] != homes[1]
        assert config_homes[0] != config_homes[1]
        for home, config_home, endpoint in zip(homes, config_homes, endpoints, strict=True):
            private_cursor = home / ".cursor"
            private_cursor_config = config_home / "cursor"
            assert config_home.parent == home
            assert tuple(private_cursor.iterdir()) == (private_cursor / "mcp.json",)
            assert tuple(private_cursor_config.iterdir()) == (private_cursor_config / "auth.json",)
            assert (private_cursor_config / "auth.json").read_text(encoding="utf-8") == operator_auth.read_text(
                encoding="utf-8"
            )
            assert not (private_cursor_config / mutable_sibling.name).exists()
            config_payload: dict[str, dict[str, dict[str, str]]] = json.loads(
                (private_cursor / "mcp.json").read_text(encoding="utf-8")
            )
            assert config_payload["mcpServers"]["ralph"]["url"] == endpoint
        assert (homes[0] / ".cursor" / "mcp.json").read_text(encoding="utf-8") != (
            homes[1] / ".cursor" / "mcp.json"
        ).read_text(encoding="utf-8")

        assert runtimes[0].cleanup is not None
        runtimes[0].cleanup()
        assert not homes[0].exists()
        assert homes[1].exists()
    finally:
        for runtime in runtimes:
            if runtime.cleanup is not None:
                runtime.cleanup()
