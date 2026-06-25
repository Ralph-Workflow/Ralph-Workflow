"""ManagedAsyncProcess — asyncio process wrapper with lifecycle integration."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from ralph.process.manager._process_status import _TERMINAL_STATUSES

if TYPE_CHECKING:
    import asyncio
    from types import TracebackType

    from ralph.process.manager._process_manager import ProcessManager
    from ralph.process.manager._process_manager_types import _AsyncProcessLike
    from ralph.process.manager._process_record import ProcessRecord


class ManagedAsyncProcess:
    """Wraps asyncio.subprocess.Process and integrates with ProcessManager lifecycle tracking."""

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
        return self._proc.returncode

    async def wait(self) -> int:
        rc = await self._proc.wait()  # mcp-timeout-ok: cancellable async coroutine
        if self._record.status not in _TERMINAL_STATUSES:
            self._manager._mark_exited(self._record, rc)
        return rc

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        stdout, stderr = await self._proc.communicate(input)  # mcp-timeout-ok: async coroutine
        rc = self._proc.returncode
        if rc is not None and self._record.status not in _TERMINAL_STATUSES:
            self._manager._mark_exited(self._record, rc)
        return stdout or b"", stderr or b""

    async def terminate(self, grace_period_s: float | None = None) -> None:
        # Idempotency guard: if already terminal, skip without error
        if self._record.status in _TERMINAL_STATUSES:
            return
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
            # Suppress termination errors so the CM exit never masks
            # the original exception (return None so exc_val still
            # propagates).
            with contextlib.suppress(Exception):
                await self.terminate(grace_period_s=2.0)
