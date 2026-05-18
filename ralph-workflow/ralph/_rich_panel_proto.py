"""RichPanelProto — protocol shim for rich.Panel."""

from __future__ import annotations

from typing import Protocol


class RichPanelProto(Protocol):
    """Protocol for rich.Panel class."""

    def fit(self, _renderable: object, **kwargs: object) -> object: ...


__all__ = ["RichPanelProto"]
