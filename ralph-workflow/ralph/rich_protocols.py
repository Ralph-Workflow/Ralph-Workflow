"""Protocol shims for lazily imported Rich classes."""

from __future__ import annotations

from typing import Protocol

from ralph._rich_group_proto import RichGroupProto
from ralph._rich_panel_proto import RichPanelProto


class RichTextProto(Protocol):
    """Protocol for rich.Text class."""

    def __call__(self, text: str = "", *, style: str = "") -> object: ...


__all__ = ["RichGroupProto", "RichPanelProto", "RichTextProto"]
