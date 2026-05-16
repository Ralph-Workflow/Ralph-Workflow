"""Tests for the MCP startup port."""

from __future__ import annotations

import datetime
import errno
from typing import TYPE_CHECKING, cast

import httpx
import pytest

from ralph.mcp.protocol import startup
from ralph.mcp.protocol.capability_mapping import AccessMode, SessionDrain
from ralph.mcp.protocol.env import MCP_PREFLIGHT_TIMEOUT_MS_ENV, MCP_SUPERVISION_INTERVAL_MS_ENV
from ralph.mcp.upstream.config import (
    UpstreamConfigError,
    UpstreamMcpServer,
    load_upstream_mcp_servers,
    normalize_upstream_mcp_servers,
    serialize_upstream_mcp_servers,
)
from ralph.policy.models import AgentChainConfig, AgentDrainConfig, AgentsPolicy

if TYPE_CHECKING:
    import socket
    from pathlib import Path

EXPECTED_PREFLIGHT_ATTEMPTS = 2


_DEFUALT_AGENTS_POLICY = AgentsPolicy(
    agent_chains={"default": AgentChainConfig(agents=["agent"])},
    agent_drains={
        "planning": AgentDrainConfig(chain="default", drain_class="planning"),
        "development": AgentDrainConfig(chain="default", drain_class="development"),
        "fix": AgentDrainConfig(chain="default", drain_class="fix"),
        "development_analysis": AgentDrainConfig(chain="default", drain_class="analysis"),
        "review_analysis": AgentDrainConfig(chain="default", drain_class="analysis"),
        "development_commit": AgentDrainConfig(chain="default", drain_class="commit"),
        "review_commit": AgentDrainConfig(chain="default", drain_class="commit"),
    },
)


def _append_sleep(target: list[float], seconds: float) -> None:
    target.append(seconds)


def test_access_mode_for_drain_planning_is_read_only() -> None:
    assert (
        startup.access_mode_for_drain(SessionDrain.PLANNING, _DEFUALT_AGENTS_POLICY)
        is AccessMode.READ_ONLY
    )


def test_access_mode_for_drain_development_allows_write() -> None:
    assert (
        startup.access_mode_for_drain(SessionDrain.DEVELOPMENT, _DEFUALT_AGENTS_POLICY)
        is AccessMode.READ_WRITE
    )


def test_access_mode_for_drain_accepts_string_alias() -> None:
    assert startup.access_mode_for_drain("fix", _DEFUALT_AGENTS_POLICY) is AccessMode.READ_WRITE


def test_access_mode_for_development_analysis_is_read_only() -> None:
    assert (
        startup.access_mode_for_drain("development_analysis", _DEFUALT_AGENTS_POLICY)
        is AccessMode.READ_ONLY
    )


def test_access_mode_for_development_commit_is_read_only() -> None:
    assert (
        startup.access_mode_for_drain("development_commit", _DEFUALT_AGENTS_POLICY)
        is AccessMode.READ_ONLY
    )


def test_access_mode_for_review_analysis_is_read_only() -> None:
    assert (
        startup.access_mode_for_drain("review_analysis", _DEFUALT_AGENTS_POLICY)
        is AccessMode.READ_ONLY
    )


def test_access_mode_for_review_commit_is_read_only() -> None:
    assert (
        startup.access_mode_for_drain("review_commit", _DEFUALT_AGENTS_POLICY)
        is AccessMode.READ_ONLY
    )


def test_parse_tcp_endpoint_requires_tcp_scheme() -> None:
    with pytest.raises(ValueError, match="tcp://"):
        startup.parse_tcp_endpoint("127.0.0.1:1234")


def test_parse_http_endpoint_parses_host_path_and_query() -> None:
    target = startup.parse_http_endpoint("http://example.com:8080/path?query=1")
    assert target.address == ("example.com", 8080)
    assert target.host_header == "example.com:8080"
    assert target.path == "/path?query=1"


def test_parse_http_endpoint_uses_default_https_port_and_root_path() -> None:
    target = startup.parse_http_endpoint("https://example.com")
    assert target.address == ("example.com", 443)
    assert target.host_header == "example.com"
    assert target.path == "/"


def test_parse_http_endpoint_rejects_unsupported_scheme() -> None:
    with pytest.raises(ValueError, match="unsupported MCP HTTP scheme 'ftp'"):
        startup.parse_http_endpoint("ftp://example.com")


