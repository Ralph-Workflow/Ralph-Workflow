"""Regression coverage for AGY authentication under Ralph's private HOME."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.invoke._runtime_resolvers import AgyRuntimeResolver
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.mcp.protocol.env import MCP_ENDPOINT_ENV

if TYPE_CHECKING:
    import pytest


def test_agy_runtime_preserves_auth_and_generated_configs_in_private_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operator_home = tmp_path / "operator-home"
    auth_root = operator_home / ".gemini" / "antigravity-cli"
    auth_root.mkdir(parents=True)
    auth_artifacts = {
        auth_root / "antigravity-oauth-token": b"placeholder-oauth-token",
        auth_root / "settings.json": b'{"theme":"operator"}',
        auth_root / "installation_id": b"placeholder-installation-id",
    }
    for path, contents in auth_artifacts.items():
        path.write_bytes(contents)
    monkeypatch.setenv("HOME", str(operator_home))

    endpoint = "http://127.0.0.1:9999/mcp"
    runtime = AgyRuntimeResolver().resolve(
        AgentConfig(cmd="agy", transport=AgentTransport.AGY),
        extra_env={str(MCP_ENDPOINT_ENV): endpoint},
        workspace_path=tmp_path,
        base_env={"HOME": str(operator_home)},
    )

    try:
        assert runtime.agent_env is not None
        private_home = Path(runtime.agent_env["HOME"])
        assert private_home != operator_home
        assert runtime.agent_env["HOME"] != str(operator_home)

        for source_path, source_bytes in auth_artifacts.items():
            relative_path = source_path.relative_to(operator_home)
            assert (private_home / relative_path).read_bytes() == source_bytes

        for relative_path in (
            Path(".gemini/antigravity-cli/mcp_config.json"),
            Path(".gemini/config/mcp_config.json"),
        ):
            generated_config = json.loads((private_home / relative_path).read_text(encoding="utf-8"))
            assert generated_config["mcpServers"]["ralph"] == {"serverUrl": endpoint}

        for source_path, source_bytes in auth_artifacts.items():
            assert source_path.read_bytes() == source_bytes
    finally:
        assert runtime.cleanup is not None
        runtime.cleanup()

    assert not private_home.exists()
    for source_path, source_bytes in auth_artifacts.items():
        assert source_path.read_bytes() == source_bytes
