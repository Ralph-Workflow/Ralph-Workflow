from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from ralph.agents.idle_watchdog import PostExitVerdict, WatchdogFireReason
from ralph.agents.invoke.attempt_supervision import (
    AttemptSupervisor,
    BrokenAgentTimeoutObserved,
    CompletionObserved,
    InteractiveCompletionObserved,
    OperatorInterruptObserved,
    PostExitHangObserved,
    ProviderErrorObserved,
    SilenceObserved,
    StartupFailureObserved,
    TeardownFn,
    TerminalStartupErrorObserved,
)
from ralph.agents.invoke.termination_recorder import TerminationRecorder
from ralph.process.manager import (
    ManagedProcess,
    ProcessManager,
    ProcessTerminationIssuer,
    SpawnOptions,
)
from ralph.recovery.attempt_facts import TerminationIssuer

if TYPE_CHECKING:
    from ralph.process.teardown import ProcessTeardown

_QUOTA_DETAIL = "RESOURCE_EXHAUSTED (code 429)\n"


def _spawn(manager: ProcessManager, script: str) -> ManagedProcess:
    return manager.spawn(
        [sys.executable, "-c", script],
        SpawnOptions(label="attempt-supervision"),
    )


def _sleeping_child(manager: ProcessManager) -> ManagedProcess:
    return _spawn(manager, "import time; time.sleep(30)")


def test_provider_error_observation_kills_with_quota_detector() -> None:
    manager = ProcessManager()
    child = _sleeping_child(manager)
    supervisor = AttemptSupervisor(child)
    outcome = supervisor.act(ProviderErrorObserved(_QUOTA_DETAIL))
    assert outcome is not None
    assert outcome.issuer is TerminationIssuer.QUOTA_DETECTOR
    assert manager.list_termination_outcomes()[child.pid] == [
        {"stage": "terminal_reason", "outcome": "quota_exhausted"}
    ]
    record = supervisor.recorder.termination()
    assert record.issuer is TerminationIssuer.QUOTA_DETECTOR
    assert record.liveness_observed is True
    assert [entry.issuer for entry in record.log] == [TerminationIssuer.QUOTA_DETECTOR]
    assert supervisor.winner is not None and supervisor.winner.acted is True
    assert outcome.returncode == child.wait(timeout=15)


@pytest.mark.parametrize(
    ("fire_reason", "process_issuer"),
    [
        (WatchdogFireReason.NO_OUTPUT_DEADLINE, ProcessTerminationIssuer.SUPERVISOR_IDLE),
        (WatchdogFireReason.PROCESS_EXIT_HANG, ProcessTerminationIssuer.POST_EXIT_WATCHDOG),
        (WatchdogFireReason.OPERATOR_CAP_REACHED, ProcessTerminationIssuer.OPERATOR),
        ("no_output_deadline", ProcessTerminationIssuer.SUPERVISOR_IDLE),
    ],
)
def test_silence_observation_kills_with_fire_issuer(
    fire_reason: WatchdogFireReason | str,
    process_issuer: ProcessTerminationIssuer,
) -> None:
    manager = ProcessManager()
    child = _sleeping_child(manager)
    supervisor = AttemptSupervisor(child)
    outcome = supervisor.act(SilenceObserved(fire_reason))
    assert outcome is not None
    assert manager.termination_issuer(child.pid) == process_issuer.value
    record = supervisor.recorder.termination()
    assert record.issuer is outcome.issuer
    assert record.liveness_observed is True
    assert len(record.log) == 1
    assert outcome.returncode == child.wait(timeout=15)


def test_broken_agent_timeout_observation_kills_with_timer_issuer() -> None:
    manager = ProcessManager()
    child = _sleeping_child(manager)
    supervisor = AttemptSupervisor(child)
    outcome = supervisor.act(BrokenAgentTimeoutObserved(has_output=False))
    assert outcome is not None
    assert outcome.issuer is TerminationIssuer.SUPERVISOR_IDLE
    assert (
        manager.termination_issuer(child.pid) == ProcessTerminationIssuer.BROKEN_AGENT_TIMER.value
    )
    record = supervisor.recorder.termination()
    assert record.issuer is TerminationIssuer.SUPERVISOR_IDLE
    assert record.liveness_observed is True
    assert outcome.returncode == child.wait(timeout=15)


def test_completion_observation_kills_with_invoke_teardown() -> None:
    manager = ProcessManager()
    child = _sleeping_child(manager)
    supervisor = AttemptSupervisor(child)
    outcome = supervisor.act(CompletionObserved())
    assert outcome is not None
    assert outcome.issuer is TerminationIssuer.AGENT_EXIT
    assert manager.termination_issuer(child.pid) == ProcessTerminationIssuer.INVOKE_TEARDOWN.value
    record = supervisor.recorder.termination()
    assert record.issuer is TerminationIssuer.AGENT_EXIT
    assert record.liveness_observed is True
    assert outcome.returncode == child.wait(timeout=15)


