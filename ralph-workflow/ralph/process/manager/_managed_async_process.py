from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

from ralph import timeout_defaults
from ralph.process.manager._process_status import _TERMINAL_STATUSES, ProcessStatus
from ralph.timeout_defaults import PROCESS_EXIT_WAIT_SECONDS

__all__ = ["PROCESS_EXIT_WAIT_SECONDS", "ManagedAsyncProcess"]

if TYPE_CHECKING:
    from types import TracebackType

    from ralph.process.manager._process_manager import ProcessManager
    from ralph.process.manager._process_manager_types import _AsyncProcessLike
    from ralph.process.manager._process_record import ProcessRecord


class ManagedAsyncProcess:
    def __init__(
        self,
        proc: _AsyncProcessLike,
        record: ProcessRecord,
        manager: ProcessManager,
    ) -> None:
        self._proc = proc
        self._record = record
        self._manager = manager

    @property
    def record(self) -> ProcessRecord:
        return self._record

    @property
    def pid(self) -> int:
        return self._proc.pid

    @property
    def stdout(self) -> asyncio.StreamReader | None:
        return self._proc.stdout

    @property
    def stderr(self) -> asyncio.StreamReader | None:
        return self._proc.stderr

    @property
    def stdin(self) -> asyncio.StreamWriter | None:
        return self._proc.stdin

    @property
    def returncode(self) -> int | None:
        rc = self._proc.returncode
        if rc is not None:
            return rc
        if self._record.status in (ProcessStatus.KILLED, ProcessStatus.FAILED):
            return self._record.returncode
        return None

    def termination_issuer(self) -> str | None:
        return self._manager.termination_issuer(self._record.pid)

    def record_terminal_reason(self, reason: str) -> None:
        self._manager.record_terminal_reason(self._record.pid, reason)

    async def wait(self) -> int:
        try:
            rc = await asyncio.wait_for(
                self._proc.wait(),
                timeout=timeout_defaults.PROCESS_EXIT_WAIT_SECONDS,
            )
        except TimeoutError:
            raise
        if self._record.status not in _TERMINAL_STATUSES:
            self._manager._mark_exited(self._record, rc)
        return rc

    async def wait_supervised(self) -> int:
        rc = await self._proc.wait()
        if self._record.status not in _TERMINAL_STATUSES:
            self._manager._mark_exited(self._record, rc)
        return rc

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        try:
            comm_result = await asyncio.wait_for(
                self._proc.communicate(input),
                timeout=timeout_defaults.PROCESS_EXIT_WAIT_SECONDS,
            )
        except TimeoutError:
            raise
        rc: int | None = self._proc.returncode
        if rc is not None and self._record.status not in _TERMINAL_STATUSES:
            self._manager._mark_exited(self._record, rc)
        stdout, stderr = comm_result
        return stdout or b"", stderr or b""

    async def terminate(self, grace_period_s: float | None = None) -> None:
        if self._record.status in _TERMINAL_STATUSES:
            return
        self._manager.record_terminal_reason(
            self._record.pid, "operator_cancellation", "managed_process.terminate"
        )
        gp = (
            grace_period_s
            if grace_period_s is not None
            else self._manager.policy.default_grace_period_s
        )
        await self._manager._escalate_termination_async(self._record, self._proc, gp)

    async def __aenter__(self) -> ManagedAsyncProcess:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        del exc_type, exc_val, exc_tb
        if self._record.status not in _TERMINAL_STATUSES:
            with contextlib.suppress(Exception):
                await self.terminate(grace_period_s=2.0)
        for pipe in (self._proc.stdout, self._proc.stderr, self._proc.stdin):
            if pipe is not None and not isinstance(pipe, asyncio.StreamReader):
                with contextlib.suppress(Exception):
                    pipe.close()
