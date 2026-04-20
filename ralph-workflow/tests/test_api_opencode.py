"""Tests for the OpenCode model catalog helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.api import opencode

if TYPE_CHECKING:
    from collections.abc import Callable


class _FakeResponse:
    def __init__(self, payload: object, *, status_error: Exception | None = None) -> None:
        self._payload = payload
        self._status_error = status_error

    def raise_for_status(self) -> None:
        if self._status_error is not None:
            raise self._status_error

    def json(self) -> object:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _FakeClient:
    def __init__(self, response_factory: Callable[[], _FakeResponse]) -> None:
        self._response_factory = response_factory

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, exc_type, exc, _tb) -> None:
        return None

    def get(self, url: str) -> _FakeResponse:
        assert url == opencode.CATALOG_URL
        return self._response_factory()


@pytest.fixture(autouse=True)
def clear_catalog_cache() -> None:
    opencode.fetch_catalog.cache_clear()


def test_fetch_catalog_validates_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        opencode.httpx,
        "Client",
        lambda timeout: _FakeClient(
            lambda: _FakeResponse(
                [
                    {
                        "id": "anthropic/claude-sonnet-4",
                        "name": "Claude Sonnet 4",
                        "provider": "anthropic",
                    },
                    {"id": "openai/gpt-5", "provider": "openai"},
                ]
            )
        ),
    )

    catalog = opencode.fetch_catalog()

    assert [model.id for model in catalog] == ["anthropic/claude-sonnet-4", "openai/gpt-5"]
    assert catalog[0].name == "Claude Sonnet 4"
    assert catalog[1].provider == "openai"


def test_fetch_catalog_caches_result(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"count": 0}

    def response_factory() -> _FakeResponse:
        calls["count"] += 1
        return _FakeResponse([{"id": "openai/gpt-5", "provider": "openai"}])

    monkeypatch.setattr(
        opencode.httpx,
        "Client",
        lambda timeout: _FakeClient(response_factory),
    )

    first = opencode.fetch_catalog()
    second = opencode.fetch_catalog()

    assert first is second
    assert calls["count"] == 1


def test_fetch_catalog_reraises_http_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    request = opencode.httpx.Request("GET", opencode.CATALOG_URL)
    error = opencode.httpx.HTTPStatusError(
        "boom", request=request, response=opencode.httpx.Response(503, request=request)
    )
    monkeypatch.setattr(
        opencode.httpx,
        "Client",
        lambda timeout: _FakeClient(lambda: _FakeResponse([], status_error=error)),
    )

    with pytest.raises(opencode.httpx.HTTPStatusError):
        opencode.fetch_catalog()


def test_fetch_catalog_reraises_json_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        opencode.httpx,
        "Client",
        lambda timeout: _FakeClient(lambda: _FakeResponse(ValueError("bad json"))),
    )

    with pytest.raises(ValueError, match="bad json"):
        opencode.fetch_catalog()


def test_catalog_helpers_filter_and_sort(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        opencode,
        "fetch_catalog",
        lambda: [
            opencode.ModelEntry(
                id="anthropic/claude-sonnet-4", name="Claude Sonnet 4", provider="Anthropic"
            ),
            opencode.ModelEntry(id="openai/gpt-5", name="GPT-5", provider="OpenAI"),
            opencode.ModelEntry(id="openai/o3", name="o3", provider="OpenAI"),
            opencode.ModelEntry(id="local/custom", name=None, provider=None),
        ],
    )

    assert opencode.get_model_by_id("openai/gpt-5") == opencode.ModelEntry(
        id="openai/gpt-5", name="GPT-5", provider="OpenAI"
    )
    assert opencode.get_model_by_id("missing") is None
    assert [model.id for model in opencode.search_models("openai")] == ["openai/gpt-5", "openai/o3"]
    assert [model.id for model in opencode.search_models("sonnet")] == ["anthropic/claude-sonnet-4"]
    assert opencode.list_providers() == ["Anthropic", "OpenAI"]
