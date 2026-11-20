from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from ralph.agents.timeout_clock import SystemClock
from ralph.checked_accessors import as_int, as_mapping
from ralph.recovery.agent_failure_ledger import AgentFailureLedger
from ralph.recovery.false_cooldown_feedback import FalseCooldownFeedback
from ralph.recovery.legacy_guard_migration import (
    FALSE_COOLDOWN_FEEDBACK_KEY,
    migrate_false_cooldown_feedback_key,
)
from ralph.recovery.restored_cooldowns import (
    rebased_unavailable_timeouts,
    restored_unavailability_entries,
)
from ralph.timeout_defaults import (
    AGENT_MAX_CONSECUTIVE_UNPRODUCTIVE_DEFAULT,
    AGENT_MAX_TOTAL_UNPRODUCTIVE_DEFAULT,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.agents.clock import Clock
    from ralph.policy.models import PolicyBundle
    from ralph.recovery.agent_unavailability_tracker import UnavailabilityEntry, UnavailabilityStore
    from ralph.recovery.attempt_facts import AttemptFacts
    from ralph.recovery.attribution import AttributionVerdict
    from ralph.recovery.canary_probe import ProbeOutcome
    from ralph.recovery.dead_agent_verdict import PersistedVerdict, TypedSignal
    from ralph.recovery.events import FailureEventBus
    from ralph.recovery.failure_classifier import FailureClassifier
    from ralph.recovery.unavailability_reason import ReasonBackoffPolicy, UnavailabilityReason
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
    dead_agent_probe_runner: Callable[[str, str], ProbeOutcome | None] | None = None
    dead_agent_verdict_runner: _DeadAgentVerdictRunner | None = None
    false_cooldown_feedback: FalseCooldownFeedback | None = None

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
        effective_clock = clock or SystemClock()
        reasons = as_mapping(
            snapshot.get("unavailability_reasons", {}), field="unavailability_reasons"
        )
        unavailable_timeouts = rebased_unavailable_timeouts(
            {
                k: as_int(v, field=f"unavailable_timeouts[{k}]")
                for k, v in as_mapping(
                    snapshot.get("unavailable_timeouts", {}), field="unavailable_timeouts"
                ).items()
            },
            {
                k: as_int(v, field=f"unavailable_until_epoch_ms[{k}]")
                for k, v in as_mapping(
                    snapshot.get("unavailable_until_epoch_ms", {}),
                    field="unavailable_until_epoch_ms",
                ).items()
            },
            reasons,
            clock=effective_clock,
        )
        unavailability_entries = restored_unavailability_entries(
            unavailable_timeouts,
            reasons,
            backoff_attempts,
        )

        ledger_snap = snapshot.get("ledger")
        ledger: AgentFailureLedger | None = None
        if isinstance(ledger_snap, Mapping):
            ledger = AgentFailureLedger.from_snapshot(ledger_snap, clock=effective_clock)

        feedback_fields = dict(snapshot)
        migrate_false_cooldown_feedback_key(feedback_fields)
        feedback_snap = feedback_fields.get(FALSE_COOLDOWN_FEEDBACK_KEY)
        false_cooldown_feedback = FalseCooldownFeedback.from_snapshot(
            feedback_snap if isinstance(feedback_snap, Mapping) else None
        )

        return cls(
            cycle_cap=cycle_cap,
            backoff_attempts=backoff_attempts,
            unavailable_timeouts=unavailable_timeouts,
            unavailability_entries=unavailability_entries,
            policy_bundle=policy_bundle,
            clock=clock,
            classifier=classifier,
            event_bus=event_bus,
            ledger=ledger,
            false_cooldown_feedback=false_cooldown_feedback,
        )
