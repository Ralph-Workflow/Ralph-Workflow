from __future__ import annotations


class FakeTeardown:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str]] = []

    def teardown_subtree(self, host_pid: int, *, issuer: str) -> None:
        self.calls.append((host_pid, issuer))
