from __future__ import annotations

import itertools
import sys
from typing import TYPE_CHECKING, Any

import pytest

from ralph.process.manager import (
    ManagedProcess,
    ProcessManager,
    ProcessManagerPolicy,
    ProcessTerminationIssuer,
    SpawnOptions,
    is_terminal_status,
)

if TYPE_CHECKING:
    from ralph.process.manager import (
        ManagedAsyncProcess,
        ManagedPtyProcess,
        PsutilModuleLike,
    )
from ralph.testing.fake_process import (
    FakePsutil,
    make_async_process_factory,
)

_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.2,
    kill_followup_timeout_s=0.3,
    log_events=False,
    enable_zombie_reaper=False,
)


def _sleeping_child(manager: ProcessManager, *, label: str = "invoke:process") -> ManagedProcess:
    return manager.spawn(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        SpawnOptions(label=label),
    )


def _fake_psutil() -> PsutilModuleLike:
    return FakePsutil()


def _async_manager() -> ProcessManager:
    return ProcessManager(
        policy=_FAST_POLICY,
        async_process_factory=make_async_process_factory(itertools.count(1), returncode=None),
        psutil=_fake_psutil(),
    )


async def test_terminate_async_returns_only_after_record_is_terminal() -> None:
    manager = _async_manager()
    handle = await manager.spawn_async(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        SpawnOptions(label="invoke:async"),
    )
    assert not is_terminal_status(handle.record.status)

    await manager.terminate_async(handle, issuer=ProcessTerminationIssuer.OPERATOR)

    assert is_terminal_status(handle.record.status), (
        f"terminate_async returned before the record became terminal: {handle.record.status}"
    )
    assert manager.termination_issuer(handle.pid) == ProcessTerminationIssuer.OPERATOR.value


def test_terminate_short_circuit_records_nothing() -> None:
    manager = ProcessManager()
    child = _sleeping_child(manager)
    manager.terminate(child, issuer=ProcessTerminationIssuer.OPERATOR)
    outcomes_after_first = list(manager.list_termination_outcomes()[child.pid])

    manager.terminate(child, issuer=ProcessTerminationIssuer.OPERATOR)
    child.terminate(issuer=ProcessTerminationIssuer.OPERATOR, reason="quota_exhausted")
    child.kill(issuer=ProcessTerminationIssuer.POST_EXIT_WATCHDOG)

    assert manager.list_termination_outcomes()[child.pid] == outcomes_after_first


async def test_terminate_async_short_circuit_records_nothing() -> None:
    manager = _async_manager()
    handle = await manager.spawn_async([sys.executable, "-c", "pass"])
    await manager.terminate_async(handle, issuer=ProcessTerminationIssuer.OPERATOR)
    outcomes_after_first = list(manager.list_termination_outcomes()[handle.pid])

    await manager.terminate_async(handle, issuer=ProcessTerminationIssuer.POST_EXIT_WATCHDOG)
    await handle.terminate(issuer=ProcessTerminationIssuer.POST_EXIT_WATCHDOG)

    assert manager.list_termination_outcomes()[handle.pid] == outcomes_after_first


def test_sync_handle_terminate_and_kill_delegate_to_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = ProcessManager()
    child = _sleeping_child(manager)
    calls: list[tuple[Any, ...]] = []

    def spy(
        handle: object,
        *,
        issuer: ProcessTerminationIssuer,
        reason: str = "operator_cancellation",
        grace_period_s: float | None = None,
    ) -> None:
        calls.append((handle, issuer, reason, grace_period_s))

    monkeypatch.setattr(manager, "terminate", spy)
    try:
        child.terminate(
            grace_period_s=1.5,
            issuer=ProcessTerminationIssuer.OPERATOR,
            reason="quota_exhausted",
        )
        child.kill(issuer=ProcessTerminationIssuer.POST_EXIT_WATCHDOG)
    finally:
        ProcessManager.terminate(
            manager,
            child,
            issuer=ProcessTerminationIssuer.OPERATOR,
            grace_period_s=0.0,
        )

    assert calls == [
        (child, ProcessTerminationIssuer.OPERATOR, "quota_exhausted", 1.5),
        (child, ProcessTerminationIssuer.POST_EXIT_WATCHDOG, "operator_cancellation", 0.0),
    ]


def test_pty_handle_terminate_and_kill_delegate_to_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = ProcessManager()
    child: ManagedPtyProcess = manager.spawn_pty(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        label="invoke:pty",
    )
    calls: list[tuple[Any, ...]] = []

    def spy(
        handle: object,
        *,
        issuer: ProcessTerminationIssuer,
        reason: str = "operator_cancellation",
        grace_period_s: float | None = None,
    ) -> None:
        calls.append((handle, issuer, reason, grace_period_s))

    monkeypatch.setattr(manager, "terminate", spy)
    try:
        child.terminate(
            grace_period_s=0.7,
            issuer=ProcessTerminationIssuer.OPERATOR,
            reason="operator_cancellation",
        )
        child.kill(issuer=ProcessTerminationIssuer.OPERATOR)
    finally:
        ProcessManager.terminate(
            manager,
            child,
            issuer=ProcessTerminationIssuer.OPERATOR,
            grace_period_s=0.0,
        )
        child.close()

    assert calls == [
        (child, ProcessTerminationIssuer.OPERATOR, "operator_cancellation", 0.7),
        (child, ProcessTerminationIssuer.OPERATOR, "operator_cancellation", 0.0),
    ]


async def test_async_handle_terminate_delegates_to_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _async_manager()
    handle: ManagedAsyncProcess = await manager.spawn_async(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        SpawnOptions(label="invoke:async"),
    )
    calls: list[tuple[Any, ...]] = []

    async def spy(
        handle: ManagedAsyncProcess,
        *,
        issuer: ProcessTerminationIssuer,
        reason: str = "operator_cancellation",
        grace_period_s: float | None = None,
    ) -> None:
        calls.append((handle, issuer, reason, grace_period_s))

    monkeypatch.setattr(manager, "terminate_async", spy)
    await handle.terminate(
        grace_period_s=0.4,
        issuer=ProcessTerminationIssuer.QUOTA_DETECTOR,
        reason="quota_exhausted",
    )

    assert calls == [
        (handle, ProcessTerminationIssuer.QUOTA_DETECTOR, "quota_exhausted", 0.4),
    ]
