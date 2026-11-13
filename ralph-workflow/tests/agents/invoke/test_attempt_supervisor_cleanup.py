from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import cast

import pytest

from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.invoke.attempt_supervision import (
    AttemptSupervisor,
    ProviderErrorObserved,
    SilenceObserved,
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
    supervisor.cleanup(ProcessTerminationIssuer.INVOKE_TEARDOWN, teardown=teardown_subtree)
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
        teardown=teardown_subtree,
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
    outcome = supervisor.act(
        SilenceObserved(WatchdogFireReason.NO_OUTPUT_DEADLINE), teardown=teardown_subtree
    )
    assert outcome is not None
    facts_after_act = supervisor.recorder.termination()
    supervisor.cleanup(
        ProcessTerminationIssuer.INVOKE_TEARDOWN,
        teardown=teardown_subtree,
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
    supervisor = AttemptSupervisor(handle)

    def _raising_teardown(pid: int, *, issuer: ProcessTerminationIssuer) -> None:
        raise ProcessLookupError("tree already gone")

    supervisor.cleanup(ProcessTerminationIssuer.INVOKE_TEARDOWN, teardown=_raising_teardown)
    with pytest.raises(ProcessLookupError, match="tree already gone"):
        supervisor.cleanup(
            ProcessTerminationIssuer.INVOKE_TEARDOWN,
            teardown=_raising_teardown,
            suppress=False,
        )


def test_cleanup_with_terminate_suppresses_signal_errors() -> None:
    def _raising_terminate(**kwargs: object) -> None:
        raise OSError("signal failed")

    handle = SimpleNamespace(
        pid=9876543,
        poll=lambda: None,
        record_terminal_reason=lambda reason: None,
        terminate=_raising_terminate,
    )
    supervisor = AttemptSupervisor(handle)
    calls: list[tuple[int, ProcessTerminationIssuer]] = []

    def _teardown(pid: int, *, issuer: ProcessTerminationIssuer) -> None:
        calls.append((pid, issuer))

    supervisor.cleanup(
        ProcessTerminationIssuer.INVOKE_TEARDOWN,
        teardown=_teardown,
        terminate=True,
    )
    assert calls == [(9876543, ProcessTerminationIssuer.INVOKE_TEARDOWN)]


def test_cleanup_runs_even_when_an_act_already_won() -> None:
    manager = ProcessManager()
    child = _sleeping_child(manager)
    supervisor = AttemptSupervisor(child)
    outcome = supervisor.act(
        ProviderErrorObserved(_QUOTA_DETAIL),
        teardown=teardown_subtree,
    )
    assert outcome is not None
    supervisor.cleanup(ProcessTerminationIssuer.INVOKE_TEARDOWN, teardown=teardown_subtree)
    assert child.wait(timeout=15) is not None
    assert len(supervisor.observations) == 1


def test_resolve_teardown_uses_the_injected_process_teardown() -> None:
    manager = ProcessManager()
    child = _sleeping_child(manager)
    calls: list[tuple[int, ProcessTerminationIssuer]] = []
    injected = cast(
        "ProcessTeardown",
        SimpleNamespace(teardown_subtree=lambda pid, *, issuer: calls.append((pid, issuer))),
    )
    supervisor = AttemptSupervisor(child)
    try:
        supervisor.cleanup(
            ProcessTerminationIssuer.INVOKE_TEARDOWN,
            teardown=resolve_teardown(injected, teardown_subtree),
        )
        assert calls == [(child.pid, ProcessTerminationIssuer.INVOKE_TEARDOWN)]
    finally:
        child.kill(issuer=ProcessTerminationIssuer.OPERATOR)
        child.wait(timeout=15)


def test_resolve_teardown_defaults_to_the_module_reference() -> None:
    assert resolve_teardown(None, teardown_subtree) is teardown_subtree
