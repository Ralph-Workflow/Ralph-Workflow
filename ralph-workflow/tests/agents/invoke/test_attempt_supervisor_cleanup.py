from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.invoke.attempt_supervision import (
    AttemptSupervisor,
    ProviderErrorObserved,
    SilenceObserved,
    TeardownFn,
    resolve_teardown,
)
from ralph.process.manager import (
    ManagedProcess,
    ProcessManager,
    ProcessTerminationIssuer,
    SpawnOptions,
)
from ralph.process.teardown import ProcessTeardown, teardown_subtree
from ralph.recovery.attempt_facts import TerminationIssuer

_QUOTA_DETAIL = "RESOURCE_EXHAUSTED (code 429)\n"


def _sleeping_child(manager: ProcessManager) -> ManagedProcess:
    return manager.spawn(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        SpawnOptions(label="attempt-supervision"),
    )


def test_cleanup_reaps_the_tree_without_recording_facts() -> None:
    manager = ProcessManager()
    child = _sleeping_child(manager)
    supervisor = AttemptSupervisor(child)
    supervisor.cleanup(ProcessTerminationIssuer.INVOKE_TEARDOWN)
    assert child.wait(timeout=15) is not None
    record = supervisor.recorder.termination()
    assert record.log == ()
    assert record.liveness_observed is None
    assert supervisor.observations == ()
    assert supervisor.winner is None


def test_cleanup_with_terminate_signals_with_the_given_issuer() -> None:
    manager = ProcessManager()
    child = _sleeping_child(manager)
    supervisor = AttemptSupervisor(child)
    supervisor.cleanup(
        ProcessTerminationIssuer.OPERATOR,
        terminate=True,
        reason="operator_cancellation",
    )
    assert child.wait(timeout=15) is not None
    assert manager.termination_issuer(child.pid) == ProcessTerminationIssuer.OPERATOR.value
    assert manager.original_terminal_reason(child.pid) == "operator_cancellation"
    assert supervisor.recorder.termination().log == ()


def test_cleanup_after_an_act_still_reaps_and_keeps_the_issuer() -> None:
    manager = ProcessManager()
    child = _sleeping_child(manager)
    supervisor = AttemptSupervisor(child)
    outcome = supervisor.act(SilenceObserved(WatchdogFireReason.NO_OUTPUT_DEADLINE))
    assert outcome is not None
    facts_after_act = supervisor.recorder.termination()
    supervisor.cleanup(
        ProcessTerminationIssuer.INVOKE_TEARDOWN,
        terminate=True,
    )
    assert child.wait(timeout=15) is not None
    assert supervisor.recorder.termination() == facts_after_act
    assert supervisor.recorder.resolved_issuer() is TerminationIssuer.SUPERVISOR_IDLE
    assert [record.acted for record in supervisor.observations] == [True]
    assert manager.termination_issuer(child.pid) == ProcessTerminationIssuer.SUPERVISOR_IDLE.value


def test_cleanup_suppresses_teardown_errors_only_when_asked() -> None:
    handle = SimpleNamespace(
        pid=9876543,
        poll=lambda: None,
        record_terminal_reason=lambda reason: None,
        terminate=lambda **kwargs: None,
    )

    def _raising_teardown(pid: int, *, issuer: ProcessTerminationIssuer) -> None:
        raise ProcessLookupError("tree already gone")

    supervisor = AttemptSupervisor(handle, process_teardown=_raising_subtree(_raising_teardown))
    supervisor.cleanup(ProcessTerminationIssuer.INVOKE_TEARDOWN)
    with pytest.raises(ProcessLookupError, match="tree already gone"):
        supervisor.cleanup(ProcessTerminationIssuer.INVOKE_TEARDOWN, suppress=False)


def test_cleanup_with_terminate_suppresses_signal_errors() -> None:
    def _raising_terminate(**kwargs: object) -> None:
        raise OSError("signal failed")

    handle = SimpleNamespace(
        pid=9876543,
        poll=lambda: None,
        record_terminal_reason=lambda reason: None,
        terminate=_raising_terminate,
    )
    calls: list[tuple[int, ProcessTerminationIssuer]] = []

    def _teardown(pid: int, *, issuer: ProcessTerminationIssuer) -> None:
        calls.append((pid, issuer))

    supervisor = AttemptSupervisor(handle, process_teardown=_raising_subtree(_teardown))
    supervisor.cleanup(ProcessTerminationIssuer.INVOKE_TEARDOWN, terminate=True)
    assert calls == [(9876543, ProcessTerminationIssuer.INVOKE_TEARDOWN)]


def test_cleanup_runs_even_when_an_act_already_won() -> None:
    manager = ProcessManager()
    child = _sleeping_child(manager)
    supervisor = AttemptSupervisor(child)
    outcome = supervisor.act(ProviderErrorObserved(_QUOTA_DETAIL))
    assert outcome is not None
    supervisor.cleanup(ProcessTerminationIssuer.INVOKE_TEARDOWN)
    assert child.wait(timeout=15) is not None
    assert len(supervisor.observations) == 1


class _ProcessTeardownStub:
    def __init__(self, fn: TeardownFn) -> None:
        self._fn = fn

    def teardown_subtree(
        self,
        host_pid: int,
        *,
        issuer: ProcessTerminationIssuer,
        owner_label: str = "invoke",
    ) -> None:
        del owner_label
        self._fn(host_pid, issuer=issuer)


def _raising_subtree(fn: TeardownFn) -> ProcessTeardown:
    return _ProcessTeardownStub(fn)


def test_resolve_teardown_uses_the_injected_process_teardown() -> None:
    manager = ProcessManager()
    child = _sleeping_child(manager)
    calls: list[tuple[int, ProcessTerminationIssuer]] = []

    def _record(pid: int, *, issuer: ProcessTerminationIssuer) -> None:
        calls.append((pid, issuer))

    injected = _ProcessTeardownStub(_record)
    supervisor = AttemptSupervisor(child, process_teardown=injected)
    try:
        supervisor.cleanup(ProcessTerminationIssuer.INVOKE_TEARDOWN)
        assert calls == [(child.pid, ProcessTerminationIssuer.INVOKE_TEARDOWN)]
    finally:
        child.kill(issuer=ProcessTerminationIssuer.OPERATOR)
        child.wait(timeout=15)


def test_resolve_teardown_defaults_to_the_module_reference() -> None:
    assert resolve_teardown(None, teardown_subtree) is teardown_subtree
