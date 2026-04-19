from __future__ import annotations

import inspect
from importlib import import_module

import pytest

SECRET_MARKER = "super-secret-value"
EXPECTED_SEARCH_LIMIT_DEFAULT = 10


def _import_ddgs_module():
    try:
        return import_module("ralph.mcp.websearch.backends.ddgs")
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised in RED phase
        raise AssertionError("ralph.mcp.websearch.backends.ddgs should exist") from exc


def test_ddgs_backend_search_signature_matches_protocol() -> None:
    ddgs_module = _import_ddgs_module()

    signature = inspect.signature(ddgs_module.DdgsBackend.search)
    params = list(signature.parameters.values())

    assert [param.name for param in params] == ["self", "query", "limit"]
    assert params[2].kind is inspect.Parameter.KEYWORD_ONLY
    assert params[2].default == EXPECTED_SEARCH_LIMIT_DEFAULT


def test_ddgs_backend_creates_fresh_client_per_search(monkeypatch: pytest.MonkeyPatch) -> None:
    ddgs_module = _import_ddgs_module()
    init_calls: list[int] = []

    class FakeDDGS:
        def __init__(self) -> None:
            init_calls.append(len(init_calls) + 1)

        def text(self, query: str, max_results: int) -> list[dict[str, str]]:
            return [
                {
                    "title": f"{query} title {max_results}",
                    "href": "https://example.com/result",
                    "body": "snippet",
                }
            ]

    monkeypatch.setattr(ddgs_module, "DDGS", FakeDDGS)

    backend = ddgs_module.DdgsBackend()
    first = backend.search("alpha", limit=3)
    second = backend.search("beta", limit=2)

    assert init_calls == [1, 2]
    assert first[0].title == "alpha title 3"
    assert second[0].title == "beta title 2"
    assert first[0].url == "https://example.com/result"
    assert first[0].snippet == "snippet"


def test_ddgs_backend_raises_scrubbed_websearch_error(monkeypatch: pytest.MonkeyPatch) -> None:
    ddgs_module = _import_ddgs_module()

    class BrokenDDGS:
        def text(self, query: str, max_results: int) -> list[dict[str, str]]:
            raise RuntimeError(f"boom {SECRET_MARKER} for {query} {max_results}")

    monkeypatch.setattr(ddgs_module, "DDGS", BrokenDDGS)
    backend = ddgs_module.DdgsBackend()

    with pytest.raises(ddgs_module.WebSearchError) as exc_info:
        backend.search("query", limit=5)

    message = str(exc_info.value)
    assert "ddgs" in message.lower()
    assert SECRET_MARKER not in message
    assert "query" not in message


def test_ddgs_backend_normalizes_sparse_result_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    ddgs_module = _import_ddgs_module()

    class FakeDDGS:
        def text(self, query: str, max_results: int) -> list[dict[str, str]]:
            return [
                {
                    "title": "Example title",
                    "url": "https://example.com/a",
                    "description": "Example snippet",
                }
            ]

    monkeypatch.setattr(ddgs_module, "DDGS", FakeDDGS)

    backend = ddgs_module.DdgsBackend()
    results = backend.search("alpha", limit=1)

    assert len(results) == 1
    assert results[0].title == "Example title"
    assert results[0].url == "https://example.com/a"
    assert results[0].snippet == "Example snippet"
