"""Unit tests for ralph.pipeline.runner._validate_custom_mcp_servers."""

from __future__ import annotations

from io import StringIO
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from loguru import logger

from ralph.config.enums import AgentTransport
from ralph.mcp.upstream.agent_probe import AgentProbeReport
from ralph.mcp.upstream.config import UpstreamMcpServer
from ralph.mcp.upstream.validation import (
    UpstreamServerReport,
    UpstreamValidationError,
    UpstreamValidationReport,
)
from ralph.pipeline import runner as runner_module

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _reset_loguru() -> Iterator[None]:
    logger.remove()
    yield
    logger.remove()


def _http_server(name: str = "alpha") -> UpstreamMcpServer:
    return UpstreamMcpServer(name=name, transport="http", url=f"http://example.invalid/{name}")


def _stdio_server(name: str = "beta") -> UpstreamMcpServer:
    return UpstreamMcpServer(
        name=name,
        transport="stdio",
        command="my-mcp",
        args=("--flag",),
    )


def _ok_report(servers: tuple[UpstreamMcpServer, ...]) -> UpstreamValidationReport:
    return UpstreamValidationReport(
        servers=tuple(
            UpstreamServerReport(
                name=s.name,
                transport=s.transport,
                ok=True,
                tool_count=1,
                error=None,
            )
            for s in servers
        )
    )


def test_returns_zero_when_no_custom_mcp_servers_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("ralph.agents.transport_emit._mcp_toml_as_upstreams", lambda _p: ())
    validate_mock = MagicMock()
    probe_mock = MagicMock()
    monkeypatch.setattr(runner_module, "_VALIDATE_MCP", validate_mock)
    monkeypatch.setattr(runner_module, "_PROBE_AGENT_TRANSPORTS", probe_mock)

    rc = runner_module._validate_custom_mcp_servers(tmp_path)

    assert rc == 0
    assert validate_mock.called is False
    assert probe_mock.called is False


def test_healthy_upstreams_and_probes_return_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    http_server = _http_server("alpha")
    stdio_server = _stdio_server("beta")
    servers = (http_server, stdio_server)
    monkeypatch.setattr("ralph.agents.transport_emit._mcp_toml_as_upstreams", lambda _p: servers)

    validate_mock = MagicMock(return_value=_ok_report(servers))
    monkeypatch.setattr(runner_module, "_VALIDATE_MCP", validate_mock)

    probe_mock = MagicMock(return_value=())
    monkeypatch.setattr(runner_module, "_PROBE_AGENT_TRANSPORTS", probe_mock)

    rc = runner_module._validate_custom_mcp_servers(tmp_path)

    assert rc == 0
    probe_mock.assert_called_once()
    forwarded_servers, kwargs = probe_mock.call_args
    assert forwarded_servers[0] == servers
    assert kwargs == {"workspace_path": tmp_path}


def test_strict_mode_upstream_validation_error_returns_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    server = _http_server("alpha")
    monkeypatch.setattr("ralph.agents.transport_emit._mcp_toml_as_upstreams", lambda _p: (server,))
    monkeypatch.delenv("RALPH_MCP_STRICT", raising=False)

    def raising_validator(*_args: object, **_kwargs: object) -> UpstreamValidationReport:
        raise UpstreamValidationError("boom")

    monkeypatch.setattr(runner_module, "_VALIDATE_MCP", raising_validator)

    probe_mock = MagicMock()
    monkeypatch.setattr(runner_module, "_PROBE_AGENT_TRANSPORTS", probe_mock)

    stream = StringIO()
    sink_id = logger.add(stream, level="ERROR")
    try:
        rc = runner_module._validate_custom_mcp_servers(tmp_path)
    finally:
        logger.remove(sink_id)

    assert rc == 1
    assert probe_mock.called is False
    assert "boom" in stream.getvalue()


