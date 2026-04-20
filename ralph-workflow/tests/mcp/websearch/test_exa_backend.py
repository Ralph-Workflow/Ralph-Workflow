from __future__ import annotations

import os
import sys
from importlib import import_module
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest
from _pytest.mark import Mark, MarkDecorator

NETWORK_MARK = MarkDecorator(Mark("network", (), {}))

API_KEY = "exa-secret-key"
ENV_NAME = "EXA_API_KEY"
SEARCH_LIMIT = 2
IMPORT_ERROR_MATCH = "pip install ralph-workflow\\[web-search\\]"


def _import_exa_module():
    try:
        return import_module("ralph.mcp.websearch.backends.exa")
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised in RED phase
        raise AssertionError("ralph.mcp.websearch.backends.exa should exist") from exc


def test_search_result_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    exa_backend = _import_exa_module()

    class FakeExa:
        def __init__(self, *, api_key: str) -> None:
            assert api_key == API_KEY

        def search(self, query: str, *, num_results: int):
            assert query == "python"
            assert num_results == SEARCH_LIMIT
            return SimpleNamespace(
                results=[
                    SimpleNamespace(
                        title="Python",
                        url="https://www.python.org/",
                        text="Programming language",
                    ),
                    SimpleNamespace(title="skip me", url="", text="missing url"),
                ]
            )

    fake_module = cast("Any", ModuleType("exa_py"))
    fake_module.Exa = FakeExa
    monkeypatch.setitem(sys.modules, "exa_py", fake_module)

    results = exa_backend.ExaBackend(api_key=API_KEY).search("python", limit=SEARCH_LIMIT)

    assert results == [
        exa_backend.SearchResult(
            title="Python",
            url="https://www.python.org/",
            snippet="Programming language",
        )
    ]


def test_error_does_not_leak_key(monkeypatch: pytest.MonkeyPatch) -> None:
    exa_backend = _import_exa_module()

    class BrokenExa:
        def __init__(self, *, api_key: str) -> None:
            self.api_key = api_key

        def search(self, query: str, *, num_results: int):
            raise RuntimeError(f"401 unauthorized api_key={self.api_key} query={query}")

    fake_module = cast("Any", ModuleType("exa_py"))
    fake_module.Exa = BrokenExa
    monkeypatch.setitem(sys.modules, "exa_py", fake_module)

    with pytest.raises(exa_backend.WebSearchError) as exc_info:
        exa_backend.ExaBackend(api_key=API_KEY).search("private query", limit=1)

    message = str(exc_info.value)
    assert "exa" in message.lower()
    assert API_KEY not in message
    assert "private query" not in message


def test_import_error_message_points_to_extras(monkeypatch: pytest.MonkeyPatch) -> None:
    exa_backend = _import_exa_module()

    monkeypatch.delitem(sys.modules, "exa_py", raising=False)
    monkeypatch.setattr(
        exa_backend,
        "import_module",
        lambda name: (
            (_ for _ in ()).throw(ImportError("missing exa"))
            if name == "exa_py"
            else import_module(name)
        ),
    )

    with pytest.raises(exa_backend.WebSearchError, match=IMPORT_ERROR_MATCH):
        exa_backend.ExaBackend(api_key=API_KEY).search("python")


@NETWORK_MARK
@pytest.mark.skipif(not os.environ.get(ENV_NAME), reason=f"{ENV_NAME} is not set")
def test_live_search_returns_results() -> None:
    exa_backend = _import_exa_module()

    results = exa_backend.ExaBackend(api_key_env=ENV_NAME).search("python mcp", limit=3)

    assert results
    assert all(result.title and result.url for result in results)
