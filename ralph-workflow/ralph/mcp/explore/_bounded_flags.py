from __future__ import annotations


class BoundedFlagRegistry(dict[str, bool]):
    def __init__(self, max_entries: int = 256) -> None:
        super().__init__()
        self._max_entries = max_entries

    def __setitem__(self, key: str, value: bool) -> None:
        if key not in self and len(self) >= self._max_entries:
            oldest = next(iter(self))
            super().__delitem__(oldest)
        super().__setitem__(key, value)
