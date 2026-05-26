from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FakePsutilProcess:
    """Minimal psutil.Process-like fake for descendant_snapshot tests."""

    pid: int
    ppid: int = 0
    _running: bool = True
    _status: str = "sleeping"
    _create_time: float = 0.0
    _terminated: bool = False
    _killed: bool = False
    _children: list[FakePsutilProcess] = field(default_factory=list)
    stubborn: bool = False

    @property
    def info(self) -> dict[str, int]:
        return {"pid": self.pid, "ppid": self.ppid}

    def is_running(self) -> bool:
        return (
            self._running
            and not self._terminated
            and not self._killed
            and self.status() != "zombie"
        )

    def status(self) -> str:
        if self._killed:
            return "zombie"
        if self._terminated:
            return "zombie"
        return self._status

    def create_time(self) -> float:
        return self._create_time

    def children(self, recursive: bool = False) -> list[FakePsutilProcess]:
        return self._children

    def terminate(self) -> None:
        if not self.stubborn:
            self._terminated = True

    def kill(self) -> None:
        self._killed = True
