"""Integration tests for legacy HTTP+SSE upstream MCP validation.

Tests the full chain from mcp.toml parsing through _validate_custom_mcp_servers
for SSE-type server entries, without spawning real subprocess servers.
"""

from __future__ import annotations

from io import StringIO
from textwrap import dedent
from typing import TYPE_CHECKING

import pytest
from loguru import logger

from ralph.mcp.upstream.upstream_tool import UpstreamTool
from ralph.mcp.upstream.validation import validate_upstream_mcp_servers
from ralph.pipeline import runner as runner_module
from tests.fixtures.mcp_test_harness import FAKE_TOOL, StubUpstreamClient

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from pathlib import Path

    from ralph.mcp.upstream.config import UpstreamMcpServer


@pytest.fixture(autouse=True)
def _reset_loguru() -> Iterator[None]:
    logger.remove()
    yield
    logger.remove()


def _write_sse_mcp_toml(workspace: Path, port: int) -> None:
    agent_dir = workspace / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    body = (
        dedent(
            f"""
        [mcp_servers.fake_sse]
        transport = "http"
        url = "http://127.0.0.1:{port}/sse"
        """
        ).strip()
        + "\n"
    )
    (agent_dir / "mcp.toml").write_text(body, encoding="utf-8")


def _stub_successful_tool_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ralph.pipeline._runner_mcp_validation.collect_tool_catalog",
        lambda servers: {
            server.name: [
                UpstreamTool(
                    name=FAKE_TOOL.name,
                    description=FAKE_TOOL.description,
                    input_schema=FAKE_TOOL.input_schema,
                )
            ]
            for server in servers
        },
    )


def test_validate_sse_server_healthy_returns_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_validate_custom_mcp_servers returns 0 when SSE server preflight and probe pass."""
    _write_sse_mcp_toml(tmp_path, 9999)
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    monkeypatch.delenv("RALPH_MCP_STRICT", raising=False)

    def passing_validate(servers: Iterable[UpstreamMcpServer], *, strict: bool | None) -> object:
        return validate_upstream_mcp_servers(
            servers,
            strict=strict,
            preflight_http=lambda *a, **k: None,
        )

    monkeypatch.setattr(
        "ralph.mcp.upstream.validation.make_upstream_client",
        lambda server, **kw: StubUpstreamClient([FAKE_TOOL]),
    )
    _stub_successful_tool_catalog(monkeypatch)
    monkeypatch.setattr(runner_module, "VALIDATE_MCP", passing_validate)
    monkeypatch.setattr(runner_module, "PROBE_AGENT_TRANSPORTS", lambda *a, **k: ())

    rc = runner_module.validate_custom_mcp_servers(tmp_path)

    assert rc == 0


def test_validate_sse_server_healthy_logs_no_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No error-level logs are emitted when SSE server validation succeeds."""
    _write_sse_mcp_toml(tmp_path, 9999)
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    monkeypatch.delenv("RALPH_MCP_STRICT", raising=False)

    def passing_validate(servers: Iterable[UpstreamMcpServer], *, strict: bool | None) -> object:
        return validate_upstream_mcp_servers(
            servers,
            strict=strict,
            preflight_http=lambda *a, **k: None,
        )

    monkeypatch.setattr(
        "ralph.mcp.upstream.validation.make_upstream_client",
        lambda server, **kw: StubUpstreamClient([FAKE_TOOL]),
    )
    _stub_successful_tool_catalog(monkeypatch)
    monkeypatch.setattr(runner_module, "VALIDATE_MCP", passing_validate)
    monkeypatch.setattr(runner_module, "PROBE_AGENT_TRANSPORTS", lambda *a, **k: ())

    error_stream = StringIO()
    sink_id = logger.add(error_stream, level="ERROR")
    try:
        rc = runner_module.validate_custom_mcp_servers(tmp_path)
    finally:
        logger.remove(sink_id)

    assert rc == 0
    assert "fake_sse" not in error_stream.getvalue()
