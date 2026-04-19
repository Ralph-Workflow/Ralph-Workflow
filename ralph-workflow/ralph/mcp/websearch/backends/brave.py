"""Brave Search web-search backend."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from importlib import import_module
from typing import cast

import httpx

from ..secrets import resolve_secret
from .base import SearchResult, WebSearchError

_DEFAULT_URL = "https://api.search.brave.com/res/v1/web/search"
_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class BraveBackend:
    """Backend powered by Brave Search's HTTP API."""

    api_key: str | None = None
    api_key_env: str | None = None
    url: str = _DEFAULT_URL

    def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        _ensure_brave_extra_installed()
        resolved_key = resolve_secret(self.api_key, self.api_key_env)
        try:
            response = httpx.get(
                self.url,
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": resolved_key,
                },
                params={"q": query, "count": limit},
                timeout=_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            payload = cast("object", response.json())
        except Exception as exc:
            raise WebSearchError("brave search failed") from exc
        return _normalize_results(payload)


def _ensure_brave_extra_installed() -> None:
    try:
        import_module("brave_search_python_client")
    except ImportError as exc:
        raise WebSearchError(
            "backend 'brave' requires 'pip install ralph-workflow[web-search]'"
        ) from exc


def _normalize_results(payload: object) -> list[SearchResult]:
    if not isinstance(payload, Mapping):
        raise WebSearchError("brave returned an invalid response")
    web_payload = payload.get("web")
    if not isinstance(web_payload, Mapping):
        return []
    raw_results = web_payload.get("results")
    if not isinstance(raw_results, Iterable):
        return []
    results: list[SearchResult] = []
    for item in raw_results:
        if not isinstance(item, Mapping):
            continue
        title = _string_value(item, "title")
        url = _string_value(item, "url")
        snippet = _string_value(item, "description")
        if not title or not url:
            continue
        results.append(SearchResult(title=title, url=url, snippet=snippet))
    return results


def _string_value(payload: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


__all__ = ["BraveBackend"]
