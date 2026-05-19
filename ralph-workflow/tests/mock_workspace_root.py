"""Standard workspace-root mock for tool tests."""

from __future__ import annotations


class MockWorkspaceRoot:
    def __init__(self, root: object) -> None:
        self.root = root
