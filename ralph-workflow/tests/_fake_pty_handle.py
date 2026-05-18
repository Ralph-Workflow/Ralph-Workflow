from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


class _FakePtyHandle:
    def __init__(
        self,
        *,
        lines: list[str] | None = None,
        exit_code: int = 0,
    ) -> None:
        self._lines = list(lines or [])
        self._exit_code = exit_code
        self.terminated = False

    def read_line(self) -> str | None:
        if self._lines:
            return self._lines.pop(0)
        return None

    def terminate(self) -> None:
        self.terminated = True

    def wait(self) -> int:
        return self._exit_code
