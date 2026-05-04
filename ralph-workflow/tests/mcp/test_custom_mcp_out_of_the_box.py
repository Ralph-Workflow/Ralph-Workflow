"""Regression test: a fresh .agent/mcp.toml server entry works out of the box.

This test verifies that adding a single server block to .agent/mcp.toml is
sufficient to expose it through the UpstreamRegistry without any extra setup.
"""
from __future__ import annotations

import json
from pathlib import Path

from ralph.mcp.tools.names import upstream_proxy_tool_name
from ralph.mcp.transport.claude import claude_mcp_config
from ralph.mcp.transport.codex import prepare_codex_home_with_upstreams
from ralph.mcp.transport.common import mcp_toml_as_upstreams
from ralph.mcp.transport.opencode import build_opencode_provider_config
from ralph.mcp.upstream.registry import UpstreamRegistry
from tests.fixtures.mcp_test_harness import FAKE_TOOL, make_stub_client_factory

_FAKE_URL = "http://127.0.0.1:9999/mcp"


def _write_mcp_toml(workspace: Path, server_name: str, url: str) -> None:
    agent_dir = workspace / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "mcp.toml").write_text(
        f'[mcp_servers.{server_name}]\ntransport = "http"\nurl = "{url}"\n',
        encoding="utf-8",
    )


def test_single_server_entry_surfaces_in_upstream_registry(
    tmp_path: Path,
) -> None:
    """Adding ONE server block to .agent/mcp.toml appears in mcp_toml_as_upstreams."""
    _write_mcp_toml(tmp_path, "my-custom-server", _FAKE_URL)

    upstreams = mcp_toml_as_upstreams(tmp_path)

    assert len(upstreams) == 1
    assert upstreams[0].name == "my-custom-server"


def test_single_server_entry_produces_non_empty_tool_definitions(
    tmp_path: Path,
) -> None:
    """UpstreamRegistry built from the upstreams has non-empty tool_definitions."""
    _write_mcp_toml(tmp_path, "my-custom-server", _FAKE_URL)

    upstreams = mcp_toml_as_upstreams(tmp_path)
    registry = UpstreamRegistry.build(upstreams, client_factory=make_stub_client_factory())
    tool_defs = registry.tool_definitions()

    assert len(tool_defs) > 0
    expected_alias = upstream_proxy_tool_name("my-custom-server", FAKE_TOOL.name)
    aliases = {t.alias for t in tool_defs}
    assert expected_alias in aliases, (
        f"Expected alias {expected_alias!r} not found in {aliases}"
    )


def test_single_server_entry_appears_in_ralph_transport_configs(
    tmp_path: Path,
) -> None:
    """The Ralph transport configs include the custom server via UpstreamRegistry."""
    _write_mcp_toml(tmp_path, "my-custom-server", _FAKE_URL)

    upstreams = mcp_toml_as_upstreams(tmp_path)
    registry = UpstreamRegistry.build(upstreams, client_factory=make_stub_client_factory())
    tool_defs = registry.tool_definitions()

    ralph_endpoint = "http://127.0.0.1:9999/mcp"
    config_json = claude_mcp_config(ralph_endpoint, workspace_path=tmp_path)
    parsed = json.loads(config_json)
    assert "mcpServers" in parsed
    assert "ralph" in parsed["mcpServers"]

    custom_alias = upstream_proxy_tool_name("my-custom-server", FAKE_TOOL.name)
    registry_aliases = {t.alias for t in tool_defs}
    assert custom_alias in registry_aliases, (
        f"Custom server tool {custom_alias!r} not in registry: {registry_aliases}"
    )


def test_single_server_entry_appears_in_codex_transport_config(
    tmp_path: Path,
) -> None:
    """prepare_codex_home_with_upstreams creates a codex home for the workspace."""
    isolated_codex_home = tmp_path / "empty-codex-home"
    isolated_codex_home.mkdir()

    _write_mcp_toml(tmp_path, "my-custom-server", _FAKE_URL)

    codex_home, existing_upstreams = prepare_codex_home_with_upstreams(
        None,
        workspace_path=tmp_path,
        existing_home=str(isolated_codex_home),
        system_prompt_file=None,
    )

    assert Path(codex_home).exists()
    assert (Path(codex_home) / "config.toml").exists()
    assert len(existing_upstreams) == 0


def test_single_server_entry_appears_in_opencode_transport_config(
    tmp_path: Path,
) -> None:
    """build_opencode_provider_config includes the ralph server in the OpenCode config."""
    _write_mcp_toml(tmp_path, "my-custom-server", _FAKE_URL)

    config_json, existing_upstreams = build_opencode_provider_config(
        None,
        "http://127.0.0.1:9999/mcp",
    )

    parsed = json.loads(config_json)
    assert "mcp" in parsed
    assert "ralph" in parsed["mcp"]
    assert len(existing_upstreams) == 0


def test_single_server_entry_surfaces_in_all_transport_paths(
    tmp_path: Path,
) -> None:
    """Full integration: single server in mcp.toml appears across all transport configs."""
    isolated_codex_home = tmp_path / "empty-codex-home"
    isolated_codex_home.mkdir()

    _write_mcp_toml(tmp_path, "my-custom-server", _FAKE_URL)

    upstreams = mcp_toml_as_upstreams(tmp_path)
    assert len(upstreams) == 1

    registry = UpstreamRegistry.build(upstreams, client_factory=make_stub_client_factory())
    tool_defs = registry.tool_definitions()
    custom_alias = upstream_proxy_tool_name("my-custom-server", FAKE_TOOL.name)
    registry_aliases = {t.alias for t in tool_defs}

    ralph_endpoint = "http://127.0.0.1:9999/mcp"
    claude_config = json.loads(claude_mcp_config(ralph_endpoint, workspace_path=tmp_path))
    assert "ralph" in claude_config.get("mcpServers", {})

    _codex_home, codex_upstreams = prepare_codex_home_with_upstreams(
        None,
        workspace_path=tmp_path,
        existing_home=str(isolated_codex_home),
        system_prompt_file=None,
    )
    assert len(codex_upstreams) == 0

    opencode_config_json, _ = build_opencode_provider_config(None, ralph_endpoint)
    opencode_config = json.loads(opencode_config_json)
    assert "mcp" in opencode_config
    assert "ralph" in opencode_config["mcp"]

    assert custom_alias in registry_aliases
