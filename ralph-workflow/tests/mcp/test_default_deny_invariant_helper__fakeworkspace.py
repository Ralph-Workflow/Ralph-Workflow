from __future__ import annotations


class _FakeWorkspace:
    """Minimal workspace stub for capability-gate tests."""

    def absolute_path(self, path: str) -> str:
        return path
