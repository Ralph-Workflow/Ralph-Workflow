from __future__ import annotations

import os
from importlib import import_module

import pytest

from tests.mcp.websearch.test_searxng_backend_helper__explodingresponse import _ExplodingResponse


def _import_searxng_module() -> object:
    try:
        return import_module("ralph.mcp.websearch.backends.searxng")
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised in RED phase
        raise AssertionError("ralph.mcp.websearch.backends.searxng should exist") from exc


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


def test_search_result_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    searxng = _import_searxng_module()

    def fake_post(url: str, *, data: dict[str, object], timeout: float) -> _FakeResponse:
        assert url == "https://searx.example/search"
        assert data == {"q": "python", "format": "json"}
        assert timeout > 0
        return _FakeResponse(
            {
                "results": [
                    {
                        "title": "Python",
                        "url": "https://www.python.org/",
                        "content": "The Python programming language.",
                    },
                    {"title": "skip missing url", "content": "ignored"},
                ]
            }
        )

    monkeypatch.setattr(searxng.httpx, "post", fake_post)

    results = searxng.SearxngBackend(url="https://searx.example").search("python", limit=2)

    assert results == [
        searxng.SearchResult(
            title="Python",
            url="https://www.python.org/",
            snippet="The Python programming language.",
        )
    ]


def test_search_failure_raises_scrubbed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    searxng = _import_searxng_module()
    query = "private search terms"

    def fake_post(url: str, *, data: dict[str, object], timeout: float) -> _ExplodingResponse:
        return _ExplodingResponse(f"boom leaked-token for {data['q']} at {url}")

    monkeypatch.setattr(searxng.httpx, "post", fake_post)

    with pytest.raises(searxng.WebSearchError) as exc_info:
        searxng.SearxngBackend(url="https://searx.example").search(query)

    message = str(exc_info.value)
    assert "searxng" in message.lower()
    assert "leaked-token" not in message
    assert query not in message


def test_live_search_returns_results_when_searxng_url_configured() -> None:
    pytest.importorskip("httpx")
    searxng = _import_searxng_module()

    searxng_url = os.environ.get("SEARXNG_URL")
    if not searxng_url:
        pytest.skip("SEARXNG_URL is not set")

    backend = searxng.SearxngBackend(url=searxng_url)
    results = backend.search("python mcp", limit=3)

    assert results
    assert all(result.title and result.url for result in results)
