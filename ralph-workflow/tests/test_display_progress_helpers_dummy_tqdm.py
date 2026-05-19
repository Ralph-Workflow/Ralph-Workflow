from __future__ import annotations


class _DummyTqdm:
    def __init__(self) -> None:
        self.n = 0
        self.updated: list[int] = []
        self.refresh_calls = 0

    def update(self, n: int = 1) -> None:
        self.updated.append(n)
        self.n += n

    def close(self) -> None:
        return None

    def refresh(self) -> None:
        self.refresh_calls += 1
