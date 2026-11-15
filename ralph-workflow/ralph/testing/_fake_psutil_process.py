from __future__ import annotations


class FakePsutilProcess:
    __slots__ = (
        "_children",
        "_create_time",
        "_killed",
        "_running",
        "_status",
        "_terminated",
        "pid",
        "ppid",
        "stubborn",
    )

    def __init__(
        self,
        pid: int,
        ppid: int = 0,
        _running: bool = True,
        _status: str = "sleeping",
        _create_time: float = 0.0,
        _terminated: bool = False,
        _killed: bool = False,
        _children: list[FakePsutilProcess] | None = None,
        stubborn: bool = False,
    ) -> None:
        self.pid = pid
        self.ppid = ppid
        self._running = _running
        self._status = _status
        self._create_time = _create_time
        self._terminated = _terminated
        self._killed = _killed
        self._children = [] if _children is None else _children
        self.stubborn = stubborn

    @property
    def info(self) -> dict[str, int]:
        return {"pid": self.pid, "ppid": self.ppid}

    def is_running(self) -> bool:
        return self._running and not self._terminated and not self._killed

    def status(self) -> str:
        if self._killed:
            return "zombie"
        if self._terminated:
            return "zombie"
        return self._status

    def name(self) -> str:
        return "fake"

    def create_time(self) -> float:
        return self._create_time

    def children(self, recursive: bool = False) -> list[FakePsutilProcess]:
        return self._children

    def terminate(self) -> None:
        if not self.stubborn:
            self._terminated = True

    def kill(self) -> None:
        self._killed = True
