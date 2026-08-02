from __future__ import annotations


class FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
