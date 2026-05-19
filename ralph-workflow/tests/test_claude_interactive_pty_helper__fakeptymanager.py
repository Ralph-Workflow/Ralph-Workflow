from __future__ import annotations

import pytest

from tests.test_claude_interactive_pty_helper__fakeptyhandle import _FakePtyHandle


class _FakePtyManager:
    def __init__(self) -> None:
        self.spawn_called = False
        self.spawn_pty_called = False

    def spawn(self, *args: object, **kwargs: object) -> _FakePtyHandle:
        del args, kwargs
        self.spawn_called = True
        pytest.fail("interactive Claude must not use pipe-based spawn()")

    def spawn_pty(self, *args: object, **kwargs: object) -> _FakePtyHandle:
        del args, kwargs
        self.spawn_pty_called = True
        return _FakePtyHandle()
