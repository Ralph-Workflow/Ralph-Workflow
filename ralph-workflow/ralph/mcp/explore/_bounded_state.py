from __future__ import annotations

from collections import deque
from collections.abc import Iterator, MutableMapping
from typing import TypeVar

KeyT = TypeVar("KeyT")
ValueT = TypeVar("ValueT")


class BoundedKeyValueStore(MutableMapping[KeyT, ValueT]):
    def __init__(self) -> None:
        self._entries: deque[tuple[KeyT, ValueT]] = deque(maxlen=64)

    def __setitem__(self, key: KeyT, value: ValueT) -> None:
        for index, (existing, _value) in enumerate(self._entries):
            if existing == key:
                self._entries[index] = (key, value)
                return
        self._entries.append((key, value))

    def __getitem__(self, key: KeyT) -> ValueT:
        for existing, value in self._entries:
            if existing == key:
                return value
        raise KeyError(key)

    def __delitem__(self, key: KeyT) -> None:
        for index, (existing, _value) in enumerate(self._entries):
            if existing == key:
                del self._entries[index]
                return
        raise KeyError(key)

    def __iter__(self) -> Iterator[KeyT]:
        return (existing for existing, _value in self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def copy(self) -> dict[KeyT, ValueT]:
        return dict(self._entries)
