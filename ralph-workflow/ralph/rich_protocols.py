"""Protocol shims for lazily imported Rich classes."""

from __future__ import annotations

from typing import Protocol


class RichTextProto(Protocol):
    """Protocol for rich.Text class."""

    def __call__(self, text: str = "", *, style: str = "") -> object: ...


class RichPanelProto(Protocol):
    """Protocol for rich.Panel class."""

    def fit(self, _renderable: object, **kwargs: object) -> object: ...


class RichGroupProto(Protocol):
    """Protocol for rich.Group class."""

    def __call__(self, *_renderables: object) -> object: ...


__all__ = ["RichGroupProto", "RichPanelProto", "RichTextProto"]
