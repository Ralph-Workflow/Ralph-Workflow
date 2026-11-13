from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ralph.recovery.termination_log_entry import TerminationLogEntry

__all__ = [
    "AttemptFacts",
    "TerminationIssuer",
    "TerminationRecord",
    "WorkspaceChangeState",
    "has_return_code_discrepancy",
    "workspace_changed_state",
]

_REQUIRED_TERMINATION_ISSUER_VALUES: tuple[str, ...] = (
    "agent_exit",
    "supervisor_idle",
    "quota_detector",
    "recovery_timeout",
    "post_exit_watchdog",
    "operator",
    "unknown",
)

_REQUIRED_WORKSPACE_CHANGE_STATE_VALUES: tuple[str, ...] = ("true", "false", "unknown")


class _TerminationIssuer(StrEnum):
    AGENT_EXIT = "agent_exit"
    SUPERVISOR_IDLE = "supervisor_idle"
    QUOTA_DETECTOR = "quota_detector"
    RECOVERY_TIMEOUT = "recovery_timeout"
    POST_EXIT_WATCHDOG = "post_exit_watchdog"
    OPERATOR = "operator"
    UNKNOWN = "unknown"


class _WorkspaceChangeState(StrEnum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


if tuple(issuer.value for issuer in _TerminationIssuer) != _REQUIRED_TERMINATION_ISSUER_VALUES:
    raise RuntimeError(
        "TerminationIssuer must expose exactly agent_exit, supervisor_idle, quota_detector, "
        "recovery_timeout, post_exit_watchdog, operator, unknown"
    )

if tuple(state.value for state in _WorkspaceChangeState) != _REQUIRED_WORKSPACE_CHANGE_STATE_VALUES:
    raise RuntimeError("WorkspaceChangeState must expose exactly true, false, unknown")


def _return_codes_disagree(
    observed_returncode: int | None, reported_returncode: int | None
) -> bool:
    if observed_returncode is None and reported_returncode is None:
        return False
    if observed_returncode is None or reported_returncode is None:
        return True
    return observed_returncode != reported_returncode


@dataclass(frozen=True)
class _TerminationRecord:
    issuer: _TerminationIssuer
    observed_returncode: int | None = None
    reported_returncode: int | None = None
    liveness_observed: bool | None = None
    log: tuple[TerminationLogEntry, ...] = field(default_factory=tuple)
    return_code_discrepancy: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "return_code_discrepancy",
            _return_codes_disagree(self.observed_returncode, self.reported_returncode),
        )

    @classmethod
    def create(
        cls,
        issuer: _TerminationIssuer,
        *,
        observed_returncode: int | None = None,
        reported_returncode: int | None = None,
        liveness_observed: bool | None = None,
        source: str | None = None,
        log: tuple[TerminationLogEntry, ...] | None = None,
    ) -> _TerminationRecord:
        resolved_log = (
            log
            if log is not None
            else (TerminationLogEntry(issuer=issuer, source=source or issuer.value),)
        )
        return cls(
            issuer=issuer,
            observed_returncode=observed_returncode,
            reported_returncode=reported_returncode,
            liveness_observed=liveness_observed,
            log=resolved_log,
        )


@dataclass(frozen=True)
class _AttemptFacts:
    event_kinds: tuple[str, ...] = field(default_factory=tuple)
    tool_calls: tuple[str, ...] = field(default_factory=tuple)
    workspace_changed_by_tool: bool | None = None
    artifact_valid: bool = False
    workspace_changed: bool | None = None
    elapsed_to_terminal_ms: int | None = None
    exit_status: str | None = None
    liveness_observed: bool | None = None
    subagent_activity_observed: bool | None = None
    termination: _TerminationRecord | None = None


def has_return_code_discrepancy(termination: _TerminationRecord | None) -> bool:
    if termination is None:
        return False
    return termination.return_code_discrepancy


def workspace_changed_state(facts: _AttemptFacts) -> _WorkspaceChangeState:
    if facts.workspace_changed is None:
        return _WorkspaceChangeState.UNKNOWN
    if facts.workspace_changed:
        return _WorkspaceChangeState.TRUE
    return _WorkspaceChangeState.FALSE


TerminationIssuer = _TerminationIssuer
WorkspaceChangeState = _WorkspaceChangeState
TerminationRecord = _TerminationRecord
AttemptFacts = _AttemptFacts
