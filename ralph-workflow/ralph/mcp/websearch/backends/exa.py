"""Exa web-search backend.

Implements the ``ExaBackend`` dataclass that wraps the ``exa-py`` Python SDK to
deliver web-search results via the Exa API. Requires ``pip install
ralph-workflow[web-search]`` (or ``pip install exa-py``) at runtime; importing
this module without the SDK installed is safe, but calling ``search`` raises
``WebSearchError``.

API key resolution:

- Pass ``api_key`` directly, or
- set ``api_key_env`` to an environment variable name that holds the key
  (resolved via ``ralph.mcp.websearch.secrets.resolve_secret``).

Typical usage (from ``ralph.config.mcp_models`` backend selection)::

    backend = ExaBackend(api_key_env="EXA_API_KEY")
    results = backend.search("async Python tutorial", limit=5)
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING, cast

from ..secrets import resolve_secret
from .base import SearchResult, WebSearchError

if TYPE_CHECKING:
    from typing import Protocol

    class _ExaSearchResponse(Protocol):
        results: Iterable[object]

    class _ExaClient(Protocol):
        def search(self, query: str, *, num_results: int) -> _ExaSearchResponse: ...

    class _ExaType(Protocol):
        def __call__(self, *, api_key: str) -> _ExaClient: ...


@dataclass(frozen=True)
class ExaBackend:
    """Backend powered by the Exa Python SDK."""

    api_key: str | None = None
    api_key_env: str | None = None

    def search(self, query: str, *, limit: int = 10) -> list[SearchResult]:
        exa_type = _load_exa_type()
        resolved_key = resolve_secret(self.api_key, self.api_key_env)
        try:
            response = exa_type(api_key=resolved_key).search(query, num_results=limit)
        except Exception as exc:
            raise WebSearchError("exa search failed") from exc
        return _normalize_results(response)


def _load_exa_type() -> _ExaType:
    try:
        module = import_module("exa_py")
    except ImportError as exc:
        raise WebSearchError(
            "backend 'exa' requires 'pip install ralph-workflow[web-search]'"
        ) from exc
    exa_type = cast("object | None", getattr(module, "Exa", None))
    if exa_type is None:
        raise WebSearchError("exa backend is unavailable")
    return cast("_ExaType", exa_type)


def _normalize_results(response: _ExaSearchResponse) -> list[SearchResult]:
    results: list[SearchResult] = []
    for item in response.results:
        payload = _object_payload(item)
        title = _string_value(payload, "title")
        url = _string_value(payload, "url")
        snippet = _string_value(payload, "text")
        if not title or not url:
            continue
        results.append(SearchResult(title=title, url=url, snippet=snippet))
    return results


def _object_payload(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return value
    try:
        payload = cast("dict[str, object]", vars(value))
    except TypeError:
        return {}
    return payload


def _string_value(payload: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


__all__ = ["ExaBackend"]
