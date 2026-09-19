"""Regression coverage for Cursor credentials under Ralph's private HOME."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.invoke._runtime_resolvers import CursorRuntimeResolver
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.mcp.protocol.env import MCP_ENDPOINT_ENV
from ralph.mcp.transport import cursor as cursor_transport

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


def test_cursor_home_mirror_skips_entry_that_vanishes_before_stat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    volatile = source / "volatile.json"
    volatile.write_text("credential", encoding="utf-8")
    original_is_dir = Path.is_dir

    def remove_before_stat(path: Path) -> bool:
        if path == volatile:
            path.unlink()
        return original_is_dir(path)

    monkeypatch.setattr(Path, "is_dir", remove_before_stat)

    cursor_transport._mirror_cursor_home(source, destination)

    assert not (destination / volatile.name).exists()


def test_cursor_home_mirror_excludes_mcp_config_and_ralph_sidecar_locks(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "mcp.json").write_text("{}", encoding="utf-8")
    (source / "mcp.json.ralph.lock").write_text("lock", encoding="utf-8")
    (source / "auth.json").write_text("credential", encoding="utf-8")

    cursor_transport._mirror_cursor_home(source, destination)

    assert not (destination / "mcp.json").exists()
    assert not (destination / "mcp.json.ralph.lock").exists()
    assert (destination / "auth.json").is_symlink()