def test_soft_mode_upstream_failure_returns_zero_and_skips_failed_servers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    alpha = _http_server("alpha")
    beta = _http_server("beta")
    servers = (alpha, beta)
    monkeypatch.setattr("ralph.agents.transport_emit._mcp_toml_as_upstreams", lambda _p: servers)
    monkeypatch.setenv("RALPH_MCP_STRICT", "0")

    mixed_report = UpstreamValidationReport(
        servers=(
            UpstreamServerReport(name="alpha", transport="http", ok=True, tool_count=2),
            UpstreamServerReport(
                name="beta",
                transport="http",
                ok=False,
                tool_count=0,
                error="connection refused",
            ),
        )
    )
    monkeypatch.setattr(
        runner_module,
        "_VALIDATE_MCP",
        MagicMock(return_value=mixed_report),
    )

    captured: list[tuple[tuple[UpstreamMcpServer, ...], dict[str, object]]] = []

    def fake_probe(
        servers_arg: tuple[UpstreamMcpServer, ...], **kwargs: object
    ) -> tuple[AgentProbeReport, ...]:
        captured.append((tuple(servers_arg), dict(kwargs)))
        return ()

    monkeypatch.setattr(runner_module, "_PROBE_AGENT_TRANSPORTS", fake_probe)

    rc = runner_module._validate_custom_mcp_servers(tmp_path)

    assert rc == 0
    assert len(captured) == 1
    forwarded, kwargs = captured[0]
    assert tuple(s.name for s in forwarded) == ("alpha",)
    assert kwargs == {"workspace_path": tmp_path}


def test_strict_mode_probe_failure_returns_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    server = _http_server("alpha")
    monkeypatch.setattr("ralph.agents.transport_emit._mcp_toml_as_upstreams", lambda _p: (server,))
    monkeypatch.delenv("RALPH_MCP_STRICT", raising=False)

    monkeypatch.setattr(
        runner_module,
        "_VALIDATE_MCP",
        MagicMock(return_value=_ok_report((server,))),
    )

    failing_probe = (
        AgentProbeReport(
            transport=AgentTransport.CLAUDE,
            server_name="alpha",
            ok=False,
            error="handshake failed",
        ),
    )
    monkeypatch.setattr(
        runner_module,
        "_PROBE_AGENT_TRANSPORTS",
        MagicMock(return_value=failing_probe),
    )

    error_stream = StringIO()
    error_sink = logger.add(error_stream, level="ERROR")
    warning_stream = StringIO()
    warning_sink = logger.add(warning_stream, level="WARNING")
    try:
        rc = runner_module._validate_custom_mcp_servers(tmp_path)
    finally:
        logger.remove(error_sink)
        logger.remove(warning_sink)

    assert rc == 1
    assert "handshake failed" in error_stream.getvalue()


def test_soft_mode_probe_failure_returns_zero_and_logs_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    server = _http_server("alpha")
    monkeypatch.setattr("ralph.agents.transport_emit._mcp_toml_as_upstreams", lambda _p: (server,))
    monkeypatch.setenv("RALPH_MCP_STRICT", "0")

    monkeypatch.setattr(
        runner_module,
        "_VALIDATE_MCP",
        MagicMock(return_value=_ok_report((server,))),
    )

    failing_probe = (
        AgentProbeReport(
            transport=AgentTransport.CLAUDE,
            server_name="alpha",
            ok=False,
            error="handshake failed",
        ),
    )
    monkeypatch.setattr(
        runner_module,
        "_PROBE_AGENT_TRANSPORTS",
        MagicMock(return_value=failing_probe),
    )

    error_stream = StringIO()
    error_sink = logger.add(error_stream, level="ERROR")
    warning_stream = StringIO()
    warning_sink = logger.add(warning_stream, level="WARNING")
    try:
        rc = runner_module._validate_custom_mcp_servers(tmp_path)
    finally:
        logger.remove(error_sink)
        logger.remove(warning_sink)

    assert rc == 0
    warning_output = warning_stream.getvalue()
    assert "handshake failed" in warning_output
    assert "soft mode" in warning_output


def test_no_probe_invoked_when_no_healthy_servers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    server = _http_server("alpha")
    monkeypatch.setattr("ralph.agents.transport_emit._mcp_toml_as_upstreams", lambda _p: (server,))
    monkeypatch.setenv("RALPH_MCP_STRICT", "0")

    failing_report = UpstreamValidationReport(
        servers=(
            UpstreamServerReport(
                name="alpha",
                transport="http",
                ok=False,
                tool_count=0,
                error="connection refused",
            ),
        )
    )
    monkeypatch.setattr(runner_module, "_VALIDATE_MCP", MagicMock(return_value=failing_report))

    probe_mock = MagicMock()
    monkeypatch.setattr(runner_module, "_PROBE_AGENT_TRANSPORTS", probe_mock)

    rc = runner_module._validate_custom_mcp_servers(tmp_path)

    assert rc == 0
    assert probe_mock.called is False
