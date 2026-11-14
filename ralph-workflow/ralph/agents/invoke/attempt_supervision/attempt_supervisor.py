from __future__ import annotations

import contextlib
import subprocess
import threading
import weakref
from collections import deque
from typing import TYPE_CHECKING, cast

from ralph.agents.invoke._quota_detection import is_quota_kill_evidence
from ralph.agents.invoke.attempt_supervision import _issuer_mapping
from ralph.agents.invoke.attempt_supervision._signal_fn import _SignalFn
from ralph.agents.invoke.attempt_supervision._wait_fn import _WaitFn
from ralph.agents.invoke.attempt_supervision.observation_record import ObservationRecord
from ralph.agents.invoke.attempt_supervision.post_exit_hang_observed import PostExitHangObserved
from ralph.agents.invoke.attempt_supervision.provider_error_observed import ProviderErrorObserved
from ralph.agents.invoke.attempt_supervision.teardown_fn import TeardownFn, resolve_teardown
from ralph.agents.invoke.attempt_supervision.termination_outcome import TerminationOutcome
from ralph.agents.invoke.termination_recorder import TerminationRecorder, observed_returncode
from ralph.process.teardown import teardown_subtree

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.agents.invoke.attempt_supervision.attempt_observation import AttemptObservation
    from ralph.process.manager.process_termination_issuer import ProcessTerminationIssuer
    from ralph.process.teardown import ProcessTeardown
    from ralph.recovery.attempt_facts import TerminationIssuer

_OWNERS_BY_RECORDER_ID: weakref.WeakValueDictionary[int, AttemptSupervisor] = (
    weakref.WeakValueDictionary()
)


def _signal_fn_of(handle: object) -> _SignalFn | None:
    candidate = cast("object", getattr(handle, "terminate", None))
    return candidate if isinstance(candidate, _SignalFn) else None


def _wait_fn_of(handle: object) -> _WaitFn | None:
    candidate = cast("object", getattr(handle, "wait", None))
    return candidate if isinstance(candidate, _WaitFn) else None


def _handle_pid(handle: object) -> int | None:
    pid = cast("object", getattr(handle, "pid", None))
    if isinstance(pid, bool) or not isinstance(pid, int):
        return None
    return pid


_POLLED_WITHOUT_RETURNCODE = object()
_RETURNCODE_NOT_POLLED = object()


