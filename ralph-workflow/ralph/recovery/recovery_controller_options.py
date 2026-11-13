from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from ralph.agents.timeout_clock import SystemClock
from ralph.checked_accessors import as_int, as_mapping, as_str_list
from ralph.recovery.agent_failure_ledger import AgentFailureLedger
from ralph.recovery.agent_unavailability_tracker import UnavailabilityEntry
from ralph.recovery.false_bench_feedback import FalseBenchFeedback
from ralph.recovery.unavailability_reason import (
    DEFAULT_UNAVAILABILITY_BACKOFF_POLICY,
    UnavailabilityReason,
)
from ralph.timeout_defaults import (
    AGENT_MAX_CONSECUTIVE_UNPRODUCTIVE_DEFAULT,
    AGENT_MAX_TOTAL_UNPRODUCTIVE_DEFAULT,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.agents.clock import Clock
    from ralph.policy.models import PolicyBundle
    from ralph.recovery.agent_unavailability_tracker import UnavailabilityStore
    from ralph.recovery.attempt_facts import AttemptFacts
    from ralph.recovery.attribution import AttributionVerdict
    from ralph.recovery.canary_probe import ProbeOutcome
    from ralph.recovery.dead_agent_verdict import PersistedVerdict, TypedSignal
    from ralph.recovery.events import FailureEventBus
    from ralph.recovery.failure_classifier import FailureClassifier
    from ralph.recovery.unavailability_reason import ReasonBackoffPolicy
    from ralph.workspace.probe import WorkspaceChangeProbe


class _DeadAgentVerdictRunner(Protocol):
    def __call__(
        self,
        *,
        facts: AttemptFacts | None,
        attribution: AttributionVerdict | None,
        probe_outcome: ProbeOutcome | None,
        typed_signal: TypedSignal | None,
    ) -> PersistedVerdict | None: ...


def _restored_unavailability_entries(
    timeouts: Mapping[str, int],
    reasons: Mapping[str, object],
    backoff_attempts: Mapping[str, int],
) -> dict[str, UnavailabilityEntry]:
    entries: dict[str, UnavailabilityEntry] = {}
    for agent, until_ms in timeouts.items():
        raw_reason = reasons.get(agent)
        if not isinstance(raw_reason, str):
            continue
        try:
            reason = UnavailabilityReason(raw_reason)
        except ValueError:
            continue
        policy = DEFAULT_UNAVAILABILITY_BACKOFF_POLICY.get(reason)
        if policy is None:
            continue
        entries[agent] = UnavailabilityEntry(
            unavailable_until_ms=until_ms,
            reason=reason,
            attempt=max(0, backoff_attempts.get(agent, 1) - 1),
            base_backoff_ms=policy.base_backoff_ms,
            max_backoff_ms=policy.max_backoff_ms,
        )
    return entries


@dataclass(frozen=True)
class RecoveryControllerOptions:
    cycle_cap: int = 200
    classifier: FailureClassifier | None = None
    event_bus: FailureEventBus | None = None
    policy_bundle: PolicyBundle | None = None
    backoff_attempts: dict[str, int] | None = None
    ledger: AgentFailureLedger | None = None
    workspace_probe: WorkspaceChangeProbe | None = None
    agent_max_consecutive_unproductive: int = AGENT_MAX_CONSECUTIVE_UNPRODUCTIVE_DEFAULT
    agent_max_total_unproductive: int = AGENT_MAX_TOTAL_UNPRODUCTIVE_DEFAULT
    unavailable_timeouts: dict[str, int] | None = None
    unavailability_backoff_policy: dict[UnavailabilityReason, ReasonBackoffPolicy] | None = None
    unavailability_entries: dict[str, UnavailabilityEntry] | None = None
    clock: Clock | None = None
    unavailability_store: UnavailabilityStore | None = None
    spent_agents: dict[str, set[str]] | None = None
    dead_agent_probe_runner: Callable[[str, str], ProbeOutcome | None] | None = None
    dead_agent_verdict_runner: _DeadAgentVerdictRunner | None = None
    false_bench_feedback: FalseBenchFeedback | None = None

    @classmethod
    def from_snapshot(
        cls,
        snapshot: Mapping[str, object],
        *,
        policy_bundle: PolicyBundle | None = None,
        clock: Clock | None = None,
        classifier: FailureClassifier | None = None,
        event_bus: FailureEventBus | None = None,
    ) -> RecoveryControllerOptions:
        cycle_cap_raw = snapshot.get("cycle_cap", 200)
        cycle_cap = int(cycle_cap_raw) if isinstance(cycle_cap_raw, (int, str)) else 200
        backoff_attempts = {
            k: as_int(v, field=f"backoff_attempts[{k}]")
            for k, v in as_mapping(
                snapshot.get("backoff_attempts", {}), field="backoff_attempts"
            ).items()
        }
        unavailable_timeouts = {
            k: as_int(v, field=f"unavailable_timeouts[{k}]")
            for k, v in as_mapping(
                snapshot.get("unavailable_timeouts", {}), field="unavailable_timeouts"
            ).items()
        }
        unavailability_entries = _restored_unavailability_entries(
            unavailable_timeouts,
            as_mapping(snapshot.get("unavailability_reasons", {}), field="unavailability_reasons"),
            backoff_attempts,
        )
        spent_agents = {
            k: set(as_str_list(v, field=f"spent_agents[{k}]"))
            for k, v in as_mapping(snapshot.get("spent_agents", {}), field="spent_agents").items()
        }

        effective_clock = clock or SystemClock()
        ledger_snap = snapshot.get("ledger")
        ledger: AgentFailureLedger | None = None
        if isinstance(ledger_snap, Mapping):
            ledger = AgentFailureLedger.from_snapshot(ledger_snap, clock=effective_clock)

        feedback_snap = snapshot.get("false_bench_feedback")
        false_bench_feedback = FalseBenchFeedback.from_snapshot(
            feedback_snap if isinstance(feedback_snap, Mapping) else None
        )

        return cls(
            cycle_cap=cycle_cap,
            backoff_attempts=backoff_attempts,
            unavailable_timeouts=unavailable_timeouts,
            unavailability_entries=unavailability_entries,
            spent_agents=spent_agents,
            policy_bundle=policy_bundle,
            clock=clock,
            classifier=classifier,
            event_bus=event_bus,
            ledger=ledger,
            false_bench_feedback=false_bench_feedback,
        )
