from __future__ import annotations


class _FakeWorkspace:
    def absolute_path(self, path: str) -> str:
        return path
