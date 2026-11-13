from __future__ import annotations

from typing import TYPE_CHECKING, Final

from ralph.agents.idle_watchdog import PostExitVerdict, WatchdogFireReason
from ralph.agents.invoke._fire_reason_issuer import termination_issuer_for_fire_reason
from ralph.agents.invoke.attempt_supervision.broken_agent_timeout_observed import (
    BrokenAgentTimeoutObserved,
)
from ralph.agents.invoke.attempt_supervision.completion_observed import CompletionObserved
from ralph.agents.invoke.attempt_supervision.interactive_completion_observed import (
    InteractiveCompletionObserved,
)
from ralph.agents.invoke.attempt_supervision.operator_interrupt_observed import (
    OperatorInterruptObserved,
)
from ralph.agents.invoke.attempt_supervision.post_exit_hang_observed import PostExitHangObserved
from ralph.agents.invoke.attempt_supervision.provider_error_observed import ProviderErrorObserved
from ralph.agents.invoke.attempt_supervision.silence_observed import SilenceObserved
from ralph.agents.invoke.attempt_supervision.terminal_startup_error_observed import (
    TerminalStartupErrorObserved,
)
from ralph.process.manager import ManagedPtyProcess
from ralph.process.manager.process_termination_issuer import ProcessTerminationIssuer
from ralph.recovery.attempt_facts import TerminationIssuer

if TYPE_CHECKING:
    from ralph.agents.invoke.attempt_supervision.attempt_observation import AttemptObservation

_FIRE_REASON_PROCESS_ISSUERS: Final[dict[TerminationIssuer, ProcessTerminationIssuer]] = {
    TerminationIssuer.SUPERVISOR_IDLE: ProcessTerminationIssuer.SUPERVISOR_IDLE,
    TerminationIssuer.QUOTA_DETECTOR: ProcessTerminationIssuer.QUOTA_DETECTOR,
    TerminationIssuer.RECOVERY_TIMEOUT: ProcessTerminationIssuer.RECOVERY_TIMEOUT,
    TerminationIssuer.POST_EXIT_WATCHDOG: ProcessTerminationIssuer.POST_EXIT_WATCHDOG,
    TerminationIssuer.OPERATOR: ProcessTerminationIssuer.OPERATOR,
}

_POST_EXIT_FIRE_VERDICTS: Final[frozenset[PostExitVerdict]] = frozenset(
    {PostExitVerdict.FIRE_DESCENDANT_HANG, PostExitVerdict.FIRE_PROCESS_EXIT_HANG}
)

_SIGNAL_GRACE_SECONDS: Final[float] = 0.5

_SIGNAL_WAIT_ERRORS: Final[tuple[type[BaseException], ...]] = (
    AttributeError,
    OSError,
    ProcessLookupError,
    RuntimeError,
)

_LIVE_RECORDING_ROWS: Final[tuple[type[AttemptObservation], ...]] = (
    BrokenAgentTimeoutObserved,
    CompletionObserved,
    PostExitHangObserved,
    ProviderErrorObserved,
    SilenceObserved,
)

_SUPPRESSED_TERMINATE_ROWS: Final[tuple[type[AttemptObservation], ...]] = (
    InteractiveCompletionObserved,
    OperatorInterruptObserved,
    TerminalStartupErrorObserved,
)

_SUPPRESSED_TEARDOWN_ROWS: Final[tuple[type[AttemptObservation], ...]] = (
    InteractiveCompletionObserved,
    OperatorInterruptObserved,
)


def process_issuer_for_fire_reason(
    reason: WatchdogFireReason | str | None,
) -> ProcessTerminationIssuer:
    if reason == WatchdogFireReason.OPERATOR_CAP_REACHED:
        return ProcessTerminationIssuer.OPERATOR
    issuer = termination_issuer_for_fire_reason(reason)
    return _FIRE_REASON_PROCESS_ISSUERS.get(issuer, ProcessTerminationIssuer.SUPERVISOR_IDLE)


