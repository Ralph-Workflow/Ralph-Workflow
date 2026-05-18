"""Shared _FakeHandle for opencode child liveness state tests."""

from __future__ import annotations


class _FakeHandle:
    returncode = 0

    def __init__(self, *, has_descendants: bool = False) -> None:
        self._has_descendants = has_descendants

    def has_live_descendants(self) -> bool:
        return self._has_descendants
