from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


class _FakeThread:
    def __init__(
        self,
        label: str,
        on_start: Callable[[], None],
        alive_after_join: bool = False,
        *,
        daemon: bool | None = None,
    ) -> None:
        self._label = label
        self._on_start = on_start
        self._alive_after_join = alive_after_join
        self.daemon_arg: bool | None = daemon
        self.join_calls: list[float | None] = []
        self.is_alive_calls: int = 0

    def start(self) -> None:
        self._on_start()

    def join(self, timeout: float | None = None) -> None:
        self.join_calls.append(timeout)

    def is_alive(self) -> bool:
        self.is_alive_calls += 1
        return self._alive_after_join