def _coerce_returncode(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _recorded_returncode(handle: object) -> int | None:
    return _coerce_returncode(cast("object", getattr(handle, "returncode", None)))


class AttemptSupervisor:
    def __init__(
        self,
        handle: object,
        *,
        recorder: TerminationRecorder | None = None,
        process_teardown: ProcessTeardown | None = None,
    ) -> None:
        self._handle = handle
        self._recorder = recorder if recorder is not None else TerminationRecorder()
        self._teardown: TeardownFn = resolve_teardown(process_teardown, teardown_subtree)
        self._lock = threading.Lock()
        self._winner: ObservationRecord | None = None
        self._observations: deque[ObservationRecord] = deque(maxlen=128)
        _OWNERS_BY_RECORDER_ID[id(self._recorder)] = self

    @classmethod
    def for_recorder(
        cls,
        recorder: TerminationRecorder | None,
        handle: object,
        *,
        process_teardown: ProcessTeardown | None = None,
    ) -> AttemptSupervisor:
        if recorder is not None:
            owner = _OWNERS_BY_RECORDER_ID.get(id(recorder))
            if owner is not None and owner.recorder is recorder:
                return owner
        return cls(handle, recorder=recorder, process_teardown=process_teardown)

    @property
    def recorder(self) -> TerminationRecorder:
        return self._recorder

    @property
    def teardown_subtree(self) -> TeardownFn:
        return self._teardown

    @property
    def winner(self) -> ObservationRecord | None:
        return self._winner

    @property
    def observations(self) -> tuple[ObservationRecord, ...]:
        return tuple(self._observations)

    def act(self, observation: AttemptObservation) -> TerminationOutcome | None:
        with self._lock:
            if self._claim_is_a_noop(observation):
                return None
            record = ObservationRecord(observation, acted=True)
            self._observations.append(record)
            self._winner = record
            attempt_issuer = _issuer_mapping.attempt_issuer_for(observation)
            self._record_attempt_issuer(observation, attempt_issuer)
            reason = _issuer_mapping.terminal_reason_for(observation, self._handle)
            self._record_terminal_reason(reason)
            process_issuer = _issuer_mapping.process_issuer_for(observation)
            self._close_if_declared(observation)
            signal_result = self._signal_if_alive(observation, process_issuer, reason)
            self._teardown_if_pid(observation, self._teardown, process_issuer)
            if signal_result is _RETURNCODE_NOT_POLLED:
                returncode: int | None = observed_returncode(self._handle)
            elif signal_result is _POLLED_WITHOUT_RETURNCODE:
                returncode = _recorded_returncode(self._handle)
            else:
                returncode = _coerce_returncode(signal_result)
            self._recorder.record_observed_returncode(returncode)
            if _issuer_mapping.records_reported_returncode(observation):
                self._recorder.record_reported_returncode(returncode)
            return TerminationOutcome(issuer=attempt_issuer, returncode=returncode)

    def teardown_observed_subtree(
        self, observation: AttemptObservation
    ) -> TerminationOutcome | None:
        if _issuer_mapping.suppresses_teardown(observation):
            raise ValueError("observation does not permit subtree teardown")
        return self.act(observation)

    teardown_subtree_for_observation = teardown_observed_subtree

    act_with_teardown = teardown_observed_subtree

    def cleanup(
        self,
        issuer: ProcessTerminationIssuer,
        *,
        terminate: bool = False,
        reason: str | None = None,
        suppress: bool = True,
    ) -> None:
        with self._lock:
            if terminate:
                terminate_fn = _signal_fn_of(self._handle)
                if terminate_fn is not None:
                    if suppress:
                        with contextlib.suppress(Exception):
                            self._terminate(terminate_fn, issuer, reason)
                    else:
                        self._terminate(terminate_fn, issuer, reason)
            pid = _handle_pid(self._handle)
            if pid is not None:
                if suppress:
                    with contextlib.suppress(Exception):
                        self._teardown(pid, issuer=issuer)
                else:
                    self._teardown(pid, issuer=issuer)

    def _claim_is_a_noop(self, observation: AttemptObservation) -> bool:
        if isinstance(observation, ProviderErrorObserved) and not is_quota_kill_evidence(
            observation.detail
        ):
            return True
        if self._winner is not None:
            self._observations.append(ObservationRecord(observation, acted=False))
            return True
        return False

    def _record_attempt_issuer(
        self, observation: AttemptObservation, attempt_issuer: TerminationIssuer | None
    ) -> None:
        if isinstance(observation, PostExitHangObserved):
            self._recorder.record_post_exit_watchdog_fire()
            return
        if attempt_issuer is not None:
            self._recorder.record_issuer(attempt_issuer)
        self._recorder.record_liveness(_issuer_mapping.liveness_for(observation))

    def _record_terminal_reason(self, reason: str | None) -> None:
        if reason is None:
            return
        record_reason = cast(
            "Callable[[str], None] | None",
            getattr(self._handle, "record_terminal_reason", None),
        )
        if record_reason is not None:
            record_reason(reason)

    def _close_if_declared(self, observation: AttemptObservation) -> None:
        if not _issuer_mapping.closes_before_signal(observation):
            return
        close_fn = cast("Callable[[], None] | None", getattr(self._handle, "close", None))
        if close_fn is not None:
            with contextlib.suppress(Exception):
                close_fn()

    def _signal_if_alive(
        self,
        observation: AttemptObservation,
        process_issuer: ProcessTerminationIssuer,
        reason: str | None,
    ) -> object:
        waits_for_exit = _issuer_mapping.waits_for_exit_before_signal(observation)
        if waits_for_exit:
            self._wait_for_grace(observation)
        terminate_fn = _signal_fn_of(self._handle)
        if terminate_fn is None:
            return _RETURNCODE_NOT_POLLED
        if not waits_for_exit:
            self._signal_with_suppression(observation, terminate_fn, process_issuer, reason)
            return _RETURNCODE_NOT_POLLED
        poll_fn = cast("Callable[[], int | None] | None", getattr(self._handle, "poll", None))
        if poll_fn is None:
            return _POLLED_WITHOUT_RETURNCODE
        with contextlib.suppress(*_issuer_mapping._SIGNAL_WAIT_ERRORS):
            returncode = poll_fn()
            if returncode is None:
                self._signal_with_suppression(observation, terminate_fn, process_issuer, reason)
                return _POLLED_WITHOUT_RETURNCODE
            return returncode
        return _POLLED_WITHOUT_RETURNCODE

    def _signal_with_suppression(
        self,
        observation: AttemptObservation,
        terminate_fn: _SignalFn,
        process_issuer: ProcessTerminationIssuer,
        reason: str | None,
    ) -> None:
        if _issuer_mapping.suppresses_terminate(observation):
            with contextlib.suppress(*_issuer_mapping._SIGNAL_WAIT_ERRORS):
                self._terminate(terminate_fn, process_issuer, reason)
        else:
            self._terminate(terminate_fn, process_issuer, reason)

    def _teardown_if_pid(
        self,
        observation: AttemptObservation,
        teardown: TeardownFn,
        process_issuer: ProcessTerminationIssuer,
    ) -> None:
        pid = _handle_pid(self._handle)
        if pid is None:
            return
        if _issuer_mapping.suppresses_teardown(observation):
            with contextlib.suppress(Exception):
                teardown(pid, issuer=process_issuer)
        else:
            teardown(pid, issuer=process_issuer)

    def _wait_for_grace(self, observation: AttemptObservation) -> None:
        wait_fn = _wait_fn_of(self._handle)
        if wait_fn is None:
            return
        with contextlib.suppress(
            *_issuer_mapping._SIGNAL_WAIT_ERRORS, TimeoutError, subprocess.TimeoutExpired
        ):
            wait_fn(_issuer_mapping.grace_wait_seconds(observation))

    def _terminate(
        self,
        terminate_fn: _SignalFn,
        issuer: ProcessTerminationIssuer,
        reason: str | None,
    ) -> None:
        if reason is None:
            terminate_fn(grace_period_s=_issuer_mapping._SIGNAL_GRACE_SECONDS, issuer=issuer)
        else:
            terminate_fn(
                grace_period_s=_issuer_mapping._SIGNAL_GRACE_SECONDS,
                issuer=issuer,
                reason=reason,
            )


__all__ = ["AttemptSupervisor"]
