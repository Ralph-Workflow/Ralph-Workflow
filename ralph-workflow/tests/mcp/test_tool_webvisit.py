from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from loguru import logger

from ralph.config.mcp_models import WebVisitConfig
from ralph.mcp.tools import webvisit as tool_webvisit
from ralph.mcp.tools.coordination import ToolResult
from ralph.mcp.webvisit.extractor import ExtractedPage
from ralph.mcp.webvisit.fetcher import FetchOutcome

if TYPE_CHECKING:
    import pytest

_HTTP_404 = 404


class _StubWorkspace:

    class _AllowedSession:
        session_id = "test-session"

        def check_capability(self, capability: str) -> object:
            return "approved"

    class _DeniedSession:
        session_id = "denied-session"

        def check_capability(self, capability: str) -> object:
            return "denied"

    def absolute_path(self, path: str) -> str:
        return path


_AllowedSession = _StubWorkspace._AllowedSession
_DeniedSession = _StubWorkspace._DeniedSession


_GOOD_OUTCOME = FetchOutcome(
    status="ok",
    effective_url="https://example.com/page",
    http_status=200,
    content_type="text/html; charset=utf-8",
    body=b"<html><body><p>Hello world</p></body></html>",
)

_GOOD_PAGE = ExtractedPage(
    title="Hello",
    text="Hello world",
    links=("https://example.com/link",),
)


def _make_config(**overrides: object) -> WebVisitConfig:
    data: dict[str, object] = {
        "enabled": True,
        "timeout_ms": 15000,
        "max_bytes": 2_097_152,
        "user_agent": "RalphWorkflow/1.0",
        "allow_private_networks": False,
        "extract_links": False,
    }
    data.update(overrides)
    return WebVisitConfig.model_validate(data)


def test_handle_visit_url_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool_webvisit, "fetch_url", lambda *a, **kw: _GOOD_OUTCOME)
    monkeypatch.setattr(tool_webvisit, "extract_readable", lambda *a, **kw: _GOOD_PAGE)

    result = tool_webvisit.handle_visit_url(
        _AllowedSession(),
        _StubWorkspace(),
        {"url": "https://example.com/page"},
        web_visit_config=_make_config(),
    )

    assert isinstance(result, ToolResult)
    assert result.is_error is False
    data = json.loads(result.content[0].text)
    assert data["status"] == "ok"
    assert data["title"] == "Hello"
    assert "Hello world" in data["text"]
    assert "links" not in data


def test_with_links_included_in_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool_webvisit, "fetch_url", lambda *a, **kw: _GOOD_OUTCOME)
    monkeypatch.setattr(tool_webvisit, "extract_readable", lambda *a, **kw: _GOOD_PAGE)

    result = tool_webvisit.handle_visit_url(
        _AllowedSession(),
        _StubWorkspace(),
        {"url": "https://example.com/page", "with_links": True},
        web_visit_config=_make_config(),
    )

    assert result.is_error is False
    data = json.loads(result.content[0].text)
    assert "links" in data
    assert "https://example.com/link" in data["links"]


def test_missing_url_param_returns_error() -> None:
    result = tool_webvisit.handle_visit_url(
        _AllowedSession(),
        _StubWorkspace(),
        {},
        web_visit_config=_make_config(),
    )

    assert result.is_error is True


def test_capability_denied_returns_error() -> None:
    result = tool_webvisit.handle_visit_url(
        _DeniedSession(),
        _StubWorkspace(),
        {"url": "https://example.com/page"},
        web_visit_config=_make_config(),
    )

    assert result.is_error is True


def test_fetch_timeout_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    timeout_outcome = FetchOutcome(status="timeout", error="request timed out")
    monkeypatch.setattr(tool_webvisit, "fetch_url", lambda *a, **kw: timeout_outcome)

    result = tool_webvisit.handle_visit_url(
        _AllowedSession(),
        _StubWorkspace(),
        {"url": "https://example.com/page"},
        web_visit_config=_make_config(),
    )

    assert result.is_error is True
    data = json.loads(result.content[0].text)
    assert data["status"] == "timeout"


def test_fetch_blocked_by_policy_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    blocked_outcome = FetchOutcome(
        status="blocked_by_policy",
        error="access to private/loopback networks is disabled",
    )
    monkeypatch.setattr(tool_webvisit, "fetch_url", lambda *a, **kw: blocked_outcome)

    result = tool_webvisit.handle_visit_url(
        _AllowedSession(),
        _StubWorkspace(),
        {"url": "http://localhost/secret"},
        web_visit_config=_make_config(),
    )

    assert result.is_error is True
    data = json.loads(result.content[0].text)
    assert data["status"] == "blocked_by_policy"


def test_http_error_status_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    error_outcome = FetchOutcome(
        status="http_error",
        effective_url="https://example.com/page",
        http_status=_HTTP_404,
        error=f"HTTP {_HTTP_404}",
    )
    monkeypatch.setattr(tool_webvisit, "fetch_url", lambda *a, **kw: error_outcome)

    result = tool_webvisit.handle_visit_url(
        _AllowedSession(),
        _StubWorkspace(),
        {"url": "https://example.com/page"},
        web_visit_config=_make_config(),
    )

    assert result.is_error is True
    data = json.loads(result.content[0].text)
    assert data["status"] == "http_error"
    assert data["http_status"] == _HTTP_404


