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


def test_concurrent_cursor_resolves_isolate_mcp_config_and_share_operator_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operator_home = tmp_path / "operator-home"
    operator_cursor = operator_home / ".cursor"
    operator_cursor.mkdir(parents=True)
    (operator_cursor / "auth.json").write_text("credential", encoding="utf-8")
    volatile = operator_cursor / "volatile.json"
    volatile.write_text("transient", encoding="utf-8")
    barrier = Barrier(2)
    original_mirror = resolver_module._mirror_cursor_home

    def synchronized_mirror(source: Path, destination: Path) -> None:
        barrier.wait(timeout=0.5)
        volatile.unlink(missing_ok=True)
        original_mirror(source, destination)

    monkeypatch.setattr(resolver_module, "_mirror_cursor_home", synchronized_mirror)
    config = AgentConfig(cmd="agent", transport=AgentTransport.CURSOR)

    def resolve(endpoint: str) -> object:
        return CursorRuntimeResolver().resolve(
            config,
            extra_env={str(MCP_ENDPOINT_ENV): endpoint},
            workspace_path=tmp_path,
            base_env={"HOME": str(operator_home)},
        )

    endpoints = ("http://127.0.0.1:9101/mcp", "http://127.0.0.1:9102/mcp")
    with ThreadPoolExecutor(max_workers=2) as executor:
        runtimes = tuple(executor.map(resolve, endpoints))

    homes = tuple(Path(runtime.agent_env["HOME"]) for runtime in runtimes if runtime.agent_env)
    try:
        assert len(homes) == 2
        assert homes[0] != homes[1]
        for home, endpoint in zip(homes, endpoints, strict=True):
            private_cursor = home / ".cursor"
            assert (private_cursor / "auth.json").read_text(encoding="utf-8") == "credential"
            config_payload = json.loads((private_cursor / "mcp.json").read_text(encoding="utf-8"))
            assert config_payload["mcpServers"]["ralph"]["url"] == endpoint
        assert json.loads((homes[0] / ".cursor" / "mcp.json").read_text(encoding="utf-8")) != json.loads(
            (homes[1] / ".cursor" / "mcp.json").read_text(encoding="utf-8")
        )

        assert runtimes[0].cleanup is not None
        runtimes[0].cleanup()
        assert not homes[0].exists()
        assert homes[1].exists()
    finally:
        for runtime in runtimes:
            if runtime.cleanup is not None:
                runtime.cleanup()
