
from __future__ import annotations

from typing import Protocol


class RichPanelProto(Protocol):

    def fit(self, _renderable: object, **kwargs: object) -> object: ...


__all__ = ["RichPanelProto"]
