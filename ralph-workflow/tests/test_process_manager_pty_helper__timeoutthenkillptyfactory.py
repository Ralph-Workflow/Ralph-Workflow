from __future__ import annotations

import itertools
from typing import TYPE_CHECKING

from tests.test_process_manager_pty_helper__timeoutthenkillptyprocess import (
    _TimeoutThenKillPtyProcess,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

_PTY_COLUMNS = 80

_PTY_ROWS = 24


class _TimeoutThenKillPtyFactory:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], str | None, dict[str, str] | None]] = []
        self._pids = itertools.count(2000)

    def __call__(
        self,
        command: Sequence[str],
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        cols: int,
        rows: int,
    ) -> _TimeoutThenKillPtyProcess:
        assert cols == _PTY_COLUMNS
        assert rows == _PTY_ROWS
        self.calls.append((tuple(command), cwd, env))
        pid = next(self._pids)
        return _TimeoutThenKillPtyProcess(pid=pid, master_fd=pid + 10, slave_fd=pid + 11)
