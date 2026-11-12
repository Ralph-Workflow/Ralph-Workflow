from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer

__all__ = [
    "Attribution",
    "AttributionVerdict",
    "RuntimeCause",
    "attribute_attempt",
    "authoritative_typed_signal_convicts_agent",
    "mid_run_activity_clears_agent",
    "peers_fail_identically_points_to_task",
    "runtime_or_unattributed_kill_of_live_session_clears_agent",
    "satisfied_outcome_clears_agent",
    "workspace_writes_clear_agent",
]

_REQUIRED_ATTRIBUTION_VALUES: Final[tuple[str, ...]] = (
    "agent",
    "runtime",
    "task_or_environment",
    "unknown",
)

_REQUIRED_RUNTIME_CAUSE_VALUES: Final[tuple[str, ...]] = (
    "runtime_kill_quota_misread",
    "runtime_kill_unattributed",
)

_RUNTIME_KILL_REASON: Final[str] = "runtime_or_unattributed_kill_of_live_session"
_SATISFIED_OUTCOME_REASON: Final[str] = "satisfied_outcome"
_PEERS_FAIL_IDENTICALLY_REASON: Final[str] = "peers_fail_identically"
_WORKSPACE_WRITES_REASON: Final[str] = "workspace_writes"
_MID_RUN_ACTIVITY_REASON: Final[str] = "mid_run_activity"
_AUTHORITATIVE_TYPED_SIGNAL_REASON: Final[str] = "authoritative_typed_signal"
_NO_RULE_FIRED_REASON: Final[str] = "no_rule_fired"

_MID_RUN_ACTIVITY_EVENT_KINDS: Final[frozenset[str]] = frozenset(
    {
        "child_heartbeat",
        "child_process",
        "child_progress",
        "child_terminal_ack",
        "progress_report",
        "stream_delta",
        "tool_result",
        "tool_use",
    }
)


class _Attribution(StrEnum):
    AGENT = "agent"
    RUNTIME = "runtime"
    TASK_OR_ENVIRONMENT = "task_or_environment"
    UNKNOWN = "unknown"


class _RuntimeCause(StrEnum):
    KILL_QUOTA_MISREAD = "runtime_kill_quota_misread"
    KILL_UNATTRIBUTED = "runtime_kill_unattributed"
    KILL_SUPERVISOR_IDLE = "runtime_kill_supervisor_idle"
    KILL_RECOVERY_TIMEOUT = "runtime_kill_recovery_timeout"
    KILL_OPERATOR = "runtime_kill_operator"


if tuple(item.value for item in _Attribution) != _REQUIRED_ATTRIBUTION_VALUES:
    raise RuntimeError(
        "Attribution must expose exactly agent, runtime, task_or_environment, unknown"
    )

if not {cause.value for cause in _RuntimeCause}.issuperset(_REQUIRED_RUNTIME_CAUSE_VALUES):
    raise RuntimeError(
        "RuntimeCause must expose runtime_kill_quota_misread and runtime_kill_unattributed"
    )


@dataclass(frozen=True, kw_only=True)
class _AttributionVerdict:
    attribution: _Attribution
    runtime_cause: _RuntimeCause | None = None
    cleared: bool = False
    counts_toward_agent_state: bool = False
    reason: str

    def __post_init__(self) -> None:
        if self.attribution is _Attribution.RUNTIME and self.runtime_cause is None:
            raise ValueError("a runtime attribution requires a labelled runtime cause")
        if self.attribution is not _Attribution.RUNTIME and self.runtime_cause is not None:
            raise ValueError("a labelled runtime cause requires a runtime attribution")
        if self.cleared and self.attribution is _Attribution.AGENT:
            raise ValueError("a cleared verdict cannot attribute the attempt end to the agent")
        if self.counts_toward_agent_state and self.attribution in (
            _Attribution.RUNTIME,
            _Attribution.TASK_OR_ENVIRONMENT,
        ):
            raise ValueError("runtime and task attributions never count toward agent state")


_RUNTIME_ISSUER_CAUSES: Final[dict[TerminationIssuer, _RuntimeCause]] = {
    TerminationIssuer.QUOTA_DETECTOR: _RuntimeCause.KILL_QUOTA_MISREAD,
    TerminationIssuer.SUPERVISOR_IDLE: _RuntimeCause.KILL_SUPERVISOR_IDLE,
    TerminationIssuer.RECOVERY_TIMEOUT: _RuntimeCause.KILL_RECOVERY_TIMEOUT,
    TerminationIssuer.OPERATOR: _RuntimeCause.KILL_OPERATOR,
    TerminationIssuer.UNKNOWN: _RuntimeCause.KILL_UNATTRIBUTED,
}


