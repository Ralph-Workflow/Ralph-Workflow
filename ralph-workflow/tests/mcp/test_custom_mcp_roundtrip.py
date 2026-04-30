"""Round-trip integration test: a fresh mcp.toml entry surfaces to every agent."""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import time
import tomllib
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.config.enums import AgentTransport
from ralph.mcp.tools.names import upstream_proxy_tool_name
from ralph.mcp.transport.claude import claude_mcp_config
from ralph.mcp.transport.codex import prepare_codex_home_with_upstreams
from ralph.mcp.transport.common import mcp_toml_as_upstreams
from ralph.mcp.transport.opencode import build_opencode_provider_config
from ralph.mcp.upstream.agent_probe import probe_agent_transports
from ralph.mcp.upstream.config import UpstreamMcpServer
from ralph.mcp.upstream.registry import UpstreamRegistry
from ralph.process.manager import ProcessTerminationError, get_process_manager

if TYPE_CHECKING:
    from collections.abc import Iterator

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
FAKE_HTTP_MCP = PACKAGE_ROOT / "tests" / "fixtures" / "fake_http_mcp.py"
_EXPECTED_TRANSPORT_COUNT = len(
    [AgentTransport.CLAUDE, AgentTransport.CODEX, AgentTransport.OPENCODE]
)

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(20)]


@contextmanager
def _spawn_fake_http_mcp() -> Iterator[int]:
    handle = get_process_manager().spawn(
        [sys.executable, str(FAKE_HTTP_MCP)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        label="test:fake-http-mcp-roundtrip",
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


def _write_mcp_toml(workspace: Path, server_name: str, url: str) -> None:
    agent_dir = workspace / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "mcp.toml").write_text(
        f'[mcp_servers.{server_name}]\ntransport = "http"\nurl = "{url}"\n',
        encoding="utf-8",
    )


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


def test_mcp_toml_entry_surfaces_in_upstream_registry(
    tmp_path: Path,
) -> None:
    with _spawn_fake_http_mcp() as port:
        _wait_for_port(port)
        url = f"http://127.0.0.1:{port}/mcp"
        _write_mcp_toml(tmp_path, "angular-docs", url)

        upstreams = mcp_toml_as_upstreams(tmp_path)
        assert len(upstreams) == 1
        assert upstreams[0].name == "angular-docs"

        registry = UpstreamRegistry.build(upstreams)
        aliases = {t.alias for t in registry.tool_definitions()}

    expected = upstream_proxy_tool_name("angular-docs", "fake_tool")
    assert expected in aliases


def test_mcp_toml_entry_appears_in_claude_config(
    tmp_path: Path,
) -> None:
    with _spawn_fake_http_mcp() as port:
        _wait_for_port(port)
        url = f"http://127.0.0.1:{port}/mcp"
        _write_mcp_toml(tmp_path, "angular-docs", url)

    ralph_endpoint = "http://127.0.0.1:9999/mcp"
    config_json = claude_mcp_config(ralph_endpoint, workspace_path=tmp_path)
    parsed = json.loads(config_json)
    assert "ralph" in parsed["mcpServers"]


def test_mcp_toml_entry_appears_in_codex_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    with _spawn_fake_http_mcp() as port:
        _wait_for_port(port)
        url = f"http://127.0.0.1:{port}/mcp"
        _write_mcp_toml(tmp_path, "angular-docs", url)

    ralph_endpoint = "http://127.0.0.1:9999/mcp"
    codex_home, _resolved_upstreams = prepare_codex_home_with_upstreams(
        ralph_endpoint,
        workspace_path=tmp_path,
        existing_home=None,
        system_prompt_file=None,
    )
    config_path = Path(codex_home) / "config.toml"
    parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert "ralph" in parsed.get("mcp_servers", {})


def test_mcp_toml_entry_appears_in_opencode_config(
    tmp_path: Path,
) -> None:
    with _spawn_fake_http_mcp() as port:
        _wait_for_port(port)
        url = f"http://127.0.0.1:{port}/mcp"
        _write_mcp_toml(tmp_path, "angular-docs", url)

    ralph_endpoint = "http://127.0.0.1:9999/mcp"
    config_json, _resolved_upstreams = build_opencode_provider_config(None, ralph_endpoint)
    parsed = json.loads(config_json)
    assert "ralph" in parsed.get("mcp", {})


def test_probe_agent_transports_sees_server_as_reachable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    with _spawn_fake_http_mcp() as port:
        _wait_for_port(port)
        url = f"http://127.0.0.1:{port}/mcp"
        server = UpstreamMcpServer(name="angular-docs", transport="http", url=url)

        reports = probe_agent_transports(
            [server],
            transports=(AgentTransport.CLAUDE, AgentTransport.CODEX, AgentTransport.OPENCODE),
            workspace_path=tmp_path,
        )

    assert len(reports) == _EXPECTED_TRANSPORT_COUNT
    for report in reports:
        assert report.ok is True, f"{report.transport}: {report.error}"