def test_parse_http_endpoint_rejects_missing_host() -> None:
    with pytest.raises(ValueError, match="missing host"):
        startup.parse_http_endpoint("http:///missing")


def test_upstream_config_normalizes_url_only_http_servers() -> None:
    servers = normalize_upstream_mcp_servers(
        {
            "docs": {
                "url": "https://example.com/mcp",
            }
        }
    )

    assert servers == (
        UpstreamMcpServer(
            name="docs",
            transport="http",
            url="https://example.com/mcp",
        ),
    )


def test_upstream_config_rejects_duplicate_ralph_server_name() -> None:
    with pytest.raises(UpstreamConfigError, match="ralph"):
        normalize_upstream_mcp_servers(
            {
                "ralph": {
                    "url": "https://wrong.example/mcp",
                }
            }
        )


def test_upstream_config_serializes_runtime_payload() -> None:
    payload = serialize_upstream_mcp_servers(
        [
            UpstreamMcpServer(
                name="filesystem",
                transport="stdio",
                command="npx",
                args=("-y", "@modelcontextprotocol/server-filesystem"),
            )
        ]
    )

    assert load_upstream_mcp_servers(payload) == (
        UpstreamMcpServer(
            name="filesystem",
            transport="stdio",
            command="npx",
            args=("-y", "@modelcontextprotocol/server-filesystem"),
        ),
    )


def test_classify_connect_error_returns_retryable_for_transient_errno() -> None:
    error = OSError(errno.ECONNRESET, "reset")
    result = startup.classify_connect_error("tcp://host", error)
    assert isinstance(result, startup.RetryablePreflightError)
    assert "failed to connect" in str(result)


def test_classify_connect_error_returns_permanent_for_non_retryable_errno() -> None:
    error = OSError(errno.EINVAL, "bad")
    result = startup.classify_connect_error("tcp://host", error)
    assert isinstance(result, startup.PermanentPreflightError)


def test_mcp_preflight_timeout_from_env_defaults_to_30_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(str(MCP_PREFLIGHT_TIMEOUT_MS_ENV), raising=False)
    expected = datetime.timedelta(milliseconds=30_000)
    assert startup.mcp_preflight_timeout_from_env() == expected


def test_mcp_preflight_timeout_from_mapping_is_injectable() -> None:
    expected = datetime.timedelta(milliseconds=1234)
    assert (
        startup.mcp_preflight_timeout_from_env({str(MCP_PREFLIGHT_TIMEOUT_MS_ENV): "1234"})
        == expected
    )


def test_heartbeat_policy_from_mapping_is_injectable() -> None:
    policy = startup.heartbeat_policy_from_env({str(MCP_SUPERVISION_INTERVAL_MS_ENV): "1500"})

    assert policy.interval == datetime.timedelta(milliseconds=1500)


def test_run_preflight_loop_accepts_injected_clock_and_sleep() -> None:
    calls: list[datetime.timedelta] = []
    sleeps: list[float] = []
    now_values = iter([0.0, 0.0, 0.0, 0.05])

    def fake_attempt(remaining: datetime.timedelta) -> None:
        calls.append(remaining)
        if len(calls) == 1:
            raise startup.RetryablePreflightError("retry")

    startup.run_preflight_loop(
        "tcp://demo",
        datetime.timedelta(seconds=1),
        fake_attempt,
        monotonic_fn=lambda: next(now_values),
        sleep_fn=lambda seconds: _append_sleep(sleeps, seconds),
    )

    assert len(calls) == EXPECTED_PREFLIGHT_ATTEMPTS
    assert sleeps == [0.1]


def test_connect_to_endpoint_accepts_injected_connector() -> None:
    seen: dict[str, object] = {}

    class Socket:
        pass

    sock = cast("socket.socket", Socket())

    def fake_connect(address: tuple[str, int], timeout: float) -> socket.socket:
        seen["address"] = address
        seen["timeout"] = timeout
        return sock

    result = startup.connect_to_endpoint(
        "tcp://demo",
        ("127.0.0.1", 9000),
        datetime.timedelta(seconds=2),
        connect_fn=fake_connect,
    )

    assert result is sock
    assert seen["address"] == ("127.0.0.1", 9000)