def _teardown_issuer_for_verdict(verdict: PostExitVerdict) -> ProcessTerminationIssuer:
    if verdict in _POST_EXIT_FIRE_VERDICTS:
        return ProcessTerminationIssuer.POST_EXIT_WATCHDOG
    return ProcessTerminationIssuer.INVOKE_TEARDOWN


def attempt_issuer_for(observation: AttemptObservation) -> TerminationIssuer | None:
    if isinstance(observation, ProviderErrorObserved):
        return TerminationIssuer.QUOTA_DETECTOR
    if isinstance(observation, SilenceObserved):
        return termination_issuer_for_fire_reason(observation.fire_reason)
    if isinstance(observation, BrokenAgentTimeoutObserved):
        return TerminationIssuer.SUPERVISOR_IDLE
    if isinstance(observation, CompletionObserved):
        return TerminationIssuer.AGENT_EXIT
    if isinstance(observation, PostExitHangObserved):
        return TerminationIssuer.POST_EXIT_WATCHDOG
    return None


def process_issuer_for(observation: AttemptObservation) -> ProcessTerminationIssuer:
    if isinstance(observation, ProviderErrorObserved):
        return ProcessTerminationIssuer.QUOTA_DETECTOR
    if isinstance(observation, SilenceObserved):
        return process_issuer_for_fire_reason(observation.fire_reason)
    if isinstance(observation, BrokenAgentTimeoutObserved):
        return ProcessTerminationIssuer.BROKEN_AGENT_TIMER
    if isinstance(observation, OperatorInterruptObserved):
        return ProcessTerminationIssuer.OPERATOR
    if isinstance(observation, PostExitHangObserved):
        return ProcessTerminationIssuer.POST_EXIT_WATCHDOG
    return ProcessTerminationIssuer.INVOKE_TEARDOWN


def terminal_reason_for(observation: AttemptObservation, handle: object) -> str | None:
    if isinstance(observation, ProviderErrorObserved):
        return "quota_exhausted"
    if isinstance(observation, SilenceObserved) and isinstance(handle, ManagedPtyProcess):
        return "conflict_inactivity"
    if isinstance(observation, InteractiveCompletionObserved):
        return "interactive_completion"
    if isinstance(observation, TerminalStartupErrorObserved):
        return "terminal_startup_error"
    if isinstance(observation, OperatorInterruptObserved):
        return "operator_cancellation"
    return None


def liveness_for(observation: AttemptObservation) -> bool | None:
    if isinstance(observation, _LIVE_RECORDING_ROWS):
        return True
    return None


def closes_before_signal(observation: AttemptObservation) -> bool:
    return isinstance(observation, OperatorInterruptObserved)


def waits_for_exit_before_signal(observation: AttemptObservation) -> bool:
    return isinstance(observation, InteractiveCompletionObserved)


def grace_wait_seconds(observation: AttemptObservation) -> float:
    if isinstance(observation, InteractiveCompletionObserved):
        return observation.grace_s
    return 0.0


def suppresses_terminate(observation: AttemptObservation) -> bool:
    return isinstance(observation, _SUPPRESSED_TERMINATE_ROWS)


def suppresses_teardown(observation: AttemptObservation) -> bool:
    return isinstance(observation, _SUPPRESSED_TEARDOWN_ROWS)


def records_reported_returncode(observation: AttemptObservation) -> bool:
    return isinstance(observation, PostExitHangObserved)


__all__ = [
    "_teardown_issuer_for_verdict",
    "attempt_issuer_for",
    "closes_before_signal",
    "grace_wait_seconds",
    "liveness_for",
    "process_issuer_for",
    "process_issuer_for_fire_reason",
    "records_reported_returncode",
    "suppresses_teardown",
    "suppresses_terminate",
    "terminal_reason_for",
    "waits_for_exit_before_signal",
]
