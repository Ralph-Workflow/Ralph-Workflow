from typing import Protocol


class _LiveDescendantHandle(Protocol):
    def has_live_descendants(self) -> bool: ...
