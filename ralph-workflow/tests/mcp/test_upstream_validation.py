"""Tests for ralph/mcp/upstream_validation.py."""

from __future__ import annotations

from datetime import timedelta
from io import StringIO
from typing import TYPE_CHECKING

import pytest
from loguru import logger

from ralph.mcp.startup import RetryablePreflightError
from ralph.mcp.upstream_config import UpstreamMcpServer
from ralph.mcp.upstream_models import UpstreamTool
from ralph.mcp.upstream_validation import (
    UpstreamValidationError,
    strict_mode_from_env,
    validate_upstream_mcp_servers,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _reset_loguru() -> Iterator[None]:
    logger.remove()
    yield
    logger.remove()


def _http_server(name: str = "remote", env: dict[str, str] | None = None) -> UpstreamMcpServer:
    return UpstreamMcpServer(
        name=name,
        transport="http",
        url="http://example.invalid/mcp",
        env=env or {},
    )


def _stdio_server(name: str = "local", env: dict[str, str] | None = None) -> UpstreamMcpServer:
    return UpstreamMcpServer(
        name=name,
        transport="stdio",
        command="my-mcp",
        args=("--flag",),
        env=env or {},
    )


def _passing_http(*_args: object, **_kwargs: object) -> None:
    return None


class _StubClient:
    def __init__(self, tools: list[UpstreamTool]) -> None:
        self._tools = tools

    def list_tools(self) -> list[UpstreamTool]:
        return list(self._tools)

    def call_tool(self, name: str, arguments: dict[str, object]) -> object:  # pragma: no cover
        del name, arguments
        return None


def _patch_make_upstream_client(monkeypatch: pytest.MonkeyPatch, client: _StubClient) -> None:
    def factory(_server: UpstreamMcpServer, **_kw: object) -> _StubClient:
        return client

    monkeypatch.setattr("ralph.mcp.upstream_validation.make_upstream_client", factory)


def test_validator_passes_healthy_http_server(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _http_server()
    _patch_make_upstream_client(
        monkeypatch,
        _StubClient([UpstreamTool(name="ping", description="ping")]),
    )
    report = validate_upstream_mcp_servers([server], strict=True, preflight_http=_passing_http)
    assert report.all_ok
    assert report.servers[0].tool_count == 1


def test_validator_passes_healthy_stdio_server(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _stdio_server()
    _patch_make_upstream_client(
        monkeypatch,
        _StubClient([UpstreamTool(name="ping", description="ping")]),
    )
    report = validate_upstream_mcp_servers([server], strict=True)
    assert report.all_ok
    assert report.servers[0].tool_count == 1


def test_validator_raises_in_strict_mode_on_unreachable_server() -> None:
    server = _http_server(name="unreachable")

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RetryablePreflightError("connection refused")

    with pytest.raises(UpstreamValidationError) as excinfo:
        validate_upstream_mcp_servers([server], strict=True, preflight_http=boom)
    assert "unreachable" in str(excinfo.value)


def test_validator_does_not_raise_in_soft_mode() -> None:
    server = _http_server(name="degraded")

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RetryablePreflightError("connection refused")

    stream = StringIO()
    sink_id = logger.add(stream, level="WARNING")
    try:
        report = validate_upstream_mcp_servers([server], strict=False, preflight_http=boom)
    finally:
        logger.remove(sink_id)
    assert not report.all_ok
    assert report.servers[0].error is not None
    assert "connection refused" in stream.getvalue()


def test_validator_report_redacts_env_secrets() -> None:
    secret = "supersecret"
    server = _http_server(name="leaky", env={"API_KEY": secret})

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RetryablePreflightError(f"connection refused (token={secret})")

    with pytest.raises(UpstreamValidationError) as excinfo:
        validate_upstream_mcp_servers([server], strict=True, preflight_http=boom)
    message = str(excinfo.value)
    assert secret not in message
    assert "API_KEY" in message
    failure_repr = repr(excinfo.value)
    assert secret not in failure_repr


def test_strict_mode_from_env_defaults_to_strict() -> None:
    assert strict_mode_from_env({}) is True


def test_strict_mode_from_env_honors_zero_false_no() -> None:
    assert strict_mode_from_env({"RALPH_MCP_STRICT": "0"}) is False
    assert strict_mode_from_env({"RALPH_MCP_STRICT": "false"}) is False
    assert strict_mode_from_env({"RALPH_MCP_STRICT": "FALSE"}) is False
    assert strict_mode_from_env({"RALPH_MCP_STRICT": "no"}) is False
    assert strict_mode_from_env({"RALPH_MCP_STRICT": "off"}) is False
    assert strict_mode_from_env({"RALPH_MCP_STRICT": "1"}) is True
    assert strict_mode_from_env({"RALPH_MCP_STRICT": ""}) is True


def test_validator_empty_iterable_returns_empty_report() -> None:
    report = validate_upstream_mcp_servers([], strict=True)
    assert report.servers == ()
    assert report.all_ok is True


def test_validator_uses_explicit_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[timedelta] = []

    def fake_http(_url: str, _required: object, timeout: timedelta) -> None:
        captured.append(timeout)

    _patch_make_upstream_client(monkeypatch, _StubClient([]))
    validate_upstream_mcp_servers(
        [_http_server()],
        strict=True,
        preflight_http=fake_http,
        timeout=timedelta(seconds=2),
    )
    assert captured[0] == timedelta(seconds=2)
