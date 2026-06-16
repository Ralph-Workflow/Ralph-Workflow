from __future__ import annotations

import builtins
import os
import sys
import threading
from importlib import import_module
from types import ModuleType
from typing import Any, cast

import pytest
from _pytest.mark import Mark, MarkDecorator

NETWORK_MARK = MarkDecorator(Mark("network", (), {}))

API_KEY = "tvly-secret-key"
ENV_NAME = "TAVILY_API_KEY"
SEARCH_LIMIT = 2
IMPORT_ERROR_MATCH = "pip install ralph-workflow\\[web-search\\]"


def _import_tavily_module() -> object:
    try:
        return import_module("ralph.mcp.websearch.backends.tavily")
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised in RED phase
        raise AssertionError("ralph.mcp.websearch.backends.tavily should exist") from exc


def test_search_result_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    tavily_backend = _import_tavily_module()

    class FakeClient:
        def __init__(self, *, api_key: str) -> None:
            assert api_key == API_KEY

        def search(self, query: str, *, max_results: int) -> dict[str, object]:
            assert query == "python"
            assert max_results == SEARCH_LIMIT
            return {
                "results": [
                    {
                        "title": "Python",
                        "url": "https://www.python.org/",
                        "content": "Programming language",
                    },
                    {"title": "skip me", "content": "missing url"},
                ]
            }

    fake_module = cast("Any", ModuleType("tavily"))
    fake_module.TavilyClient = FakeClient
    monkeypatch.setitem(sys.modules, "tavily", fake_module)

    results = tavily_backend.TavilyBackend(api_key=API_KEY).search("python", limit=SEARCH_LIMIT)

    assert results == [
        tavily_backend.SearchResult(
            title="Python",
            url="https://www.python.org/",
            snippet="Programming language",
        )
    ]


def test_401_does_not_leak_key(monkeypatch: pytest.MonkeyPatch) -> None:
    tavily_backend = _import_tavily_module()

    class BrokenClient:
        def __init__(self, *, api_key: str) -> None:
            self.api_key = api_key

        def search(self, query: str, *, max_results: int) -> dict[str, object]:
            raise RuntimeError(f"401 unauthorized api_key={self.api_key} query={query}")

    fake_module = cast("Any", ModuleType("tavily"))
    fake_module.TavilyClient = BrokenClient
    monkeypatch.setitem(sys.modules, "tavily", fake_module)

    with pytest.raises(tavily_backend.WebSearchError) as exc_info:
        tavily_backend.TavilyBackend(api_key=API_KEY).search("private query", limit=1)

    message = str(exc_info.value)
    assert "tavily" in message.lower()
    assert API_KEY not in message
    assert "private query" not in message


def test_import_error_message_points_to_extras(monkeypatch: pytest.MonkeyPatch) -> None:
    tavily_backend = _import_tavily_module()
    original_import = builtins.__import__

    def fake_import(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "tavily":
            raise ImportError("missing tavily")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.delitem(sys.modules, "tavily", raising=False)
    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(tavily_backend.WebSearchError, match=IMPORT_ERROR_MATCH):
        tavily_backend.TavilyBackend(api_key=API_KEY).search("python")


@NETWORK_MARK
@pytest.mark.skipif(not os.environ.get(ENV_NAME), reason=f"{ENV_NAME} is not set")
def test_live_search_returns_results() -> None:
    tavily_backend = _import_tavily_module()

    results = tavily_backend.TavilyBackend(api_key_env=ENV_NAME).search("python mcp", limit=3)

    assert results
    assert all(result.title and result.url for result in results)


def test_tavily_backend_bounded_by_with_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    tavily_backend = _import_tavily_module()
    bounded = import_module("ralph.mcp.websearch._bounded_sdk_call")
    bounded.reset_default()
    event = threading.Event()

    class HangingTavily:
        def __init__(self, *, api_key: str) -> None:
            self._api_key = api_key

        def search(self, query: str, *, max_results: int) -> dict[str, object]:
            event.wait(timeout=10.0)
            return {"results": []}

    fake_module = cast("Any", ModuleType("tavily"))
    fake_module.TavilyClient = HangingTavily
    monkeypatch.setitem(sys.modules, "tavily", fake_module)
    try:
        backend = tavily_backend.TavilyBackend(api_key=API_KEY, timeout_seconds=0.05)
        with pytest.raises(bounded.WebSearchError) as exc_info:
            backend.search("q")
    finally:
        event.set()
        bounded.reset_default()
    assert "tavily" in str(exc_info.value)
    assert "0.05" in str(exc_info.value)
