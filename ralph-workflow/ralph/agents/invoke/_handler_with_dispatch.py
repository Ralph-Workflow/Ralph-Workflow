from __future__ import annotations

from typing import Protocol


class _HandlerWithDispatch(Protocol):
    def dispatch(self, event: object) -> None: ...
