from __future__ import annotations

from io import TextIOBase


class _DummyTTY(TextIOBase):
    def isatty(self) -> bool:
        return True
