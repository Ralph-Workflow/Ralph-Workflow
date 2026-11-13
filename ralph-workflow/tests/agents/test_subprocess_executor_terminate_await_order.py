from __future__ import annotations

import asyncio

import pytest

from ralph.agents import subprocess_executor
from ralph.agents.subprocess_executor import SubprocessAgentExecutor
from ralph.process.manager import ProcessManager, ProcessTerminationIssuer
from ralph.process.manager._managed_async_process import ManagedAsyncProcess
from ralph.process.manager._process_status import ProcessStatus


class _FakeRecord:
    def __init__(self) -> None:
        self.status = ProcessStatus.RUNNING
        self.label = "agent:unit-1:root"


class _FakeHandle(ManagedAsyncProcess):
    def __init__(self, events: list[str]) -> None:
        self._record = _FakeRecord()
        self._events = events

    @property
    def pid(self) -> int:
        return 424_242

    @property
    def returncode(self) -> int | None:
        return None

    async def wait_supervised(self) -> int:
        await asyncio.Event().wait()
        return 0

    async def wait(self) -> int:
        self._events.append("wait")
        return 0


class _FakeManager(ProcessManager):
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.calls: list[dict[str, object]] = []

    async def terminate_async(
        self,
        handle: object,
        *,
        issuer: ProcessTerminationIssuer,
        reason: str = "operator_cancellation",
        grace_period_s: float | None = None,
    ) -> None:
        del handle
        await asyncio.sleep(0)
        self._events.append("terminate_async")
        self.calls.append({"issuer": issuer, "reason": reason, "grace_period_s": grace_period_s})


@pytest.mark.asyncio
async def test_supervise_error_path_awaits_terminate_before_wait_and_reap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    manager = _FakeManager(events)
    handle = _FakeHandle(events)

    async def _reap_session(handle: object, pgid: int | None) -> None:
        del handle, pgid
        events.append("reap")

    monkeypatch.setattr(subprocess_executor, "_verified_session_pgid", lambda pid: None)
    monkeypatch.setattr(subprocess_executor, "_reap_session", _reap_session)

    async def _failing_drain() -> None:
        raise RuntimeError("drain failed")

    executor = SubprocessAgentExecutor(
        command=("/opt/agents/worker-cli",),
        _pm=manager,
    )

    with pytest.raises(RuntimeError, match="drain failed"):
        await executor._supervise(
            handle,
            _failing_drain(),
            "unit-1",
        )

    terminate_index = events.index("terminate_async")
    assert terminate_index < events.index("wait")
    assert events.index("wait") < events.index("reap")
    assert manager.calls[0] == {
        "issuer": ProcessTerminationIssuer.OPERATOR,
        "reason": "operator_cancellation",
        "grace_period_s": 0,
    }
