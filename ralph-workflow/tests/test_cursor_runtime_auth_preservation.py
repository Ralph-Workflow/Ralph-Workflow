"""Regression coverage for Cursor credentials under Ralph's private HOME."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from pytest import mark

from ralph.agents.invoke._runtime_resolvers import CursorRuntimeResolver
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.mcp.protocol.env import MCP_ENDPOINT_ENV
from ralph.mcp.transport import cursor as cursor_transport

if TYPE_CHECKING:
    import pytest


@mark.parametrize("use_explicit_xdg", [False, True])
def test_cursor_runtime_regression_projects_xdg_auth_into_private_config_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, use_explicit_xdg: bool
) -> None:
    """Regression: Cursor auth lives at XDG_CONFIG_HOME/cursor/auth.json, not ~/.cursor."""
    operator_home = tmp_path / "operator-home"
    source_config_home = (
        tmp_path / "operator-xdg-config" if use_explicit_xdg else operator_home / ".config"
    )
    credentials = source_config_home / "cursor" / "auth.json"
    credentials.parent.mkdir(parents=True)
    credentials.write_text('{"token":"operator-token"}', encoding="utf-8")
    mutable_sibling = source_config_home / "cursor" / "state.vscdb"
    mutable_sibling.write_text("mutable operator state", encoding="utf-8")
    operator_mcp = operator_home / ".cursor" / "mcp.json"
    operator_mcp.parent.mkdir(parents=True)
    operator_mcp.write_text('{"mcpServers":{"operator":{}}}', encoding="utf-8")
    monkeypatch.setenv("HOME", str(operator_home))
    if use_explicit_xdg:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(source_config_home))
    else:
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    endpoint = "http://127.0.0.1:9999/mcp"
    runtime = CursorRuntimeResolver().resolve(
        AgentConfig(cmd="agent", transport=AgentTransport.CURSOR),
        extra_env={str(MCP_ENDPOINT_ENV): endpoint},
        workspace_path=tmp_path,
    )

    try:
        assert runtime.agent_env is not None
        private_home = Path(runtime.agent_env["HOME"])
        private_config_home = Path(runtime.agent_env["XDG_CONFIG_HOME"])
        assert private_config_home != source_config_home
        assert private_config_home.parent == private_home
        assert (private_config_home / "cursor" / "auth.json").read_text(
            encoding="utf-8"
        ) == credentials.read_text(encoding="utf-8")
        assert not (private_config_home / "cursor" / mutable_sibling.name).exists()
        generated_mcp: dict[str, object] = json.loads(
            (private_home / ".cursor" / "mcp.json").read_text(encoding="utf-8")
        )
        mcp_servers = generated_mcp["mcpServers"]
        assert isinstance(mcp_servers, dict)
        ralph_server = mcp_servers["ralph"]
        assert isinstance(ralph_server, dict)
        ralph_url = ralph_server["url"]
        assert isinstance(ralph_url, str)
        assert ralph_url == endpoint
        assert operator_mcp.read_text(encoding="utf-8") == '{"mcpServers":{"operator":{}}}'
    finally:
        assert runtime.cleanup is not None
        runtime.cleanup()


def test_cursor_runtime_regression_defaults_to_file_store_without_api_key(
    tmp_path: Path,
) -> None:
    """S-4: Cursor runs avoid the macOS keychain unless an operator overrides the store."""
    runtime = CursorRuntimeResolver().resolve(
        AgentConfig(cmd="agent", transport=AgentTransport.CURSOR),
        extra_env={str(MCP_ENDPOINT_ENV): "http://127.0.0.1:9999/mcp"},
        workspace_path=tmp_path,
        base_env={"HOME": str(tmp_path / "operator-home")},
    )

    try:
        assert runtime.agent_env is not None
        assert runtime.agent_env["AGENT_CLI_CREDENTIAL_STORE"] == "file"
        assert "CURSOR_API_KEY" not in runtime.agent_env
    finally:
        assert runtime.cleanup is not None
        runtime.cleanup()


@mark.parametrize(
    ("extra_env", "base_env", "expected_store", "expected_api_key"),
    [
        (
            {str(MCP_ENDPOINT_ENV): "http://127.0.0.1:9999/mcp"},
            {
                "HOME": "/operator-home",
                "AGENT_CLI_CREDENTIAL_STORE": "memory",
                "CURSOR_API_KEY": "ambient-key",
            },
            "memory",
            "ambient-key",
        ),
        (
            {
                str(MCP_ENDPOINT_ENV): "http://127.0.0.1:9999/mcp",
                "AGENT_CLI_CREDENTIAL_STORE": "default",
                "CURSOR_API_KEY": "invocation-key",
            },
            {
                "HOME": "/operator-home",
                "AGENT_CLI_CREDENTIAL_STORE": "memory",
                "CURSOR_API_KEY": "ambient-key",
            },
            "default",
            "invocation-key",
        ),
    ],
)
def test_cursor_runtime_regression_preserves_credential_override_precedence(
    tmp_path: Path,
    extra_env: dict[str, str],
    base_env: dict[str, str],
    expected_store: str,
    expected_api_key: str,
) -> None:
    """S-4: invocation credentials override ambient values, which override defaults."""
    runtime = CursorRuntimeResolver().resolve(
        AgentConfig(cmd="agent", transport=AgentTransport.CURSOR),
        extra_env=extra_env,
        workspace_path=tmp_path,
        base_env=base_env,
    )

    try:
        assert runtime.agent_env is not None
        assert runtime.agent_env["AGENT_CLI_CREDENTIAL_STORE"] == expected_store
        assert runtime.agent_env["CURSOR_API_KEY"] == expected_api_key
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