def test_post_http_jsonrpc_accepts_injected_http_post() -> None:
    seen: dict[str, object] = {}

    def fake_post(
        url: str,
        *,
        json: startup.JsonRpcResponse,
        headers: dict[str, str],
        timeout: float,
    ) -> httpx.Response:
        seen["url"] = url
        seen["json"] = json
        seen["headers"] = headers
        seen["timeout"] = timeout
        return httpx.Response(
            200,
            content='{"jsonrpc":"2.0","result":{"ok":true}}',
            headers={"mcp-session-id": "session-1"},
        )

    response = startup.post_http_jsonrpc_with_session(
        "http://demo/mcp",
        startup.parse_http_endpoint("http://demo/mcp"),
        startup.initialize_request(),
        post_fn=fake_post,
    )

    assert response[0]["result"] == {"ok": True}
    assert seen["url"] == "http://demo/mcp"


def test_post_http_jsonrpc_with_session_ignores_missing_ssl_cert_env_for_plain_http(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing_cert = tmp_path / "missing-ca.pem"
    endpoint = "http://127.0.0.1:9/mcp"
    monkeypatch.setenv("SSL_CERT_FILE", str(missing_cert))

    with pytest.raises(startup.RetryablePreflightError, match="failed to connect"):
        startup.post_http_jsonrpc_with_session(
            endpoint,
            startup.parse_http_endpoint(endpoint),
            startup.initialize_request(),
        )


def test_preflight_tcp_attempt_accepts_injected_connector() -> None:
    seen: dict[str, object] = {}

    class Socket:
        def close(self) -> None:
            seen["closed"] = True

        def makefile(self, _mode: str):
            raise AssertionError("should not use real makefile")

    def fake_connect(
        endpoint: str, address: tuple[str, int], remaining: datetime.timedelta
    ) -> socket.socket:
        seen["endpoint"] = endpoint
        seen["address"] = address
        return cast("socket.socket", Socket())

    startup.preflight_tcp_attempt(
        "tcp://demo",
        ("127.0.0.1", 9000),
        ["read_file"],
        datetime.timedelta(seconds=1),
        deps=startup.PreflightTcpDeps(
            connect_to_endpoint_fn=fake_connect,
            list_tools_fn=lambda sock, io_timeout: ["read_file"],
        ),
    )

    assert seen["endpoint"] == "tcp://demo"
    assert seen["closed"] is True


def test_preflight_http_attempt_accepts_injected_post() -> None:
    expected_call_count = 3
    calls: list[tuple[str, dict[str, object] | None, str | None]] = []

    def fake_post(
        endpoint_or_target: str | startup.HttpEndpointTarget,
        target_or_payload: startup.HttpEndpointTarget | startup.JsonRpcResponse,
        payload: startup.JsonRpcResponse | None = None,
        *,
        session_id: str | None = None,
        post_fn: startup.HttpPostFn = httpx.post,
    ) -> tuple[startup.JsonRpcResponse, str | None]:
        del post_fn
        endpoint = cast("str", endpoint_or_target)
        assert isinstance(target_or_payload, startup.HttpEndpointTarget)
        calls.append((endpoint, payload, session_id))
        if payload and payload.get("method") == "initialize":
            return {"jsonrpc": "2.0", "result": {"ok": True}}, "session-1"
        if payload and payload.get("method") == "notifications/initialized":
            return {}, session_id
        return {"jsonrpc": "2.0", "result": {"tools": [{"name": "read_file"}]}}, session_id

    startup.preflight_http_attempt(
        "http://demo/mcp",
        startup.parse_http_endpoint("http://demo/mcp"),
        ["read_file"],
        datetime.timedelta(seconds=1),
        post_with_session_fn=fake_post,
    )

    assert len(calls) == expected_call_count


def test_preflight_http_attempt_fails_when_initialize_returns_jsonrpc_error() -> None:
    def fake_post(
        endpoint_or_target: str | startup.HttpEndpointTarget,
        target_or_payload: startup.HttpEndpointTarget | startup.JsonRpcResponse,
        payload: startup.JsonRpcResponse | None = None,
        *,
        session_id: str | None = None,
        post_fn: startup.HttpPostFn = httpx.post,
    ) -> tuple[startup.JsonRpcResponse, str | None]:
        del endpoint_or_target, target_or_payload, session_id, post_fn
        assert payload is not None
        if payload.get("method") == "initialize":
            return {"jsonrpc": "2.0", "error": {"code": -32000, "message": "boom"}}, "session-1"
        return {"jsonrpc": "2.0", "result": {"tools": [{"name": "read_file"}]}}, "session-1"

    with pytest.raises(startup.PermanentPreflightError, match="HTTP MCP initialize failed"):
        startup.preflight_http_attempt(
            "http://demo/mcp",
            startup.parse_http_endpoint("http://demo/mcp"),
            ["read_file"],
            datetime.timedelta(seconds=1),
            post_with_session_fn=fake_post,
        )


def test_preflight_http_attempt_fails_when_initialize_omits_session_id() -> None:
    def fake_post(
        endpoint_or_target: str | startup.HttpEndpointTarget,
        target_or_payload: startup.HttpEndpointTarget | startup.JsonRpcResponse,
        payload: startup.JsonRpcResponse | None = None,
        *,
        session_id: str | None = None,
        post_fn: startup.HttpPostFn = httpx.post,
    ) -> tuple[startup.JsonRpcResponse, str | None]:
        del endpoint_or_target, target_or_payload, session_id, post_fn
        assert payload is not None
        if payload.get("method") == "initialize":
            return {"jsonrpc": "2.0", "result": {"ok": True}}, None
        return {"jsonrpc": "2.0", "result": {"tools": [{"name": "read_file"}]}}, "session-1"

    with pytest.raises(startup.PermanentPreflightError, match="missing mcp-session-id"):
        startup.preflight_http_attempt(
            "http://demo/mcp",
            startup.parse_http_endpoint("http://demo/mcp"),
            ["read_file"],
            datetime.timedelta(seconds=1),
            post_with_session_fn=fake_post,
        )


def test_post_http_jsonrpc_with_session_accepts_202_empty_notification_response() -> None:
    def fake_post(
        url: str,
        *,
        json: startup.JsonRpcResponse,
        headers: dict[str, str],
        timeout: float,
    ) -> httpx.Response:
        del url, json, headers, timeout
        return httpx.Response(202, content=b"", headers={"mcp-session-id": "session-1"})

    response, session_id = startup.post_http_jsonrpc_with_session(
        "http://demo/mcp",
        startup.parse_http_endpoint("http://demo/mcp"),
        startup.initialized_notification(),
        session_id="session-1",
        post_fn=fake_post,
    )

    assert response == {}
    assert session_id == "session-1"


def test_read_legacy_sse_message_endpoint_rejects_absolute_cross_origin_url() -> None:
    lines = iter(["event: endpoint", "data: https://evil.invalid/message", ""])

    with pytest.raises(startup.PermanentPreflightError, match="cross-origin"):
        startup._read_legacy_sse_message_endpoint("http://demo.local/sse", lines)


def test_read_legacy_sse_message_endpoint_accepts_same_origin_relative_path() -> None:
    lines = iter(["event: endpoint", "data: /message?sessionId=abc123", ""])

    endpoint = startup._read_legacy_sse_message_endpoint("http://demo.local/sse", lines)

    assert endpoint == "http://demo.local/message?sessionId=abc123"


def test_heartbeat_policy_from_env_returns_default_when_unset() -> None:
    policy = startup.heartbeat_policy_from_env({})
    assert policy.interval == datetime.timedelta(milliseconds=2000)


def test_heartbeat_policy_from_env_reads_env_variable() -> None:
    policy = startup.heartbeat_policy_from_env({str(MCP_SUPERVISION_INTERVAL_MS_ENV): "500"})
    assert policy.interval == datetime.timedelta(milliseconds=500)


def test_heartbeat_policy_from_env_enforces_minimum_bound() -> None:
    policy = startup.heartbeat_policy_from_env({str(MCP_SUPERVISION_INTERVAL_MS_ENV): "5"})
    assert policy.interval == datetime.timedelta(milliseconds=100)


def test_heartbeat_policy_from_env_ignores_invalid_value() -> None:
    policy = startup.heartbeat_policy_from_env(
        {str(MCP_SUPERVISION_INTERVAL_MS_ENV): "not-a-number"}
    )
    assert policy.interval == datetime.timedelta(milliseconds=2000)
