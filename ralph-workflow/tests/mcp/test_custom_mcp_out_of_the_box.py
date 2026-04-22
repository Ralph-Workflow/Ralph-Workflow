"""Regression test: a fresh .agent/mcp.toml server entry works out of the box.

This test verifies that adding a single server block to .agent/mcp.toml is
sufficient to expose it through the UpstreamRegistry without any extra setup.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.tools.names import upstream_proxy_tool_name
from ralph.mcp.transport.claude import claude_mcp_config
from ralph.mcp.transport.codex import prepare_codex_home_with_upstreams
from ralph.mcp.transport.common import mcp_toml_as_upstreams
from ralph.mcp.transport.opencode import build_opencode_provider_config
from ralph.mcp.upstream.registry import UpstreamRegistry
from ralph.process.manager import ProcessTerminationError, get_process_manager

if TYPE_CHECKING:
    from collections.abc import Iterator

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
FAKE_HTTP_MCP = PACKAGE_ROOT / "tests" / "fixtures" / "fake_http_mcp.py"

pytestmark = pytest.mark.timeout_seconds(20)


@contextmanager
def _spawn_fake_http_mcp() -> Iterator[int]:
    handle = get_process_manager().spawn(
        [sys.executable, str(FAKE_HTTP_MCP)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        label="test:fake-http-mcp-ooto",
    )
    try:
        stdout = handle.stdout
        assert stdout is not None
        port_line = stdout.readline().strip()
        assert port_line, "fake_http_mcp did not print its port"
        yield int(port_line)
    finally:
        with contextlib.suppress(ProcessTerminationError):
            handle.terminate(grace_period_s=5.0)
        if handle.stdout is not None:
            with contextlib.suppress(Exception):
                handle.stdout.close()
        if handle.stderr is not None:
            with contextlib.suppress(Exception):
                handle.stderr.close()


def _write_mcp_toml(workspace: Path, server_name: str, url: str) -> Path:
    agent_dir = workspace / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    mcp_path = agent_dir / "mcp.toml"
    mcp_path.write_text(
        f'[mcp_servers.{server_name}]\ntransport = "http"\nurl = "{url}"\n',
        encoding="utf-8",
    )
    return mcp_path


def _wait_for_port(port: int, *, timeout: float = 5.0) -> None:
    import socket  # noqa: PLC0415

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except Exception:
            time.sleep(0.01)
    raise AssertionError(f"fake_http_mcp did not open port {port} in time")


def test_single_server_entry_surfaces_in_upstream_registry(
    tmp_path: Path,
) -> None:
    """Adding ONE server block to .agent/mcp.toml appears in mcp_toml_as_upstreams."""
    with _spawn_fake_http_mcp() as port:
        _wait_for_port(port)
        url = f"http://127.0.0.1:{port}/mcp"
        _write_mcp_toml(tmp_path, "my-custom-server", url)

        upstreams = mcp_toml_as_upstreams(tmp_path)

    assert len(upstreams) == 1, "Expected exactly one upstream server"
    assert upstreams[0].name == "my-custom-server"


def test_single_server_entry_produces_non_empty_tool_definitions(
    tmp_path: Path,
) -> None:
    """UpstreamRegistry built from the upstreams has non-empty tool_definitions."""
    with _spawn_fake_http_mcp() as port:
        _wait_for_port(port)
        url = f"http://127.0.0.1:{port}/mcp"
        _write_mcp_toml(tmp_path, "my-custom-server", url)

        upstreams = mcp_toml_as_upstreams(tmp_path)
        registry = UpstreamRegistry.build(upstreams)
        tool_defs = registry.tool_definitions()

    assert len(tool_defs) > 0, "Expected at least one proxied tool from the custom server"
    # Verify the alias follows the expected naming pattern
    expected_alias = upstream_proxy_tool_name("my-custom-server", "fake_tool")
    aliases = {t.alias for t in tool_defs}
    assert expected_alias in aliases, (
        f"Expected alias {expected_alias!r} not found in {aliases}"
    )


def test_single_server_entry_appears_in_ralph_transport_configs(
    tmp_path: Path,
) -> None:
    """The Ralph transport configs include the custom server via UpstreamRegistry."""
    with _spawn_fake_http_mcp() as port:
        _wait_for_port(port)
        url = f"http://127.0.0.1:{port}/mcp"
        _write_mcp_toml(tmp_path, "my-custom-server", url)

        upstreams = mcp_toml_as_upstreams(tmp_path)
        registry = UpstreamRegistry.build(upstreams)
        tool_defs = registry.tool_definitions()

    # Verify ralph appears in Claude config
    ralph_endpoint = "http://127.0.0.1:9999/mcp"
    config_json = claude_mcp_config(ralph_endpoint, workspace_path=tmp_path)
    parsed = json.loads(config_json)
    assert "mcpServers" in parsed
    assert "ralph" in parsed["mcpServers"]

    # Verify the custom server's tools appear in the registry
    # (the registry is what gets exposed to agents via UpstreamProxyHandler)
    custom_alias = upstream_proxy_tool_name("my-custom-server", "fake_tool")
    registry_aliases = {t.alias for t in tool_defs}
    assert custom_alias in registry_aliases, (
        f"Custom server tool {custom_alias!r} not in registry: {registry_aliases}"
    )


def test_single_server_entry_appears_in_codex_transport_config(
    tmp_path: Path,
) -> None:
    """prepare_codex_home_with_upstreams surfaces the custom server via upstreams."""
    with _spawn_fake_http_mcp() as port:
        _wait_for_port(port)
        url = f"http://127.0.0.1:{port}/mcp"
        _write_mcp_toml(tmp_path, "my-custom-server", url)

        # Exercise prepare_codex_home_with_upstreams - this parses the mcp.toml
        # and returns the upstreams alongside the codex home path
        codex_home, upstreams = prepare_codex_home_with_upstreams(
            None,  # no Ralph endpoint in this test
            workspace_path=tmp_path,
            existing_home=None,
            system_prompt_file=None,
        )

    # Verify the upstreams from mcp.toml were extracted
    assert len(upstreams) == 1, "Expected exactly one upstream from mcp.toml"
    assert upstreams[0].name == "my-custom-server", (
        f"Expected 'my-custom-server', got {upstreams[0].name!r}"
    )
    # Verify codex home was created
    assert Path(codex_home).exists(), "Codex home directory should exist"
    assert (Path(codex_home) / "config.toml").exists(), (
        "Codex config.toml should exist"
    )


def test_single_server_entry_appears_in_opencode_transport_config(
    tmp_path: Path,
) -> None:
    """build_opencode_provider_config includes the custom server tools in upstreams."""
    with _spawn_fake_http_mcp() as port:
        _wait_for_port(port)
        url = f"http://127.0.0.1:{port}/mcp"
        _write_mcp_toml(tmp_path, "my-custom-server", url)

        # Parse upstreams from mcp.toml
        upstreams = mcp_toml_as_upstreams(tmp_path)

        # build_opencode_provider_config takes existing config + Ralph endpoint
        # and returns the merged config plus any upstreams found in existing config.
        # For this test we pass None for existing and a fake Ralph endpoint.
        config_json, existing_upstreams = build_opencode_provider_config(
            None,  # no pre-existing opencode config
            "http://127.0.0.1:9999/mcp",  # Ralph endpoint
        )

    # Verify the returned config contains ralph MCP server
    parsed = json.loads(config_json)
    assert "mcp" in parsed, "OpenCode config should have 'mcp' key"
    assert "ralph" in parsed["mcp"], "OpenCode config should include ralph MCP server"
    # The upstreams from existing config should be empty since we passed None
    assert len(existing_upstreams) == 0, (
        "No existing upstreams expected when existing config is None"
    )


def test_single_server_entry_surfaces_in_all_transport_paths(
    tmp_path: Path,
) -> None:
    """Full end-to-end: single server in mcp.toml appears across all transport configs."""
    with _spawn_fake_http_mcp() as port:
        _wait_for_port(port)
        url = f"http://127.0.0.1:{port}/mcp"
        _write_mcp_toml(tmp_path, "my-custom-server", url)

        # Parse upstreams once
        upstreams = mcp_toml_as_upstreams(tmp_path)
        assert len(upstreams) == 1, "Expected one upstream"

        # Build registry
        registry = UpstreamRegistry.build(upstreams)
        tool_defs = registry.tool_definitions()
        custom_alias = upstream_proxy_tool_name("my-custom-server", "fake_tool")
        registry_aliases = {t.alias for t in tool_defs}

        # Claude transport
        ralph_endpoint = "http://127.0.0.1:9999/mcp"
        claude_config = json.loads(claude_mcp_config(ralph_endpoint, workspace_path=tmp_path))
        assert "ralph" in claude_config.get("mcpServers", {})

        # Codex transport
        codex_home, codex_upstreams = prepare_codex_home_with_upstreams(
            None,
            workspace_path=tmp_path,
            existing_home=None,
            system_prompt_file=None,
        )
        assert len(codex_upstreams) == 1

        # OpenCode transport
        opencode_config_json, _ = build_opencode_provider_config(
            None,
            ralph_endpoint,
        )
        opencode_config = json.loads(opencode_config_json)
        assert "mcp" in opencode_config
        assert "ralph" in opencode_config["mcp"]

        # Registry should surface the custom server tool
        assert custom_alias in registry_aliases