def test_interactive_completion_exited_child_is_not_signalled() -> None:
    manager = ProcessManager()
    child = _spawn(manager, "import time; time.sleep(0.05)")
    supervisor = AttemptSupervisor(child)
    outcome = supervisor.act(InteractiveCompletionObserved(grace_s=30.0))
    assert outcome is not None
    assert outcome.returncode == 0
    assert outcome.issuer is None
    assert manager.list_termination_outcomes()[child.pid] == [
        {"stage": "terminal_reason", "outcome": "interactive_completion"}
    ]
    record = supervisor.recorder.termination()
    assert record.log == ()
    assert record.liveness_observed is None
    assert supervisor.winner is not None and supervisor.winner.acted is True
    assert child.wait(timeout=15) == 0


def test_interactive_completion_live_child_is_signalled() -> None:
    manager = ProcessManager()
    child = _sleeping_child(manager)
    supervisor = AttemptSupervisor(child)
    outcome = supervisor.act(InteractiveCompletionObserved(grace_s=0.2))
    assert outcome is not None
    assert outcome.issuer is None
    assert manager.list_termination_outcomes()[child.pid] == [
        {"stage": "terminal_reason", "outcome": "interactive_completion"}
    ]
    record = supervisor.recorder.termination()
    assert record.log == ()
    assert record.liveness_observed is None
    assert outcome.returncode == -15
    assert outcome.returncode == child.wait(timeout=15)


def test_terminal_startup_error_observation_reaps_without_attempt_issuer() -> None:
    manager = ProcessManager()
    child = _sleeping_child(manager)
    supervisor = AttemptSupervisor(child)
    outcome = supervisor.act(TerminalStartupErrorObserved("menu never appeared"))
    assert outcome is not None
    assert outcome.issuer is None
    assert manager.list_termination_outcomes()[child.pid] == [
        {"stage": "terminal_reason", "outcome": "terminal_startup_error"}
    ]
    assert supervisor.recorder.termination().log == ()
    assert child.wait(timeout=15) is not None


def test_startup_failure_observation_reaps_and_appends_nothing() -> None:
    manager = ProcessManager()
    child = _sleeping_child(manager)
    supervisor = AttemptSupervisor(child)
    outcome = supervisor.act(StartupFailureObserved("Failed to capture stdout"))
    assert outcome is not None
    assert outcome.issuer is None
    assert manager.termination_issuer(child.pid) == ProcessTerminationIssuer.INVOKE_TEARDOWN.value
    record = supervisor.recorder.termination()
    assert record.log == ()
    assert record.issuer is TerminationIssuer.UNKNOWN
    assert record.liveness_observed is None
    assert child.wait(timeout=15) is not None


def test_operator_interrupt_observation_kills_with_operator_issuer() -> None:
    manager = ProcessManager()
    child = _sleeping_child(manager)
    supervisor = AttemptSupervisor(child)
    outcome = supervisor.act(OperatorInterruptObserved())
    assert outcome is not None
    assert outcome.issuer is None
    assert manager.list_termination_outcomes()[child.pid] == [
        {"stage": "terminal_reason", "outcome": "operator_cancellation"}
    ]
    assert supervisor.recorder.termination().log == ()
    assert child.wait(timeout=15) is not None


def test_post_exit_hang_observation_kills_with_watchdog_issuer() -> None:
    manager = ProcessManager()
    child = _sleeping_child(manager)
    supervisor = AttemptSupervisor(child)
    outcome = supervisor.act(PostExitHangObserved(PostExitVerdict.FIRE_DESCENDANT_HANG))
    assert outcome is not None
    assert outcome.issuer is TerminationIssuer.POST_EXIT_WATCHDOG
    assert (
        manager.termination_issuer(child.pid) == ProcessTerminationIssuer.POST_EXIT_WATCHDOG.value
    )
    record = supervisor.recorder.termination()
    assert record.issuer is TerminationIssuer.POST_EXIT_WATCHDOG
    assert [entry.issuer for entry in record.log] == [TerminationIssuer.POST_EXIT_WATCHDOG]
    assert record.liveness_observed is True
    assert record.observed_returncode is not None
    assert record.observed_returncode == record.reported_returncode
    assert outcome.returncode == child.wait(timeout=15)


def test_first_acting_observation_wins_and_later_acts_are_noops() -> None:
    manager = ProcessManager()
    child = _sleeping_child(manager)
    supervisor = AttemptSupervisor(child)
    first = supervisor.act(CompletionObserved())
    assert first is not None
    facts_after_first = supervisor.recorder.termination()
    second = supervisor.act(ProviderErrorObserved(_QUOTA_DETAIL))
    third = supervisor.act(PostExitHangObserved(PostExitVerdict.FIRE_DESCENDANT_HANG))
    assert second is None
    assert third is None
    assert supervisor.recorder.resolved_issuer() is TerminationIssuer.AGENT_EXIT
    assert len(supervisor.recorder.termination().log) == 1
    assert supervisor.recorder.termination() == facts_after_first
    assert [record.acted for record in supervisor.observations] == [True, False, False]
    winner = supervisor.winner
    assert winner is not None
    assert isinstance(winner.observation, CompletionObserved)
    assert manager.termination_issuer(child.pid) == ProcessTerminationIssuer.INVOKE_TEARDOWN.value
    assert child.wait(timeout=15) is not None


