from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

class _FakeThread:
    def __init__(self, label: str, on_start: Callable[[], None]) -> None:
        self._label = label
        self._on_start = on_start

    def start(self) -> None:
        self._on_start()
