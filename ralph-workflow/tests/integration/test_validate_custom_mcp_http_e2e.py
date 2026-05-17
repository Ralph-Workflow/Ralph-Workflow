"""Integration tests for the runner's HTTP MCP startup validation.

Tests the full chain from mcp.toml parsing through _validate_custom_mcp_servers
without spawning real subprocess servers or waiting on real sockets.
"""

from __future__ import annotations

from io import StringIO
from textwrap import dedent
from typing import TYPE_CHECKING

import pytest
from loguru import logger

from ralph.mcp.protocol.startup import RetryablePreflightError
from ralph.mcp.upstream.validation import (
    validate_upstream_mcp_servers,
)
from ralph.pipeline import runner as runner_module
from tests.fixtures.mcp_test_harness import FAKE_TOOL, StubUpstreamClient

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _reset_loguru() -> Iterator[None]:
    logger.remove()
    yield
    logger.remove()


def _write_http_mcp_toml(workspace: Path, port: int) -> None:
    agent_dir = workspace / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    body = (
        dedent(
            f"""
        [mcp_servers.fake_http]
        transport = "http"
        url = "http://127.0.0.1:{port}/mcp"
        """
        ).strip()
        + "\n"
    )
    (agent_dir / "mcp.toml").write_text(body, encoding="utf-8")


def test_validate_http_server_healthy_returns_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_validate_custom_mcp_servers returns 0 when HTTP preflight and tool probe pass."""
    _write_http_mcp_toml(tmp_path, 9999)
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    monkeypatch.delenv("RALPH_MCP_STRICT", raising=False)

    def passing_validate(servers: object, *, strict: object) -> object:
        return validate_upstream_mcp_servers(
            servers,
            strict=strict,
            preflight_http=lambda *a, **k: None,
        )

    monkeypatch.setattr(
        "ralph.mcp.upstream.validation.make_upstream_client",
        lambda server, **kw: StubUpstreamClient([FAKE_TOOL]),
    )
    monkeypatch.setattr(runner_module, "VALIDATE_MCP", passing_validate)
    monkeypatch.setattr(runner_module, "PROBE_AGENT_TRANSPORTS", lambda *a, **k: ())

    rc = runner_module.validate_custom_mcp_servers(tmp_path)

    assert rc == 0


def test_validate_http_server_unreachable_strict_returns_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_validate_custom_mcp_servers returns 1 in strict mode when HTTP server unreachable."""
    _write_http_mcp_toml(tmp_path, 9999)
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    monkeypatch.delenv("RALPH_MCP_STRICT", raising=False)

    def failing_validate(servers: object, *, strict: object) -> object:
        def boom(*a: object, **k: object) -> None:
            raise RetryablePreflightError("connection refused")

        return validate_upstream_mcp_servers(servers, strict=strict, preflight_http=boom)

    monkeypatch.setattr(runner_module, "VALIDATE_MCP", failing_validate)
    monkeypatch.setattr(runner_module, "PROBE_AGENT_TRANSPORTS", lambda *a, **k: ())

    error_stream = StringIO()
    sink_id = logger.add(error_stream, level="ERROR")
    try:
        rc = runner_module.validate_custom_mcp_servers(tmp_path)
    finally:
        logger.remove(sink_id)

    assert rc == 1
    assert "fake_http" in error_stream.getvalue()


def test_validate_http_server_unreachable_soft_returns_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_validate_custom_mcp_servers returns 0 in soft mode when HTTP server unreachable."""
    _write_http_mcp_toml(tmp_path, 9999)
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    monkeypatch.setenv("RALPH_MCP_STRICT", "0")

    def failing_validate(servers: object, *, strict: object) -> object:
        def boom(*a: object, **k: object) -> None:
            raise RetryablePreflightError("connection refused")

        return validate_upstream_mcp_servers(servers, strict=strict, preflight_http=boom)

    monkeypatch.setattr(runner_module, "VALIDATE_MCP", failing_validate)
    monkeypatch.setattr(runner_module, "PROBE_AGENT_TRANSPORTS", lambda *a, **k: ())

    warning_stream = StringIO()
    sink_id = logger.add(warning_stream, level="WARNING")
    try:
        rc = runner_module.validate_custom_mcp_servers(tmp_path)
    finally:
        logger.remove(sink_id)

    assert rc == 0
    assert "fake_http" in warning_stream.getvalue()
