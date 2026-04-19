from __future__ import annotations

import inspect
from dataclasses import is_dataclass
from importlib import import_module
from typing import Any


def _import_base_module():
    try:
        return import_module("ralph.mcp.websearch.backends.base")
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised in RED phase
        raise AssertionError("ralph.mcp.websearch.backends.base should exist") from exc


def test_search_result_has_uniform_shape() -> None:
    base = _import_base_module()

    result = base.SearchResult(title="Example", url="https://example.com", snippet="summary")

    typed_result: Any = result

    assert is_dataclass(typed_result)
    assert typed_result.__dict__ == {
        "title": "Example",
        "url": "https://example.com",
        "snippet": "summary",
    }


def test_websearch_symbols_are_exported() -> None:
    package = import_module("ralph.mcp.websearch")
    base = _import_base_module()

    assert package.SearchResult is base.SearchResult
    assert package.WebSearchError is base.WebSearchError
    assert package.WebSearchBackend is base.WebSearchBackend
    assert hasattr(base.WebSearchBackend, "search")


EXPECTED_SEARCH_LIMIT_DEFAULT = 10


def test_websearch_backend_protocol_search_signature_uses_kw_only_limit_default() -> None:
    base = _import_base_module()

    signature = inspect.signature(base.WebSearchBackend.search)
    params = list(signature.parameters.values())

    assert [param.name for param in params] == ["self", "query", "limit"]
    assert params[1].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert params[2].kind is inspect.Parameter.KEYWORD_ONLY
    assert params[2].default == EXPECTED_SEARCH_LIMIT_DEFAULT
