"""Passthrough workspace mock for coordination tool tests."""

from __future__ import annotations


class MockWorkspace:
    def absolute_path(self, path: str) -> str:
        return path
