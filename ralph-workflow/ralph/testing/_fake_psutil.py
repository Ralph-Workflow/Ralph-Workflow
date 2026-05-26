from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from ralph.testing._fake_psutil_process import FakePsutilProcess

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ralph.process.manager._process_manager_types import _PsutilProcessLike


class FakePsutil:
    """Fake psutil module for testing without real process operations."""

    NoSuchProcess: type[BaseException] = Exception
    AccessDenied: type[BaseException] = Exception
    Process: Callable[[int], FakePsutilProcess]

    def __init__(self) -> None:
        self._processes: dict[int, FakePsutilProcess] = {}
        self.Process = self.process_from_pid

    def process_from_pid(self, pid: int) -> FakePsutilProcess:
        if pid not in self._processes:
            self._processes[pid] = FakePsutilProcess(pid=pid)
        return self._processes[pid]

    def process_iter(self, attrs: Sequence[str] | None = None) -> list[FakePsutilProcess]:
        del attrs
        return list(self._processes.values())

    def wait_procs(
        self,
        procs: Sequence[_PsutilProcessLike],
        timeout: float | None = None,
    ) -> tuple[list[_PsutilProcessLike], list[_PsutilProcessLike]]:
        dead: list[_PsutilProcessLike] = []
        alive: list[_PsutilProcessLike] = []
        for p in procs:
            if not p.is_running() or p.status() == "zombie":
                dead.append(p)
            else:
                alive.append(p)
        return dead, alive