def test_non_evidence_provider_error_records_nothing_and_leaves_child_alive() -> None:
    manager = ProcessManager()
    child = _sleeping_child(manager)
    try:
        supervisor = AttemptSupervisor(child)
        outcome = supervisor.act(
            ProviderErrorObserved('{"type":"message","role":"assistant","content":"hi"}'),
        )
        assert outcome is None
        assert supervisor.observations == ()
        assert supervisor.recorder.termination().log == ()
        assert child.poll() is None
    finally:
        child.kill(issuer=ProcessTerminationIssuer.OPERATOR)
        child.wait(timeout=15)


def test_for_recorder_returns_the_live_owner() -> None:
    supervisor = AttemptSupervisor(object())
    assert AttemptSupervisor.for_recorder(supervisor.recorder, object()) is supervisor


def test_for_recorder_with_foreign_recorder_builds_a_new_owner() -> None:
    supervisor = AttemptSupervisor(object())
    foreign = TerminationRecorder()
    other = AttemptSupervisor.for_recorder(foreign, object())
    assert other is not supervisor
    assert other.recorder is foreign


def test_for_recorder_with_none_builds_a_fresh_recorder() -> None:
    supervisor = AttemptSupervisor(object())
    standalone = AttemptSupervisor.for_recorder(None, object())
    assert standalone is not supervisor
    assert standalone.recorder is not supervisor.recorder


def test_winner_is_none_until_an_observation_acts() -> None:
    supervisor = AttemptSupervisor(object())
    assert supervisor.winner is None
    supervisor.act(CompletionObserved())
    winner = supervisor.winner
    assert winner is not None
    assert winner.acted is True
    assert isinstance(winner.observation, CompletionObserved)
    assert len(supervisor.observations) == 1


def _dead_handle(terminate_error: type[BaseException]) -> SimpleNamespace:
    def _terminate(**kwargs: object) -> None:
        raise terminate_error("process is gone")

    return SimpleNamespace(
        pid=1234567,
        poll=lambda: 1,
        record_terminal_reason=lambda reason: None,
        close=lambda: None,
        terminate=_terminate,
    )


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


def _process_teardown_wrapping(fn: TeardownFn) -> ProcessTeardown:
    return _ProcessTeardownStub(fn)


def test_operator_interrupt_on_a_dead_handle_raises_nothing() -> None:
    calls: list[tuple[int, ProcessTerminationIssuer]] = []

    def _teardown(pid: int, *, issuer: ProcessTerminationIssuer) -> None:
        calls.append((pid, issuer))

    supervisor = AttemptSupervisor(
        _dead_handle(ProcessLookupError), process_teardown=_process_teardown_wrapping(_teardown)
    )
    outcome = supervisor.act(OperatorInterruptObserved())
    assert outcome is not None
    assert calls == [(1234567, ProcessTerminationIssuer.OPERATOR)]


def test_terminal_startup_error_on_a_dead_handle_raises_nothing() -> None:
    calls: list[tuple[int, ProcessTerminationIssuer]] = []

    def _teardown(pid: int, *, issuer: ProcessTerminationIssuer) -> None:
        calls.append((pid, issuer))

    supervisor = AttemptSupervisor(
        _dead_handle(ProcessLookupError), process_teardown=_process_teardown_wrapping(_teardown)
    )
    outcome = supervisor.act(TerminalStartupErrorObserved("pty closed"))
    assert outcome is not None
    assert calls == [(1234567, ProcessTerminationIssuer.INVOKE_TEARDOWN)]


def test_terminal_startup_error_teardown_errors_propagate() -> None:
    handle = SimpleNamespace(
        pid=7654321,
        poll=lambda: 1,
        record_terminal_reason=lambda reason: None,
        terminate=lambda **kwargs: None,
    )

    def _raising_teardown(pid: int, *, issuer: ProcessTerminationIssuer) -> None:
        raise RuntimeError("reap failed")

    supervisor = AttemptSupervisor(
        handle, process_teardown=_process_teardown_wrapping(_raising_teardown)
    )
    with pytest.raises(RuntimeError, match="reap failed"):
        supervisor.act(TerminalStartupErrorObserved("pty closed"))


def test_provider_error_propagates_signal_errors() -> None:
    supervisor = AttemptSupervisor(_dead_handle(OSError))
    with pytest.raises(OSError, match="process is gone"):
        supervisor.act(ProviderErrorObserved(_QUOTA_DETAIL))
