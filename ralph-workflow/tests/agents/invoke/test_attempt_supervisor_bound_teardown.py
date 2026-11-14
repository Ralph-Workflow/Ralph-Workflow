from __future__ import annotations

from types import SimpleNamespace

from ralph.agents.invoke.attempt_supervision import (
    AttemptSupervisor,
    OperatorInterruptObserved,
)
from ralph.agents.invoke.termination_recorder import TerminationRecorder
from ralph.process.manager import ProcessTerminationIssuer

_PID = 1234567


class _RecordingTeardown:
    def __init__(self) -> None:
        self.calls: list[tuple[int, ProcessTerminationIssuer]] = []

    def teardown_subtree(
        self, host_pid: int, *, issuer: ProcessTerminationIssuer, owner_label: str = "invoke"
    ) -> None:
        self.calls.append((host_pid, issuer))


def _handle() -> SimpleNamespace:
    return SimpleNamespace(
        pid=_PID,
        poll=lambda: 1,
        record_terminal_reason=lambda reason: None,
        close=lambda: None,
        terminate=lambda **kwargs: None,
    )


def test_act_uses_bound_teardown_without_per_call_override() -> None:
    fake = _RecordingTeardown()
    supervisor = AttemptSupervisor(_handle(), process_teardown=fake)
    outcome = supervisor.act(OperatorInterruptObserved())
    assert outcome is not None
    assert fake.calls == [(_PID, ProcessTerminationIssuer.OPERATOR)]


def test_cleanup_uses_bound_teardown_without_per_call_override() -> None:
    fake = _RecordingTeardown()
    supervisor = AttemptSupervisor(_handle(), process_teardown=fake)
    supervisor.cleanup(ProcessTerminationIssuer.INVOKE_TEARDOWN)
    assert fake.calls == [(_PID, ProcessTerminationIssuer.INVOKE_TEARDOWN)]


def test_property_teardown_subtree_invokes_injected_teardown_fn() -> None:
    fake = _RecordingTeardown()
    supervisor = AttemptSupervisor(_handle(), process_teardown=fake)

    supervisor.teardown_subtree(4242, issuer=ProcessTerminationIssuer.INVOKE_TEARDOWN)

    assert fake.calls == [(4242, ProcessTerminationIssuer.INVOKE_TEARDOWN)]


def test_for_recorder_passes_seam_through_and_first_construction_wins() -> None:
    recorder = TerminationRecorder()
    first = _RecordingTeardown()
    owner = AttemptSupervisor.for_recorder(recorder, _handle(), process_teardown=first)
    replacement = _RecordingTeardown()
    again = AttemptSupervisor.for_recorder(recorder, _handle(), process_teardown=replacement)
    assert again is owner
    owner.cleanup(ProcessTerminationIssuer.INVOKE_TEARDOWN)
    assert first.calls == [(_PID, ProcessTerminationIssuer.INVOKE_TEARDOWN)]
    assert replacement.calls == []
