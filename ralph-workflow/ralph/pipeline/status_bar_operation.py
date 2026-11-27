from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.display.status_bar import StatusBarModel
    from ralph.pipeline.status_bar_authority import StatusBarAuthority


class OperationStatus:
    __slots__ = ("_authority", "is_agent", "model")

    def __init__(self, authority: StatusBarAuthority) -> None:
        self._authority = authority
        self.model: StatusBarModel | None = None
        self.is_agent = False

    def show(self, model: StatusBarModel) -> None:
        self.model = model
        self._authority.render()
