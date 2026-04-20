from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from loguru import logger

from ralph.config.mcp_models import WebSearchConfig
from ralph.mcp.tools import websearch as tool_websearch
from ralph.mcp.tools.coordination import ToolResult
from ralph.mcp.tools.websearch import _MAX_LIMIT, _MIN_LIMIT
from ralph.mcp.websearch.backends.base import SearchResult, WebSearchError

if TYPE_CHECKING:
    import pytest


class _AllowedSession:
    session_id = "test-session"

    def check_capability(self, capability: str) -> object:
        return "approved"


class _DeniedSession:
    session_id = "denied-session"

    def check_capability(self, capability: str) -> object:
        return "denied"


class _StubWorkspace:
    def absolute_path(self, path: str) -> str:
        return path


_GOOD_RESULTS = [
    SearchResult(title="Result One", url="https://example.com/1", snippet="Snippet one."),
    SearchResult(title="Result Two", url="https://example.com/2", snippet="Snippet two."),
]


def _make_config(
    backend: str = "ddgs",
    fallback: list[str] | None = None,
) -> WebSearchConfig:
    return WebSearchConfig(backend=backend, fallback=fallback or [])


def test_handle_web_search_ddgs_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_backend = MagicMock()
    mock_backend.search.return_value = _GOOD_RESULTS
    monkeypatch.setattr(tool_websearch, "_build_backend", lambda name, cfg: mock_backend)

    config = _make_config(backend="ddgs")
    result = tool_websearch.handle_web_search(
        _AllowedSession(),
        _StubWorkspace(),
        {"query": "python mcp"},
        web_search_config=config,
    )

    assert isinstance(result, ToolResult)
    assert result.is_error is False
    assert result.content
    combined = " ".join(c.text for c in result.content)
    assert "Result One" in combined
    assert "https://example.com/1" in combined


def test_fallback_chain_advances_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    failing_backend = MagicMock()
    failing_backend.search.side_effect = WebSearchError("primary failed")

    fallback_backend = MagicMock()
    fallback_backend.search.return_value = _GOOD_RESULTS

    call_order: list[str] = []

    def _factory(name: str, cfg: WebSearchConfig) -> object:
        call_order.append(name)
        if name == "ddgs":
            return failing_backend
        return fallback_backend

    monkeypatch.setattr(tool_websearch, "_build_backend", _factory)

    config = _make_config(backend="ddgs", fallback=["tavily"])
    result = tool_websearch.handle_web_search(
        _AllowedSession(),
        _StubWorkspace(),
        {"query": "fallback test"},
        web_search_config=config,
    )

    assert result.is_error is False
    assert "ddgs" in call_order
    assert "tavily" in call_order
    combined = " ".join(c.text for c in result.content)
    assert "Result One" in combined


def test_all_backends_fail_returns_is_error_true(monkeypatch: pytest.MonkeyPatch) -> None:
    failing = MagicMock()
    failing.search.side_effect = WebSearchError("all down")
    monkeypatch.setattr(tool_websearch, "_build_backend", lambda name, cfg: failing)

    config = _make_config(backend="ddgs", fallback=["tavily"])
    result = tool_websearch.handle_web_search(
        _AllowedSession(),
        _StubWorkspace(),
        {"query": "everything broken"},
        web_search_config=config,
    )

    assert result.is_error is True
    combined = " ".join(c.text for c in result.content)
    assert "all" in combined.lower() and "fail" in combined.lower()


def test_missing_query_param_returns_error() -> None:
    result = tool_websearch.handle_web_search(
        _AllowedSession(),
        _StubWorkspace(),
        {},
        web_search_config=_make_config(),
    )

    assert result.is_error is True


def test_limit_bounds_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_limits: list[int] = []

    def _capturing_backend(name: str, cfg: WebSearchConfig) -> object:
        b = MagicMock()

        def _search(query: str, *, limit: int = 10) -> list[SearchResult]:
            captured_limits.append(limit)
            return _GOOD_RESULTS

        b.search.side_effect = _search
        return b

    monkeypatch.setattr(tool_websearch, "_build_backend", _capturing_backend)

    tool_websearch.handle_web_search(
        _AllowedSession(),
        _StubWorkspace(),
        {"query": "test", "limit": 100},
        web_search_config=_make_config(),
    )
    tool_websearch.handle_web_search(
        _AllowedSession(),
        _StubWorkspace(),
        {"query": "test", "limit": 0},
        web_search_config=_make_config(),
    )

    assert captured_limits[0] == _MAX_LIMIT, f"Expected {_MAX_LIMIT}, got {captured_limits[0]}"
    assert captured_limits[1] == _MIN_LIMIT, f"Expected {_MIN_LIMIT}, got {captured_limits[1]}"


def test_query_not_in_warning_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    secret_query = "top secret research topic 12345"

    failing = MagicMock()
    failing.search.side_effect = WebSearchError("backend error")
    monkeypatch.setattr(tool_websearch, "_build_backend", lambda name, cfg: failing)

    records: list[str] = []
    sink_id = logger.add(lambda msg: records.append(str(msg)), level="WARNING")
    try:
        tool_websearch.handle_web_search(
            _AllowedSession(),
            _StubWorkspace(),
            {"query": secret_query},
            web_search_config=_make_config(backend="ddgs"),
        )
    finally:
        logger.remove(sink_id)

    for record in records:
        assert secret_query not in record, f"Secret query found in log record: {record!r}"


def test_capability_denied_when_session_missing_web_search() -> None:
    result = tool_websearch.handle_web_search(
        _DeniedSession(),
        _StubWorkspace(),
        {"query": "test"},
        web_search_config=_make_config(),
    )

    assert result.is_error is True
