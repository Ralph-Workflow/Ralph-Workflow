"""Tests for the MCP startup port."""

from __future__ import annotations

import datetime
import errno

import pytest

from ralph.mcp import startup
from ralph.mcp.capability_mapping import AccessMode, SessionDrain


def test_access_mode_for_drain_planning_is_read_only() -> None:
    assert startup.access_mode_for_drain(SessionDrain.PLANNING) is AccessMode.READ_ONLY


def test_access_mode_for_drain_development_allows_write() -> None:
    assert startup.access_mode_for_drain(SessionDrain.DEVELOPMENT) is AccessMode.READ_WRITE


def test_access_mode_for_drain_accepts_string_alias() -> None:
    assert startup.access_mode_for_drain("fix") is AccessMode.READ_WRITE


def test_access_mode_for_development_analysis_is_read_only() -> None:
    assert startup.access_mode_for_drain("development_analysis") is AccessMode.READ_ONLY


def test_access_mode_for_development_commit_is_read_only() -> None:
    assert startup.access_mode_for_drain("development_commit") is AccessMode.READ_ONLY


def test_access_mode_for_review_analysis_is_read_only() -> None:
    assert startup.access_mode_for_drain("review_analysis") is AccessMode.READ_ONLY


def test_access_mode_for_review_commit_is_read_only() -> None:
    assert startup.access_mode_for_drain("review_commit") is AccessMode.READ_ONLY


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
    monkeypatch.delenv("RALPH_MCP_PREFLIGHT_TIMEOUT_MS", raising=False)
    expected = datetime.timedelta(milliseconds=30_000)
    assert startup.mcp_preflight_timeout_from_env() == expected
