"""Round-trip integration test coverage for legacy HTTP+SSE upstream entries."""

from __future__ import annotations

import contextlib
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.config.enums import AgentTransport
from ralph.mcp.tools.names import upstream_proxy_tool_name
from ralph.mcp.transport.common import mcp_toml_as_upstreams
from ralph.mcp.upstream.agent_probe import probe_agent_transports
from ralph.mcp.upstream.registry import UpstreamRegistry
from ralph.process.manager import ProcessTerminationError, get_process_manager

if TYPE_CHECKING:
    from collections.abc import Iterator

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
FAKE_SSE_MCP = PACKAGE_ROOT / "tests" / "fixtures" / "fake_sse_mcp.py"
_EXPECTED_TRANSPORT_COUNT = len(
    [AgentTransport.CLAUDE, AgentTransport.CODEX, AgentTransport.OPENCODE]
)

pytestmark = pytest.mark.timeout_seconds(20)


@contextmanager
def _spawn_fake_sse_mcp() -> Iterator[int]:
    handle = get_process_manager().spawn(
        [sys.executable, str(FAKE_SSE_MCP)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        label="test:fake-sse-mcp-roundtrip",
    )
    try:
        stdout = handle.stdout
        assert stdout is not None
        port_line = stdout.readline().strip()
        assert port_line, "fake_sse_mcp did not print its port"
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
    raise AssertionError(f"fake_sse_mcp did not open port {port} in time")


def test_sse_entry_surfaces_in_upstream_registry(tmp_path: Path) -> None:
    with _spawn_fake_sse_mcp() as port:
        _wait_for_port(port)
        url = f"http://127.0.0.1:{port}/sse"
        _write_mcp_toml(tmp_path, "docs-sse", url)

        upstreams = mcp_toml_as_upstreams(tmp_path)
        assert len(upstreams) == 1
        assert upstreams[0].name == "docs-sse"

        registry = UpstreamRegistry.build(upstreams)
        aliases = {t.alias for t in registry.tool_definitions()}

    expected = upstream_proxy_tool_name("docs-sse", "fake_tool")
    assert expected in aliases


def test_sse_entry_probe_agent_transports_sees_server_as_reachable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    with _spawn_fake_sse_mcp() as port:
        _wait_for_port(port)
        url = f"http://127.0.0.1:{port}/sse"
        _write_mcp_toml(tmp_path, "docs-sse", url)

        reports = probe_agent_transports(
            mcp_toml_as_upstreams(tmp_path),
            transports=(AgentTransport.CLAUDE, AgentTransport.CODEX, AgentTransport.OPENCODE),
            workspace_path=tmp_path,
        )

    assert len(reports) == _EXPECTED_TRANSPORT_COUNT
    for report in reports:
        assert report.ok is True, f"{report.transport}: {report.error}"


def test_sse_registry_call_tool_round_trip(tmp_path: Path) -> None:
    with _spawn_fake_sse_mcp() as port:
        _wait_for_port(port)
        url = f"http://127.0.0.1:{port}/sse"
        _write_mcp_toml(tmp_path, "docs-sse", url)
        upstreams = mcp_toml_as_upstreams(tmp_path)
        registry = UpstreamRegistry.build(upstreams)
        definitions = list(registry.tool_definitions())
        alias = upstream_proxy_tool_name("docs-sse", "fake_tool")
        assert any(item.alias == alias for item in definitions)

        result = registry.call_tool(alias, {})

    assert isinstance(result, dict)
    content = result.get("content")
    assert isinstance(content, list)
    first = content[0]
    assert isinstance(first, dict)
    assert first.get("text") == "fake-sse-result"
