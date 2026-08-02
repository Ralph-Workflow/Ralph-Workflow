from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.api import opencode

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.test_api_opencode import _FakeResponse


class _FakeClient:
    def __init__(self, response_factory: Callable[[], _FakeResponse]) -> None:
        self._response_factory = response_factory

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, exc_type: object, exc: object, _tb: object) -> None:
        return None

    def get(self, url: str) -> _FakeResponse:
        assert url == opencode.CATALOG_URL
        return self._response_factory()