def test_unsupported_content_type_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    unsupported_outcome = FetchOutcome(
        status="unsupported_content",
        effective_url="https://example.com/file.pdf",
        http_status=200,
        content_type="application/pdf",
        error="unsupported content type: 'application/pdf'",
    )
    monkeypatch.setattr(tool_webvisit, "fetch_url", lambda *a, **kw: unsupported_outcome)

    result = tool_webvisit.handle_visit_url(
        _AllowedSession(),
        _StubWorkspace(),
        {"url": "https://example.com/file.pdf"},
        web_visit_config=_make_config(),
    )

    assert result.is_error is True
    data = json.loads(result.content[0].text)
    assert data["status"] == "unsupported_content"


def test_missing_optional_deps_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool_webvisit, "fetch_url", lambda *a, **kw: _GOOD_OUTCOME)
    monkeypatch.setattr(
        tool_webvisit,
        "extract_readable",
        MagicMock(side_effect=ImportError("missing dep")),
    )

    result = tool_webvisit.handle_visit_url(
        _AllowedSession(),
        _StubWorkspace(),
        {"url": "https://example.com/page"},
        web_visit_config=_make_config(),
    )

    assert result.is_error is True
    data = json.loads(result.content[0].text)
    assert data["status"] == "unsupported_content"


def test_url_not_in_warning_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    secret_url = "https://internal-secret-url-98765.example.com/private"
    timeout_outcome = FetchOutcome(status="timeout", error="timed out")
    monkeypatch.setattr(tool_webvisit, "fetch_url", lambda *a, **kw: timeout_outcome)

    records: list[str] = []
    sink_id = logger.add(lambda msg: records.append(str(msg)), level="WARNING")
    try:
        tool_webvisit.handle_visit_url(
            _AllowedSession(),
            _StubWorkspace(),
            {"url": secret_url},
            web_visit_config=_make_config(),
        )
    finally:
        logger.remove(sink_id)

    for record in records:
        assert secret_url not in record, f"Secret URL found in log record: {record!r}"


def test_text_truncated_to_max_bytes_divided_by_4(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _make_config(max_bytes=100)
    long_text = "A" * 200
    long_page = ExtractedPage(title="T", text=long_text, links=())
    monkeypatch.setattr(tool_webvisit, "fetch_url", lambda *a, **kw: _GOOD_OUTCOME)
    monkeypatch.setattr(tool_webvisit, "extract_readable", lambda *a, **kw: long_page)

    result = tool_webvisit.handle_visit_url(
        _AllowedSession(),
        _StubWorkspace(),
        {"url": "https://example.com/page"},
        web_visit_config=config,
    )

    assert result.is_error is False
    data = json.loads(result.content[0].text)
    assert len(data["text"]) <= 100 // 4


def test_fetch_unreachable_returns_structured_error(monkeypatch: pytest.MonkeyPatch) -> None:
    unreachable_outcome = FetchOutcome(
        status="unreachable",
        error="All connection attempts failed",
    )
    monkeypatch.setattr(tool_webvisit, "fetch_url", lambda *a, **kw: unreachable_outcome)

    result = tool_webvisit.handle_visit_url(
        _AllowedSession(),
        _StubWorkspace(),
        {"url": "https://example.com/page"},
        web_visit_config=_make_config(),
    )

    assert result.is_error is True
    data = json.loads(result.content[0].text)
    assert data["status"] == "unreachable"
    assert "error" in data


def test_fetch_too_large_returns_structured_error(monkeypatch: pytest.MonkeyPatch) -> None:
    too_large_outcome = FetchOutcome(
        status="too_large",
        effective_url="https://example.com/huge",
        error="response body exceeds 2097152 bytes",
    )
    monkeypatch.setattr(tool_webvisit, "fetch_url", lambda *a, **kw: too_large_outcome)

    result = tool_webvisit.handle_visit_url(
        _AllowedSession(),
        _StubWorkspace(),
        {"url": "https://example.com/huge"},
        web_visit_config=_make_config(),
    )

    assert result.is_error is True
    data = json.loads(result.content[0].text)
    assert data["status"] == "too_large"


def test_fetch_invalid_url_returns_structured_error(monkeypatch: pytest.MonkeyPatch) -> None:
    invalid_outcome = FetchOutcome(
        status="invalid_url",
        error="unsupported scheme: 'ftp'",
    )
    monkeypatch.setattr(tool_webvisit, "fetch_url", lambda *a, **kw: invalid_outcome)

    result = tool_webvisit.handle_visit_url(
        _AllowedSession(),
        _StubWorkspace(),
        {"url": "ftp://example.com/file"},
        web_visit_config=_make_config(),
    )

    assert result.is_error is True
    data = json.loads(result.content[0].text)
    assert data["status"] == "invalid_url"
