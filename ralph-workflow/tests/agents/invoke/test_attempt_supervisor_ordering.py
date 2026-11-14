from __future__ import annotations

from ralph.agents.invoke.attempt_supervision import (
    AttemptSupervisor,
    OperatorInterruptObserved,
    ProviderErrorObserved,
)
from ralph.process.manager.process_termination_issuer import ProcessTerminationIssuer

_QUOTA_DETAIL = "RESOURCE_EXHAUSTED (code 429)\n"


class _OrderRecordingHandle:
    def __init__(self, *, close_raises: bool = False, terminate_raises: bool = False) -> None:
        self.pid = 7654321
        self.events: list[tuple[str, ...]] = []
        self._close_raises = close_raises
        self._terminate_raises = terminate_raises

    def record_terminal_reason(self, reason: str) -> None:
        self.events.append(("reason", reason))

    def close(self) -> None:
        self.events.append(("close",))
        if self._close_raises:
            raise OSError("handle already closed")

    def terminate(self, grace_period_s: float = 0.5, **kwargs: object) -> None:
        self.events.append(("terminate", str(kwargs.get("issuer")), str(kwargs.get("reason"))))
        if self._terminate_raises:
            raise ProcessLookupError("process already reaped")

    def poll(self) -> int | None:
        return None


class _RecordingTeardown:
    def __init__(self) -> None:
        self.calls: list[tuple[int, ProcessTerminationIssuer]] = []

    def teardown_subtree(
        self, host_pid: int, *, issuer: ProcessTerminationIssuer, owner_label: str = "invoke"
    ) -> None:
        del owner_label
        self.calls.append((host_pid, issuer))


def test_operator_interrupt_orders_reason_close_terminate_then_teardown() -> None:
    handle = _OrderRecordingHandle()
    teardown = _RecordingTeardown()
    supervisor = AttemptSupervisor(handle, process_teardown=teardown)
    outcome = supervisor.act(OperatorInterruptObserved())
    assert outcome is not None
    assert handle.events == [
        ("reason", "operator_cancellation"),
        ("close",),
        ("terminate", "operator", "operator_cancellation"),
    ]
    assert teardown.calls == [(handle.pid, ProcessTerminationIssuer.OPERATOR)]


def test_provider_error_orders_reason_before_terminate_without_close() -> None:
    handle = _OrderRecordingHandle()
    teardown = _RecordingTeardown()
    supervisor = AttemptSupervisor(handle, process_teardown=teardown)
    outcome = supervisor.act(ProviderErrorObserved(_QUOTA_DETAIL))
    assert outcome is not None
    assert handle.events == [
        ("reason", "quota_exhausted"),
        ("terminate", "quota_detector", "quota_exhausted"),
    ]
    assert teardown.calls == [(handle.pid, ProcessTerminationIssuer.QUOTA_DETECTOR)]


def test_raising_close_still_terminates_and_reaps() -> None:
    handle = _OrderRecordingHandle(close_raises=True)
    teardown = _RecordingTeardown()
    supervisor = AttemptSupervisor(handle, process_teardown=teardown)
    outcome = supervisor.act(OperatorInterruptObserved())
    assert outcome is not None
    assert [event[0] for event in handle.events] == ["reason", "close", "terminate"]
    assert teardown.calls == [(handle.pid, ProcessTerminationIssuer.OPERATOR)]


def test_raising_terminate_still_reaps() -> None:
    handle = _OrderRecordingHandle(terminate_raises=True)
    teardown = _RecordingTeardown()
    supervisor = AttemptSupervisor(handle, process_teardown=teardown)
    outcome = supervisor.act(OperatorInterruptObserved())
    assert outcome is not None
    assert [event[0] for event in handle.events] == ["reason", "close", "terminate"]
    assert teardown.calls == [(handle.pid, ProcessTerminationIssuer.OPERATOR)]


def test_losing_observation_never_overwrites_reason_or_signals_again() -> None:
    handle = _OrderRecordingHandle()
    teardown = _RecordingTeardown()
    supervisor = AttemptSupervisor(handle, process_teardown=teardown)
    first = supervisor.act(ProviderErrorObserved(_QUOTA_DETAIL))
    assert first is not None
    second = supervisor.act(OperatorInterruptObserved())
    assert second is None
    reasons = [event for event in handle.events if event[0] == "reason"]
    assert reasons == [("reason", "quota_exhausted")]
    closes = [event for event in handle.events if event[0] == "close"]
    assert closes == []
    terminates = [event for event in handle.events if event[0] == "terminate"]
    assert len(terminates) == 1
    assert len(teardown.calls) == 1
    assert [record.acted for record in supervisor.observations] == [True, False]
