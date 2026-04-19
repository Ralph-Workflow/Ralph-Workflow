from __future__ import annotations

import builtins
import os
import sys
from importlib import import_module
from types import ModuleType

import pytest
from _pytest.mark import Mark, MarkDecorator

NETWORK_MARK = MarkDecorator(Mark("network", (), {}))

API_KEY = "brave-secret-key"
ENV_NAME = "BRAVE_SEARCH_API_KEY"
CLIENT_MODULE_NAME = "brave_search_python_client"
IMPORT_ERROR_MATCH = "pip install ralph-workflow\\[web-search\\]"


def _import_brave_module():
    try:
        return import_module("ralph.mcp.websearch.backends.brave")
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised in RED phase
        raise AssertionError("ralph.mcp.websearch.backends.brave should exist") from exc


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class _ExplodingResponse:
    def __init__(self, detail: str) -> None:
        self.detail = detail

    def raise_for_status(self) -> None:
        raise RuntimeError(self.detail)


def test_search_result_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    brave_backend = _import_brave_module()
    monkeypatch.setitem(
        sys.modules, CLIENT_MODULE_NAME, ModuleType(CLIENT_MODULE_NAME)
    )

    def fake_get(
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, object],
        timeout: float,
    ) -> _FakeResponse:
        assert url == brave_backend._DEFAULT_URL
        assert headers["X-Subscription-Token"] == API_KEY
        assert params == {"q": "python", "count": 2}
        assert timeout > 0
        return _FakeResponse(
            {
                "web": {
                    "results": [
                        {
                            "title": "Python",
                            "url": "https://www.python.org/",
                            "description": "Programming language",
                        },
                        {"title": "skip me", "description": "missing url"},
                    ]
                }
            }
        )

    monkeypatch.setattr(brave_backend.httpx, "get", fake_get)

    results = brave_backend.BraveBackend(api_key=API_KEY).search("python", limit=2)

    assert results == [
        brave_backend.SearchResult(
            title="Python",
            url="https://www.python.org/",
            snippet="Programming language",
        )
    ]


def test_error_does_not_leak_key(monkeypatch: pytest.MonkeyPatch) -> None:
    brave_backend = _import_brave_module()
    monkeypatch.setitem(
        sys.modules, CLIENT_MODULE_NAME, ModuleType(CLIENT_MODULE_NAME)
    )

    def fake_get(
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, object],
        timeout: float,
    ) -> _ExplodingResponse:
        token = headers["X-Subscription-Token"]
        query = params["q"]
        return _ExplodingResponse(f"401 unauthorized token={token} q={query}")

    monkeypatch.setattr(brave_backend.httpx, "get", fake_get)

    with pytest.raises(brave_backend.WebSearchError) as exc_info:
        brave_backend.BraveBackend(api_key=API_KEY).search("private query", limit=1)

    message = str(exc_info.value)
    assert "brave" in message.lower()
    assert API_KEY not in message
    assert "private query" not in message


def test_import_error_message_points_to_extras(monkeypatch: pytest.MonkeyPatch) -> None:
    brave_backend = _import_brave_module()
    original_import = builtins.__import__

    def fake_import(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ):
        if name == CLIENT_MODULE_NAME:
            raise ImportError("missing brave client")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.delitem(sys.modules, CLIENT_MODULE_NAME, raising=False)
    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(brave_backend.WebSearchError, match=IMPORT_ERROR_MATCH):
        brave_backend.BraveBackend(api_key=API_KEY).search("python")


@NETWORK_MARK
@pytest.mark.skipif(not os.environ.get(ENV_NAME), reason=f"{ENV_NAME} is not set")
def test_live_search_returns_results() -> None:
    brave_backend = _import_brave_module()

    results = brave_backend.BraveBackend(api_key_env=ENV_NAME).search("python mcp", limit=3)

    assert results
    assert all(result.title and result.url for result in results)
