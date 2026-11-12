from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.agents.timeout_clock import SystemClock
from ralph.checked_accessors import as_int, as_mapping, as_str_list
from ralph.recovery.agent_failure_ledger import AgentFailureLedger
from ralph.timeout_defaults import (
    AGENT_MAX_CONSECUTIVE_UNPRODUCTIVE_DEFAULT,
    AGENT_MAX_TOTAL_UNPRODUCTIVE_DEFAULT,
)

if TYPE_CHECKING:
    from ralph.agents.clock import Clock
    from ralph.policy.models import PolicyBundle
    from ralph.recovery.agent_unavailability_tracker import (
        UnavailabilityEntry,
        UnavailabilityStore,
    )
    from ralph.recovery.events import FailureEventBus
    from ralph.recovery.failure_classifier import FailureClassifier
    from ralph.recovery.unavailability_reason import ReasonBackoffPolicy, UnavailabilityReason
    from ralph.workspace.probe import WorkspaceChangeProbe


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
        spent_agents = {
            k: set(as_str_list(v, field=f"spent_agents[{k}]"))
            for k, v in as_mapping(snapshot.get("spent_agents", {}), field="spent_agents").items()
        }

        effective_clock = clock or SystemClock()
        ledger_snap = snapshot.get("ledger")
        ledger: AgentFailureLedger | None = None
        if isinstance(ledger_snap, Mapping):
            ledger = AgentFailureLedger.from_snapshot(ledger_snap, clock=effective_clock)

        return cls(
            cycle_cap=cycle_cap,
            backoff_attempts=backoff_attempts,
            unavailable_timeouts=unavailable_timeouts,
            spent_agents=spent_agents,
            policy_bundle=policy_bundle,
            clock=clock,
            classifier=classifier,
            event_bus=event_bus,
            ledger=ledger,
        )
