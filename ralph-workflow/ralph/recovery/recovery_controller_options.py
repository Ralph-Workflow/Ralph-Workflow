"""Options dataclass for RecoveryController construction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from ralph.timeout_defaults import (
    BROKEN_AGENT_SAME_SHAPE_DEFAULT,
    IN_SESSION_RETRY_ESCALATION_DEFAULT,
    SAME_SHAPE_RETRY_DEFAULT,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.agents.clock import Clock
    from ralph.policy.models import PolicyBundle
    from ralph.recovery.agent_budget_registry import AgentBudgetRegistry
    from ralph.recovery.agent_unavailability_tracker import (
        UnavailabilityEntry,
        UnavailabilityStore,
    )
    from ralph.recovery.events import FailureEventBus
    from ralph.recovery.failure_classifier import FailureClassifier
    from ralph.recovery.unavailability_reason import ReasonBackoffPolicy, UnavailabilityReason


@dataclass(frozen=True)
class RecoveryControllerOptions:
    """Options for constructing a RecoveryController."""

    cycle_cap: int = 200
    classifier: FailureClassifier | None = None
    event_bus: FailureEventBus | None = None
    budget_registry: AgentBudgetRegistry | None = None
    policy_bundle: PolicyBundle | None = None
    backoff_attempts: dict[str, int] | None = None
    technical_retry_cap: int = 10
    # R6 (Trustworthy Idle Watchdog spec): same-shape retry loop bound.
    # When consecutive ``AgentInactivityTimeoutError`` resumes fingerprint
    # identically on ``(fire_reason, diagnostic_signature, no-new-artifact,
    # workspace-change)``, the controller raises
    # ``SameShapeRetryLoopError`` instead of resuming. The default is
    # :data:`SAME_SHAPE_RETRY_DEFAULT` (3); operators extend the leash via
    # ``[general] agent_max_same_shape_resumes`` in ``ralph-workflow.toml``,
    # which is routed through this field by the runtime builder in
    # ``ralph/pipeline/run_loop.py::_build_recovery_controller``. Must be
    # >= 1; a bound of 0 would silently disable the R6 contract.
    same_shape_retry_limit: int = SAME_SHAPE_RETRY_DEFAULT
    # Consecutive identical broken-agent failures on one sole agent fail the
    # phase instead of entering the all-agents-unavailable cooldown loop.
    broken_agent_same_shape_limit: int = BROKEN_AGENT_SAME_SHAPE_DEFAULT
    # Maximum consecutive qualifying in-session retries allowed for an agent
    # before escalating to cooldown and next eligible agent selection.
    in_session_retry_escalation_limit: int = IN_SESSION_RETRY_ESCALATION_DEFAULT
    # Initial unavailable timeout state, keyed by "phase:agent" with values as
    # monotonic timestamps in milliseconds. Used to inject test state; in
    # production this starts empty and is populated by the controller.
    unavailable_timeouts: dict[str, int] | None = None
    # Per-reason backoff policy mapping. Defaults to
    # DEFAULT_UNAVAILABILITY_BACKOFF_POLICY when None.
    unavailability_backoff_policy: dict[UnavailabilityReason, ReasonBackoffPolicy] | None = None
    # Pre-seeded UnavailabilityEntry dict for testing the tracker directly.
    unavailability_entries: dict[str, UnavailabilityEntry] | None = None
    # Clock for time-dependent recovery decisions. Defaults to system clock in
    # production; inject FakeClock for deterministic tests.
    clock: Clock | None = None
    # Optional pre-built unavailability store implementing the
    # ``UnavailabilityStore`` Protocol. When provided, the controller
    # uses this store instead of constructing a default
    # ``AgentUnavailabilityTracker``. This is the future-proofing seam
    # for a persistent (sqlite, redis, file) implementation; the
    # in-memory tracker remains the default. When a custom store is
    # provided, the ``unavailable_timeouts`` and ``unavailability_entries``
    # options are ignored (the caller is responsible for the store's
    # initial state).
    unavailability_store: UnavailabilityStore | None = None
    # Snapshot restoration fields for restarting across runs
    budgets: dict[str, dict[str, int]] | None = None
    in_session_retry_counts: dict[str, int] | None = None
    spent_agents: dict[str, set[str]] | None = None

    def __post_init__(self) -> None:
        if self.in_session_retry_escalation_limit < 1:
            raise ValueError("in_session_retry_escalation_limit must be at least 1")

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
        """Construct RecoveryControllerOptions by deserializing a controller snapshot."""
        cycle_cap_raw = snapshot.get("cycle_cap", 200)
        cycle_cap = int(cycle_cap_raw) if isinstance(cycle_cap_raw, (int, str)) else 200
        technical_cap_raw = snapshot.get("technical_retry_cap", 10)
        technical_retry_cap = (
            int(technical_cap_raw) if isinstance(technical_cap_raw, (int, str)) else 10
        )
        in_session_raw = snapshot.get(
            "in_session_retry_escalation_limit", IN_SESSION_RETRY_ESCALATION_DEFAULT
        )
        in_session_limit = (
            int(in_session_raw)
            if isinstance(in_session_raw, (int, str))
            else IN_SESSION_RETRY_ESCALATION_DEFAULT
        )
        backoff_attempts = dict(cast("dict[str, int]", snapshot.get("backoff_attempts", {})))
        unavailable_timeouts = dict(
            cast("dict[str, int]", snapshot.get("unavailable_timeouts", {}))
        )
        budgets_data = cast("dict[str, dict[str, int]]", snapshot.get("budgets", {}))
        in_session_counts = dict(
            cast("dict[str, int]", snapshot.get("in_session_retry_counts", {}))
        )
        spent_raw = cast("dict[str, list[str]]", snapshot.get("spent_agents", {}))
        spent_agents = {k: set(v) for k, v in spent_raw.items()}

        return cls(
            cycle_cap=cycle_cap,
            technical_retry_cap=technical_retry_cap,
            in_session_retry_escalation_limit=in_session_limit,
            backoff_attempts=backoff_attempts,
            unavailable_timeouts=unavailable_timeouts,
            budgets=budgets_data,
            in_session_retry_counts=in_session_counts,
            spent_agents=spent_agents,
            policy_bundle=policy_bundle,
            clock=clock,
            classifier=classifier,
            event_bus=event_bus,
        )
