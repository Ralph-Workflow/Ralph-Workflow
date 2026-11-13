from __future__ import annotations

from datetime import UTC, datetime

from ralph.process.manager import ManagedProcess, ProcessManager, ProcessRecord, ProcessStatus
from tests.recovery._fake_sync_process import FAKE_PROCESS_PID, FakeSyncProcess


class FakeManagedHandle(ManagedProcess):
    def __init__(
        self,
        *,
        reported_returncode: int | None = 0,
        observed_returncode: int | None = 0,
        issuer: str | None = None,
    ) -> None:
        super().__init__(
            FakeSyncProcess(reported_returncode),
            ProcessRecord(
                pid=FAKE_PROCESS_PID,
                pgid=FAKE_PROCESS_PID,
                command=("fake-agent",),
                cwd=None,
                started_at=datetime(2024, 1, 1, tzinfo=UTC),
                status=ProcessStatus.RUNNING,
                returncode=reported_returncode,
                label="invoke:fake-agent",
            ),
            ProcessManager(),
        )
        self._observed_returncode = observed_returncode
        self._issuer = issuer
        self.terminated: list[float | None] = []

    def poll(self) -> int | None:
        return self._observed_returncode

    def terminate(self, grace_period_s: float | None = None) -> None:
        self.terminated.append(grace_period_s)

    def termination_issuer(self) -> str | None:
        return self._issuer
