"""Regression coverage for Cursor credentials under Ralph's private HOME."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.invoke._runtime_resolvers import CursorRuntimeResolver
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.mcp.protocol.env import MCP_ENDPOINT_ENV

if TYPE_CHECKING:
    import pytest


def test_cursor_runtime_preserves_operator_credentials_without_overwriting_mcp_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operator_home = tmp_path / "operator-home"
    operator_cursor = operator_home / ".cursor"
    operator_cursor.mkdir(parents=True)
    credentials = operator_cursor / "auth.json"
    credentials.write_text('{"token":"operator-token"}', encoding="utf-8")
    operator_mcp = operator_cursor / "mcp.json"
    operator_mcp.write_text('{"mcpServers":{"operator":{}}}', encoding="utf-8")
    monkeypatch.setenv("HOME", str(operator_home))

    endpoint = "http://127.0.0.1:9999/mcp"
    runtime = CursorRuntimeResolver().resolve(
        AgentConfig(cmd="agent", transport=AgentTransport.CURSOR),
        extra_env={str(MCP_ENDPOINT_ENV): endpoint},
        workspace_path=tmp_path,
    )

    try:
        assert runtime.agent_env is not None
        private_cursor = Path(runtime.agent_env["HOME"]) / ".cursor"
        assert (private_cursor / "auth.json").read_text(encoding="utf-8") == credentials.read_text(
            encoding="utf-8"
        )
        generated_mcp = json.loads((private_cursor / "mcp.json").read_text(encoding="utf-8"))
        assert generated_mcp["mcpServers"]["ralph"]["url"] == endpoint
        assert operator_mcp.read_text(encoding="utf-8") == '{"mcpServers":{"operator":{}}}'
    finally:
        assert runtime.cleanup is not None
        runtime.cleanup()
