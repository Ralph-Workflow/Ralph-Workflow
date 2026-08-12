from typing import Protocol


class LiveDescendantHandle(Protocol):
    """Structural protocol for process handles with live-descendant visibility."""

    def has_live_descendants(self) -> bool: ...
