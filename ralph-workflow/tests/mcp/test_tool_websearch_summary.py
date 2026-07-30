"""Phase 4: tests for ``format='summary'`` in ``handle_web_search``.

Tests are colocated under ``tests/mcp/`` to match the existing web
test layout (``test_tool_websearch.py``). They stub
``ralph.mcp.tools.websearch.build_backend`` so the handler runs
end-to-end without any real network call.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.config.mcp_models import WebSearchConfig
from ralph.mcp.tools import websearch as tool_websearch
from ralph.mcp.tools.coordination import ToolResult
from ralph.mcp.websearch.backends.base import SearchResult, WebSearchError

if TYPE_CHECKING:
    import pytest

from tests.mcp.test_tool_websearch_helper__stubworkspace import _StubWorkspace


class _AllowedSession:
    session_id = "test-session"

    def check_capability(self, capability: str) -> object:
        return "approved"


_GOOD_RESULTS = [
    SearchResult(title="Result One", url="https://example.com/1", snippet="Snippet one."),
    SearchResult(
        title="Result Two",
        url="https://example.com/2",
        snippet="Snippet two is a bit longer. " * 20,
    ),
]


def _make_config(
    backend: str = "ddgs",
    fallback: list[str] | None = None,
) -> WebSearchConfig:
    return WebSearchConfig(backend=backend, fallback=fallback or [])


def test_format_summary_returns_compact_cards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_backend = MagicMock()
    mock_backend.search.return_value = _GOOD_RESULTS
    monkeypatch.setattr(
        tool_websearch, "build_backend", lambda name, cfg: mock_backend
    )

    config = _make_config(backend="ddgs")
    result = tool_websearch.handle_web_search(
        _AllowedSession(),
        _StubWorkspace(),
        {"query": "python mcp", "format": "summary"},
        web_search_config=config,
    )

    assert isinstance(result, ToolResult)
    assert result.is_error is False
    envelope = json.loads(result.content[0].text)
    assert envelope["format"] == "summary"
    assert envelope["query_length"] == len("python mcp")
    assert envelope["result_count"] == 2
    assert envelope["backend_chain_used"] == ["ddgs"]
    assert envelope["bytes_in"] > 0
    assert envelope["bytes_out"] > 0
    titles = [card["title"] for card in envelope["results"]]
    assert titles == ["Result One", "Result Two"]
    # Long snippet must be truncated to ``SUMMARY_SNIPPET_MAX_CHARS`` + ellipsis.
    long_snippet = envelope["results"][1]["snippet"]
    assert len(long_snippet) <= tool_websearch.SUMMARY_SNIPPET_MAX_CHARS + len("...")


def test_format_raw_default_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """``format='raw'`` (default) preserves the legacy Title/URL/Snippet text."""
    mock_backend = MagicMock()
    mock_backend.search.return_value = _GOOD_RESULTS
    monkeypatch.setattr(
        tool_websearch, "build_backend", lambda name, cfg: mock_backend
    )

    config = _make_config(backend="ddgs")
    result = tool_websearch.handle_web_search(
        _AllowedSession(),
        _StubWorkspace(),
        {"query": "python mcp"},
        web_search_config=config,
    )

    assert isinstance(result, ToolResult)
    assert result.is_error is False
    text = result.content[0].text
    # Legacy shape: Title / URL / Snippet blocks joined by blank lines.
    assert "Title: Result One" in text
    assert "URL: https://example.com/1" in text
    assert "Snippet: Snippet one." in text


def test_format_invalid_value_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unknown ``format`` value returns an explicit is_error result."""
    mock_backend = MagicMock()
    mock_backend.search.return_value = _GOOD_RESULTS
    monkeypatch.setattr(
        tool_websearch, "build_backend", lambda name, cfg: mock_backend
    )

    config = _make_config(backend="ddgs")
    result = tool_websearch.handle_web_search(
        _AllowedSession(),
        _StubWorkspace(),
        {"query": "python mcp", "format": "bogus"},
        web_search_config=config,
    )
    assert result.is_error is True
    assert "Invalid web_search format" in result.content[0].text


def test_format_summary_records_backend_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``backend_chain_used`` records every backend that was queried
    before one returned results, including fallbacks."""
    failing_backend = MagicMock()
    failing_backend.search.side_effect = WebSearchError("primary failed")

    fallback_backend = MagicMock()
    fallback_backend.search.return_value = _GOOD_RESULTS

    def _factory(name: str, cfg: WebSearchConfig) -> object:
        if name == "ddgs":
            return failing_backend
        return fallback_backend

    monkeypatch.setattr(tool_websearch, "build_backend", _factory)

    config = _make_config(backend="ddgs", fallback=["tavily"])
    result = tool_websearch.handle_web_search(
        _AllowedSession(),
        _StubWorkspace(),
        {"query": "fallback test", "format": "summary"},
        web_search_config=config,
    )
    assert result.is_error is False
    envelope = json.loads(result.content[0].text)
    assert envelope["backend_chain_used"] == ["tavily"]


# AC-06 / analysis-feedback regression: see the prior fix in
# ``git_read.py``. ``_format_summary_envelope`` previously
# computed ``bytes_out`` from the envelope BEFORE adding the
# field, so the declared counter was smaller than the actually
# returned text. This test pins the new convention.
def test_format_summary_bytes_out_matches_actual_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_backend = MagicMock()
    mock_backend.search.return_value = _GOOD_RESULTS
    monkeypatch.setattr(
        tool_websearch, "build_backend", lambda name, cfg: mock_backend
    )
    config = _make_config(backend="ddgs")
    result = tool_websearch.handle_web_search(
        _AllowedSession(),
        _StubWorkspace(),
        {"query": "python mcp", "format": "summary"},
        web_search_config=config,
    )
    envelope = json.loads(result.content[0].text)
    assert envelope["bytes_out"] == len(result.content[0].text.encode("utf-8"))


# AC-06 / analysis-feedback regression: ``snippet_budget_bytes``
# previously used ``len(snippet)`` (character count) instead of
# ``len(snippet.encode("utf-8"))``. Multi-byte snippets (CJK,
# emoji) undercounted by 2-4x and broke byte-budget planning.
# This test pins the UTF-8 byte count convention with a 4-byte
# emoji that spans 4 UTF-8 bytes per code point.
def test_format_summary_snippet_budget_bytes_is_utf8(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    multibyte_snippet = "\U0001f600\U0001f601\U0001f602"
    multibyte_results = [
        SearchResult(
            title="Unicode",
            url="https://example.com/u",
            snippet=multibyte_snippet,
        ),
    ]
    mock_backend = MagicMock()
    mock_backend.search.return_value = multibyte_results
    monkeypatch.setattr(
        tool_websearch, "build_backend", lambda name, cfg: mock_backend
    )
    config = _make_config(backend="ddgs")
    result = tool_websearch.handle_web_search(
        _AllowedSession(),
        _StubWorkspace(),
        {"query": "unicode", "format": "summary"},
        web_search_config=config,
    )
    envelope = json.loads(result.content[0].text)
    card = envelope["results"][0]
    assert card["snippet_budget_bytes"] == len(multibyte_snippet.encode("utf-8"))
    assert card["snippet_budget_bytes"] != len(multibyte_snippet)
