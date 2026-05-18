"""Shared _FakeHandle for opencode session execution tests."""

from __future__ import annotations


class _FakeHandle:
    returncode: int = 0
    stdout = None
    stderr = None

    def __init__(self, *, returncode: int = 0, has_descendants: bool = False) -> None:
        self.returncode = returncode
        self._has_descendants = has_descendants

    def has_live_descendants(self) -> bool:
        return self._has_descendants

    def descendant_snapshot(self) -> tuple[int, float | None]:
        return (1 if self._has_descendants else 0, 5.0 if self._has_descendants else None)

    def poll(self) -> int | None:
        return self.returncode
