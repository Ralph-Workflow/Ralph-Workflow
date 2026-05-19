"""RichGroupProto — protocol shim for rich.Group."""

from __future__ import annotations

from typing import Protocol


class RichGroupProto(Protocol):
    """Protocol for rich.Group class."""

    def __call__(self, *_renderables: object) -> object: ...


__all__ = ["RichGroupProto"]