def _liveness_observed(facts: AttemptFacts) -> bool:
    if facts.liveness_observed is True:
        return True
    if facts.subagent_activity_observed is True:
        return True
    termination = facts.termination
    return termination is not None and termination.liveness_observed is True


def _has_mid_run_activity(facts: AttemptFacts) -> bool:
    if facts.tool_calls:
        return True
    if facts.subagent_activity_observed is True:
        return True
    return any(kind in _MID_RUN_ACTIVITY_EVENT_KINDS for kind in facts.event_kinds)


def runtime_or_unattributed_kill_of_live_session_clears_agent(
    facts: AttemptFacts,
) -> _AttributionVerdict | None:
    termination = facts.termination
    if termination is None:
        return None
    runtime_cause = _RUNTIME_ISSUER_CAUSES.get(termination.issuer)
    if runtime_cause is None:
        return None
    if not _liveness_observed(facts):
        return None
    return _AttributionVerdict(
        attribution=_Attribution.RUNTIME,
        runtime_cause=runtime_cause,
        cleared=True,
        counts_toward_agent_state=False,
        reason=_RUNTIME_KILL_REASON,
    )


def satisfied_outcome_clears_agent(facts: AttemptFacts) -> _AttributionVerdict | None:
    if not facts.artifact_valid:
        return None
    return _AttributionVerdict(
        attribution=_Attribution.TASK_OR_ENVIRONMENT,
        cleared=True,
        counts_toward_agent_state=False,
        reason=_SATISFIED_OUTCOME_REASON,
    )


def peers_fail_identically_points_to_task(
    facts: AttemptFacts,
    *,
    peer_failures_identical: bool = False,
) -> _AttributionVerdict | None:
    if facts.artifact_valid:
        return None
    if not peer_failures_identical:
        return None
    return _AttributionVerdict(
        attribution=_Attribution.TASK_OR_ENVIRONMENT,
        cleared=True,
        counts_toward_agent_state=False,
        reason=_PEERS_FAIL_IDENTICALLY_REASON,
    )


def workspace_writes_clear_agent(facts: AttemptFacts) -> _AttributionVerdict | None:
    if facts.workspace_changed is not True and facts.workspace_changed_by_tool is not True:
        return None
    return _AttributionVerdict(
        attribution=_Attribution.TASK_OR_ENVIRONMENT,
        cleared=True,
        counts_toward_agent_state=False,
        reason=_WORKSPACE_WRITES_REASON,
    )


def mid_run_activity_clears_agent(facts: AttemptFacts) -> _AttributionVerdict | None:
    if not _has_mid_run_activity(facts):
        return None
    return _AttributionVerdict(
        attribution=_Attribution.TASK_OR_ENVIRONMENT,
        cleared=True,
        counts_toward_agent_state=False,
        reason=_MID_RUN_ACTIVITY_REASON,
    )


def authoritative_typed_signal_convicts_agent(
    facts: AttemptFacts,
    *,
    typed_signal_observed: bool = False,
    typed_signal_provider_originated: bool = False,
) -> _AttributionVerdict | None:
    if facts.artifact_valid:
        return None
    if not typed_signal_observed:
        return None
    if not typed_signal_provider_originated:
        return None
    return _AttributionVerdict(
        attribution=_Attribution.AGENT,
        cleared=False,
        counts_toward_agent_state=True,
        reason=_AUTHORITATIVE_TYPED_SIGNAL_REASON,
    )


def attribute_attempt(
    facts: AttemptFacts,
    *,
    peer_failures_identical: bool = False,
    typed_signal_observed: bool = False,
    typed_signal_provider_originated: bool = False,
) -> _AttributionVerdict:
    verdict = runtime_or_unattributed_kill_of_live_session_clears_agent(facts)
    if verdict is None:
        verdict = satisfied_outcome_clears_agent(facts)
    if verdict is None:
        verdict = peers_fail_identically_points_to_task(
            facts,
            peer_failures_identical=peer_failures_identical,
        )
    if verdict is None:
        verdict = workspace_writes_clear_agent(facts)
    if verdict is None:
        verdict = mid_run_activity_clears_agent(facts)
    if verdict is None:
        verdict = authoritative_typed_signal_convicts_agent(
            facts,
            typed_signal_observed=typed_signal_observed,
            typed_signal_provider_originated=typed_signal_provider_originated,
        )
    if verdict is None:
        return _AttributionVerdict(
            attribution=_Attribution.UNKNOWN,
            cleared=False,
            counts_toward_agent_state=False,
            reason=_NO_RULE_FIRED_REASON,
        )
    return verdict


Attribution = _Attribution
RuntimeCause = _RuntimeCause
AttributionVerdict = _AttributionVerdict
