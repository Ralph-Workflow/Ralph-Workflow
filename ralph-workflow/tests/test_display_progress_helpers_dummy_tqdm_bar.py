from __future__ import annotations


class _DummyTqdmBar:
    def __init__(self) -> None:
        self.closed_calls = 0
        self.closed = False

    def update(self, n: int = 1) -> None:
        return None

    def close(self) -> None:
        self.closed_calls += 1
        self.closed = True

    def refresh(self) -> None:
        return None
