"""Tests for the OpenCode model catalog helpers."""

from __future__ import annotations

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.api import opencode
from ralph.executor.process import ProcessResult
from tests.test_api_opencode_helper__fakeclient import _FakeClient


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


def test_fetch_catalog_accepts_provider_map_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        opencode.httpx,
        "Client",
        lambda timeout: _FakeClient(
            lambda: _FakeResponse(
                {
                    "anthropic": {
                        "id": "anthropic",
                        "name": "Anthropic",
                        "models": {
                            "claude-sonnet-4": {"name": "Claude Sonnet 4"},
                        },
                    },
                    "openai": {
                        "id": "openai",
                        "name": "OpenAI",
                        "models": {
                            "gpt-5": {"name": "GPT-5"},
                            "o3": {},
                        },
                    },
                }
            )
        ),
    )

    catalog = opencode.fetch_catalog()

    assert [model.id for model in catalog] == [
        "anthropic/claude-sonnet-4",
        "openai/gpt-5",
        "openai/o3",
    ]
    assert catalog[0].provider == "anthropic"
    assert catalog[0].name == "Claude Sonnet 4"
    assert catalog[2].provider == "openai"
    assert catalog[2].name is None


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


def test_fetch_catalog_refetches_after_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-03: the catalog TTL triggers a refetch when stale.

    Wires a ``FakeClock`` so we can advance logical time past
    ``_TTL_SECONDS`` without any real wall-clock wait. The first
    call populates the cache at ``t=0``; a call inside the TTL
    returns the cache without refetching; a call after the TTL
    triggers a refetch and refreshes ``_cached_at``.
    """
    calls = {"count": 0}

    def response_factory() -> _FakeResponse:
        calls["count"] += 1
        return _FakeResponse([{"id": "openai/gpt-5", "provider": "openai"}])

    monkeypatch.setattr(
        opencode.httpx,
        "Client",
        lambda timeout: _FakeClient(response_factory),
    )

    clock = FakeClock(start=0.0)
    fetcher = opencode._CatalogFetcher(clock=clock)
    assert calls["count"] == 0

    first = fetcher()
    assert calls["count"] == 1
    assert fetcher._cached_at == 0.0

    # Advance inside the TTL — no refetch expected.
    clock.advance(opencode._TTL_SECONDS - 1.0)
    second = fetcher()
    assert calls["count"] == 1
    assert second is first
    assert fetcher._cached_at == 0.0

    # Advance past the TTL — refetch expected, _cached_at updated.
    clock.advance(2.0)
    third = fetcher()
    assert calls["count"] == 2
    assert third is not first
    assert fetcher._cached_at == opencode._TTL_SECONDS + 1.0


def test_fetch_catalog_cache_clear_resets_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """cache_clear() resets both the cache and the TTL timestamp."""
    calls = {"count": 0}

    def response_factory() -> _FakeResponse:
        calls["count"] += 1
        return _FakeResponse([{"id": "openai/gpt-5", "provider": "openai"}])

    monkeypatch.setattr(
        opencode.httpx,
        "Client",
        lambda timeout: _FakeClient(response_factory),
    )

    clock = FakeClock(start=0.0)
    fetcher = opencode._CatalogFetcher(clock=clock)
    fetcher()
    assert fetcher._cached_at == 0.0

    # cache_clear must reset the timestamp; otherwise the next call
    # would be inside the (now-meaningless) TTL and skip the refetch.
    clock.advance(10.0)
    fetcher.cache_clear()
    assert fetcher._cache is None
    assert fetcher._cached_at is None

    fetcher()
    assert calls["count"] == 2
    assert fetcher._cached_at == 10.0


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


def test_validate_local_model_support_reports_path_selected_from_path_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        opencode.shutil,
        "which",
        lambda command, path=None: "/first/opencode" if command == "opencode" else None,
    )
    monkeypatch.setattr(
        opencode,
        "_path_command_candidates",
        lambda command, env_path=None: ("/first/opencode", "/second/opencode"),
    )

    def fake_run_process(
        command: str,
        args: tuple[str, ...] | list[str] = (),
        *,
        options: object | None = None,
        _pm: object | None = None,
    ) -> ProcessResult:
        del options, _pm
        if command == "opencode" and list(args) == ["--version"]:
            return ProcessResult((command, *args), 0, "1.15.5\n", "")
        if command == "opencode" and list(args) == ["models", "--refresh", "minimax"]:
            return ProcessResult((command, *args), 1, "", "Provider not found: minimax\n")
        raise AssertionError(f"Unexpected command: {command} {list(args)}")

    message = opencode.validate_local_model_support(
        "minimax/MiniMax-M3",
        _run_process=fake_run_process,
    )

    assert message is not None
    assert "/first/opencode" in message
    assert "1.15.5" in message
    assert "/second/opencode" in message
    assert "first 'opencode' on PATH" in message


def test_validate_local_model_support_rejects_missing_model_after_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        opencode.shutil,
        "which",
        lambda command, path=None: "/chosen/opencode" if command == "opencode" else None,
    )
    monkeypatch.setattr(
        opencode,
        "_path_command_candidates",
        lambda command, env_path=None: ("/chosen/opencode",),
    )

    def fake_run_process(
        command: str,
        args: tuple[str, ...] | list[str] = (),
        *,
        options: object | None = None,
        _pm: object | None = None,
    ) -> ProcessResult:
        del options, _pm
        if command == "opencode" and list(args) == ["--version"]:
            return ProcessResult((command, *args), 0, "1.16.2\n", "")
        if command == "opencode" and list(args) == ["models", "--refresh", "openai"]:
            return ProcessResult(
                (command, *args),
                0,
                "openai/gpt-5.4\nopenai/gpt-5.4-mini\n",
                "",
            )
        raise AssertionError(f"Unexpected command: {command} {list(args)}")

    message = opencode.validate_local_model_support(
        "openai/gpt-5.4-pro",
        _run_process=fake_run_process,
    )

    assert message is not None
    assert "openai/gpt-5.4-pro" in message
    assert "/chosen/opencode" in message
    assert "1.16.2" in message
    assert "openai/gpt-5.4-mini" in message
