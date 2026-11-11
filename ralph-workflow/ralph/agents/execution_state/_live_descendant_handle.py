from typing import Protocol


class LiveDescendantHandle(Protocol):

    def has_live_descendants(self) -> bool: ...
