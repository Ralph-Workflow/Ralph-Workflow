
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class WorkspaceLike(Protocol):

    def absolute_path(self, path: str) -> str:
        ...
