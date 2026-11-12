from __future__ import annotations

from typing import Protocol


class RichGroupProto(Protocol):
    def __call__(self, *_renderables: object) -> object: ...


__all__ = ["RichGroupProto"]
