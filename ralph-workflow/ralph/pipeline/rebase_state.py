"""Rebase state model for pipeline state."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from loguru import logger
from pydantic import ConfigDict, Field, model_validator

from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline._publication_state_machine import PublicationDecision
from ralph.pydantic_compat import RalphBaseModel

if TYPE_CHECKING:
    from typing import Self

    from ralph.display.auto_integrate_message import OperationDisplayState
    from ralph.pipeline._fan_out_authority import FanOutDecision

_FROZEN = ConfigDict(frozen=True)
HISTORY_AWARE_CONFLICT_STRATEGY_INDEX = 3
_LEGACY_AGENT_FAILURE_REASON = "resolution_agent_failure"


class _ResolutionFailureCause(StrEnum):
    """Structured root cause for an integration or conflict resolution failure."""

    NONE = "NONE"
    AGENT_FAILURE = "AGENT_FAILURE"
    UNRESOLVED_CONFLICT = "UNRESOLVED_CONFLICT"
    CONTEXT_FAILURE = "CONTEXT_FAILURE"


_ResolutionFailureCause.__name__ = "ResolutionFailureCause"
_ResolutionFailureCause.__qualname__ = "ResolutionFailureCause"
ResolutionFailureCause = _ResolutionFailureCause


class _StartupRetentionReason(StrEnum):
    """Typed vocabulary for why a startup recovery record stayed on disk."""

    FAILED_ABORT = "FAILED_ABORT"
    FAILED_RESET = "FAILED_RESET"
    TARGET_POINTER_UNREFRESHABLE = "TARGET_POINTER_UNREFRESHABLE"
    TRANSIENT_FAST_FORWARD_FAILURE = "TRANSIENT_FAST_FORWARD_FAILURE"
    OWNED_TARGET_PRECONDITION_FAILED = "OWNED_TARGET_PRECONDITION_FAILED"
    OWNED_OPERATION_IDENTITY_MISMATCH = "OWNED_OPERATION_IDENTITY_MISMATCH"
    REMOTE_RECONCILE_RETAINED = "REMOTE_RECONCILE_RETAINED"
    OBSERVED_RETAINED_ON_DISK = "OBSERVED_RETAINED_ON_DISK"


_StartupRetentionReason.__name__ = "StartupRetentionReason"
_StartupRetentionReason.__qualname__ = "StartupRetentionReason"
StartupRetentionReason = _StartupRetentionReason


class RebaseState(RalphBaseModel):
    """State for git rebase operations.

    The legacy ``pending``/``in_progress``/``completed`` fields are
    preserved so existing checkpoints deserialize cleanly. The
    auto-integration fields (``last_outcome``/``last_reason``/
    ``last_target``/``fast_forwarded``) are the surface the runner
    reads to thread the integration outcome into the persisted
    checkpoint, and the surface operators see in run state when a
    skip or conflict is recorded. All four new fields have defaults
    so legacy checkpoints load without modification.
    """

    model_config = _FROZEN

    pending: bool = False
    in_progress: bool = False
    completed: bool = False

    # Auto-integration outcome. ``last_outcome`` is the typed outcome
    # kind (``RebaseOutcomeKind``); ``last_action`` is the backward-compatible
    # string property projection; ``last_reason`` is the human-readable
    # skip / failure reason when one applies; ``last_target`` is the
    # mainline branch the integration step targeted; ``fast_forwarded``
    # records whether the fast-forward phase actually advanced the
    # target ref (False on conflict, on dirty worktree, on
    # concurrent-target-update race, etc.). See
    # ``ralph.pipeline.auto_integrate`` for the producer of these
    # values.
    last_outcome: RebaseOutcomeKind | None = None
    last_reason: str | None = None
    last_failure_cause: ResolutionFailureCause = ResolutionFailureCause.NONE
    last_target: str | None = None
    fast_forwarded: bool = False

    @property
    def last_action(self) -> str | None:
        """Backward-compatible projection of typed last_outcome."""
        return self.last_outcome.value if self.last_outcome is not None else None

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy_action(cls, data: object) -> object:
        if isinstance(data, dict):
            migrated: dict[str, object] = {str(k): v for k, v in data.items()}
            if "last_failure_cause" not in migrated or migrated.get("last_failure_cause") is None:
                if migrated.get("last_reason") == _LEGACY_AGENT_FAILURE_REASON:
                    migrated["last_failure_cause"] = ResolutionFailureCause.AGENT_FAILURE
                else:
                    migrated["last_failure_cause"] = ResolutionFailureCause.NONE
            has_action = "last_action" in migrated
            raw_action = migrated.get("last_action")
            raw_outcome = migrated.get("last_outcome")

            def _to_kind(val: object) -> RebaseOutcomeKind | None:
                if val is None:
                    return None
                if isinstance(val, RebaseOutcomeKind):
                    return val
                return RebaseOutcomeKind.from_legacy(str(val))

            if has_action and raw_outcome is None:
                if raw_action is None:
                    migrated["last_outcome"] = None
                else:
                    migrated["last_outcome"] = _to_kind(raw_action)
                mapped_outcome: object = migrated["last_outcome"]
                mapped_value: object = (
                    mapped_outcome.value if isinstance(mapped_outcome, RebaseOutcomeKind) else None
                )
                logger.warning(
                    "MIGRATED_LEGACY: last_action {} -> last_outcome {}",
                    raw_action,
                    mapped_value,
                )
                return migrated

            if has_action and raw_action is not None and raw_outcome is not None:
                action_kind = _to_kind(raw_action)
                outcome_kind = _to_kind(raw_outcome)
                if (
                    None in (action_kind, outcome_kind)
                    or RebaseOutcomeKind.UNKNOWN in (action_kind, outcome_kind)
                    or action_kind != outcome_kind
                ):
                    migrated["last_outcome"] = RebaseOutcomeKind.UNKNOWN
                    logger.warning(
                        "MIGRATED_LEGACY: contradictory last_action={} last_outcome={}; set to UNKNOWN",
                        raw_action,
                        raw_outcome,
                    )
                else:
                    migrated["last_outcome"] = outcome_kind
                    outcome_value: object = (
                        outcome_kind.value if isinstance(outcome_kind, RebaseOutcomeKind) else None
                    )
                    logger.warning(
                        "MIGRATED_LEGACY: last_action {} -> last_outcome {}",
                        raw_action,
                        outcome_value,
                    )
                return migrated

            if raw_outcome is not None and isinstance(raw_outcome, str):
                outcome_kind = _to_kind(raw_outcome)
                if outcome_kind == RebaseOutcomeKind.UNKNOWN:
                    migrated["last_outcome"] = RebaseOutcomeKind.UNKNOWN
                    logger.warning(
                        "MIGRATED_LEGACY: unknown last_outcome {} set to UNKNOWN",
                        raw_outcome,
                    )
                    return migrated

            return migrated
        return data

    @classmethod
    def from_legacy_payload(cls, payload: dict[str, object]) -> Self:
        """One-way migration entry point for legacy checkpoints."""
        validated = cls.model_validate(payload)
        legacy_action = payload.get("last_action")
        mapped_outcome = validated.last_outcome
        mapped_val = mapped_outcome.value if mapped_outcome is not None else None
        logger.warning(
            "MIGRATED_LEGACY: last_action {} -> last_outcome {}",
            legacy_action,
            mapped_val,
        )
        return validated

    # ``last_refresh`` records the outcome of the origin refresh that
    # ran immediately before the fast-forward observed the target SHA
    # (one of the ``REFRESH_*`` values in
    # ``ralph.pipeline.auto_integrate_sync``). The refresh is fail-open
    # -- an unreachable remote still lands locally -- so this field is
    # the only signal that tells an operator whether the mainline
    # pointer just landed against was actually fresh. Defaulted, so
    # legacy checkpoints load unchanged.
    last_refresh: str | None = None

    # Freshness is structured so seam control and display never infer safety
    # from human-readable reasons. ``verified`` means a healthy fetch or
    # shared-ref observation established the base; ``degraded`` is fail-open
    # remote unavailability; ``unverified`` is an intentionally suppressed
    # probe; ``unsafe`` blocks the feature rebase for this seam.
    freshness_verdict: str | None = None
    freshness_source: str | None = None
    freshness_safe: bool = True
    freshness_target_sha: str | None = None

    # Reclamation is structured separately from warning prose so checkpoints,
    # summaries, and recovery tooling can report destructive target-owner work
    # without parsing a log line. ``None`` / 0 preserve legacy checkpoints.
    reclaimed_worktree_path: str | None = None
    reclaim_snapshot_ref: str | None = None
    reclaim_discarded_path_count: int = 0

    # ``last_push`` records the fail-open configured-remote push after a
    # successful local landing. The summary is operator-visible but never
    # changes the landing result. The field is None when sync is disabled
    # or when the prior integration did not produce a record, so legacy
    # checkpoints load unchanged.
    last_push: str | None = None

    # ``last_remote_sync`` carries the latest opt-in remote-sync
    # outcome produced by
    # :mod:`ralph.pipeline.auto_integrate_remote_sync`. Distinct from
    # ``last_push`` (which is the free-form summary string) and from
    # ``last_refresh`` (which is the observe-only outcome); the value
    # is one of the ``REMOTE_*`` constants in that module and names
    # the higher-level verb (`pulled`, `reconciled`, `pushed`,
    # `push rejected`, `pending push`, ...). Defaults to ``None`` so
    # legacy checkpoints load unchanged.
    last_remote_sync: str | None = None

    # Configured remote used for the latest remote-sync outcome.
    last_remote: str | None = None

    # Structured classification of the latest publication attempt. This is
    # deliberately separate from ``last_push`` prose: retry decisions must
    # never parse operator display text. Defaults preserve old checkpoints.
    last_push_status: str | None = None

    # ``consecutive_conflicts`` counts unresolved integration conflicts
    # against ``last_target`` in a row. It bounds how often the
    # dev-agent conflict resolver is invoked for the same conflict (see
    # ``ralph.pipeline.auto_integrate_conflict_budget``) and resets to 0
    # on any successful land. Defaulted, so legacy checkpoints load
    # unchanged.
    consecutive_conflicts: int = 0
    integration_retry_attempt: int = Field(
        default=0,
        ge=0,
        description="Checkpointed integration backoff attempt; stops increasing at the delay cap",
    )

    # Typed persisted deadline (monotonic seconds since an unspecified
    # origin) the run loop uses to bound a ``WAIT_FOR_BOUNDED_RETRY``
    # decision. ``None`` is the default for legacy checkpoints and for
    # records that have not yet been armed with a deadline; the action
    # authority treats a missing or malformed ``retry_deadline`` as
    # proof that the wait cannot be bounded, which routes the
    # decision to ``REQUIRE_INTERVENTION`` rather than a hopeful
    # wait. The value is read through the typed
    # :class:`IntegrationAction` authority; the run loop never parses
    # this field directly.
    retry_deadline: float | None = None

    # Durable identity of the conflict ``consecutive_conflicts`` counts.
    # ``last_target`` alone does not identify a conflict: a developer
    # can add a feature commit that changes what conflicts while the
    # mainline branch NAME stays ``main``, and other agents move the
    # mainline tip continuously. Without these two observations the
    # budget would keep suppressing the resolver for a conflict it has
    # never actually seen. The pair is the feature tip and the target
    # tip observed immediately before the integration attempt that
    # recorded the conflict; when either differs at the next seam the
    # budget starts fresh (see
    # ``ralph.pipeline.auto_integrate_conflict_budget.ConflictIdentity``).
    # Both are cleared on a successful land and both are defaulted, so
    # legacy checkpoints load unchanged and conservatively keep their
    # carried count.
    last_conflict_feature_sha: str | None = None
    last_conflict_target_sha: str | None = None
    last_conflict_paths: tuple[str, ...] = ()
    last_conflict_stage_oids: tuple[str, ...] = ()
    last_conflict_scope: str = ""

    # The ladder rung that spent ``consecutive_conflicts``.  A new rung gets
    # its own configured resolver budget even when it addresses the same
    # durable conflict identity; defaulting preserves legacy checkpoints.
    last_conflict_strategy_index: int = 0

    resolving_rebase: bool = False
    resolving_merge: bool = False
    diagnostic_ownership: str | None = None

    # ``recovery_record_retained`` marks a startup crash-recovery outcome
    # that deliberately LEFT the durable ``IntegrationRecord`` on disk for
    # the next startup to retry (a failed abort, a failed reset, a target
    # pointer that could not be refreshed, a transient fast-forward
    # failure). Recovery still owns that record, so the caller must not
    # begin a fresh integration in the same startup: ``_integrate_once``
    # writes a new ``IntegrationRecord(phase='integrating', ...)`` before
    # it mutates anything, which would overwrite the only durable
    # metadata describing the interrupted operation.
    #
    # This is a STRUCTURED flag on purpose. The retention fact used to be
    # legible only from the free-form ``last_reason`` display text, which
    # no caller may parse. Produced by
    # ``ralph.pipeline.auto_integrate_recovery`` and read through its
    # ``recovery_retained_record`` predicate; defaulted, so legacy
    # checkpoints load unchanged and conservatively read as "not
    # retained".
    recovery_record_retained: bool = False

    # Typed companion to ``recovery_record_retained``: the single-owner
    # vocabulary describing WHY the durable record was left on disk.
    # ``None`` for legacy checkpoints and cleared states. Write it only
    # through :meth:`set_recovery_record_retained` / the constructor's
    # ``recovery_record_retained_reason`` kwarg; never through
    # ``model_copy(update={...})`` at call sites.
    recovery_record_retained_reason: StartupRetentionReason | None = None

    # A resolver chain that has tried every allowed candidate is terminal
    # integration evidence.  These defaulted fields preserve compatibility
    # with checkpoints written before conflict-resolution exhaustion was
    # durable.
    resolution_exhausted: bool = False
    resolution_exhaustion_reason: str | None = None

    # The conflict-resolution ladder is durable so a restart continues with
    # the next genuinely different integration strategy.
    conflict_strategy_index: int = 0
    conflict_strategies_tried: tuple[str, ...] = ()

    # An earlier seam recorded an unresolved integration and a LATER seam
    # neither landed nor re-recorded one -- a skip, which changes nothing
    # about the repository and so cannot resolve anything either. Carrying
    # the block on its own field keeps ``last_action`` honest about what
    # the latest seam actually did, which the operator log and the
    # integration tests both read. Defaulted, so older checkpoints load
    # unchanged. Cleared by a landing; see
    # ``ralph.pipeline.auto_integrate_resolution_state``.
    unresolved_integration_carried: bool = False

    # --- Lifecycle authority fields (ADR-0007) ---------------------------
    # All four fields are defaulted so legacy checkpoints load unchanged.
    # They are the single persisted surface for the seven typed authorities
    # defined in the integration-lifecycle architecture decision record.
    # No caller may infer progression from the prose fields above; the
    # run loop reads the typed values here.

    # The run loop's typed decision. One of
    # ``CONTINUE_PHASE`` / ``ADVANCE_PHASE`` / ``BLOCK_FOR_RECOVERY`` /
    # ``REPORT_TERMINAL_SUCCESS`` / ``REPORT_TERMINAL_FAILURE`` /
    # ``REPORT_INTERVENTION_REQUIRED``. ``None`` until the run loop's
    # first :func:`ralph.pipeline._pipeline_state_machine.decide_phase_transition`
    # call lands.
    last_decision: str | None = None

    # The remote-publication authority's typed decision. One of
    # ``PUBLICATION_PENDING`` / ``PUBLISH_NOW`` /
    # ``PUBLISH_DEFERRED_FRESHNESS`` / ``PUBLISH_DEFERRED_REMOTE`` /
    # ``PUBLICATION_TERMINAL`` / ``PUBLICATION_BLOCKED``.
    last_publication_decision: str | None = None

    # The parallel-joins authority's typed decision. One of
    # ``JOIN_PENDING`` / ``JOIN_COMPLETE`` / ``JOIN_PARTIAL_FAILURE`` /
    # ``JOIN_BLOCKED``.
    last_fan_out_decision: str | None = None

    # The display formatter's typed eight-category state. One of
    # ``INTEGRATING`` / ``LANDING`` / ``PUBLISHING`` / ``CONFLICT_RESOLUTION``
    # / ``EVIDENCE_DIAGNOSIS`` / ``WAITING`` / ``INTERVENTION_REQUIRED`` /
    # ``TERMINAL``. ``None`` for legacy checkpoints; the display formatter
    # falls back to the prose fields when this is unset.
    operation_display_state: str | None = None

    @model_validator(mode="after")
    def _populate_operation_display_state(self) -> Self:
        if self.last_decision is None:
            if self.is_intervention_required:
                object.__setattr__(self, "last_decision", "report_intervention_required")
            elif self.integration_unresolved or self.recovery_record_retained:
                object.__setattr__(self, "last_decision", "block_for_recovery")
        if self.operation_display_state is None:
            state = _resolve_operation_display_state(self)
            if state is not None:
                object.__setattr__(self, "operation_display_state", state)
        return self

    # Legacy rebase checkpoint identity. These fields let both startup paths
    # retain actionable evidence without re-parsing or replacing it.
    legacy_checkpoint_blocked: bool = False
    legacy_phase: str | None = None
    legacy_conflicted_files: tuple[str, ...] = ()
    legacy_resolved_files: tuple[str, ...] = ()
    legacy_error_count: int = 0
    legacy_timestamp: str | None = None
    legacy_source: str | None = None

    @property
    def integration_unresolved(self) -> bool:
        """Whether a durable integration outcome still blocks phase advancement.

        Conflict resolution and retained crash-recovery records are both
        unresolved integration ownership. Callers must fail closed rather than
        materializing prompts, dispatching agents, or reporting success.
        """
        return (
            self.last_outcome
            in (
                RebaseOutcomeKind.CONFLICT,
                RebaseOutcomeKind.UNKNOWN,
                RebaseOutcomeKind.INTERVENTION_REQUIRED,
                RebaseOutcomeKind.DIAGNOSTIC_INTERVENTION_REQUIRED,
            )
            or self.recovery_record_retained
            or self.legacy_checkpoint_blocked
            or self.unresolved_integration_carried
            or self.resolving_rebase
            or self.resolving_merge
            or bool(self.diagnostic_ownership)
        )

    def set_recovery_record_retained(self, reason: StartupRetentionReason) -> Self:
        """Retain the durable recovery record with a typed reason."""
        return self.model_copy(
            update={
                "recovery_record_retained": True,
                "recovery_record_retained_reason": reason,
            }
        )

    def set_publication_decision(self, value: PublicationDecision) -> Self:
        """Record the remote-publication authority's typed decision.

        Single writer of :attr:`last_publication_decision`;
        ``value`` must belong to the closed
        :class:`PublicationDecision` vocabulary.
        """
        if not isinstance(value, PublicationDecision):
            raise ValueError(f"invalid publication decision: {value!r}")
        return self.model_copy(update={"last_publication_decision": value.value})

    def set_fan_out_decision(self, value: FanOutDecision) -> Self:
        """Record the fan-out authority's typed decision.

        Single writer of :attr:`last_fan_out_decision`;
        ``value`` must belong to the closed
        :class:`FanOutDecision` vocabulary.
        """
        # Lazy import: _fan_out_authority imports PipelineState, which
        # imports this module.
        from ralph.pipeline._fan_out_authority import FanOutDecision

        if not isinstance(value, FanOutDecision):
            raise ValueError(f"invalid fan_out_decision: {value!r}")
        return self.model_copy(update={"last_fan_out_decision": value.value})

    def set_operation_display_state(self, value: OperationDisplayState) -> Self:
        """Record the operator display state.

        Single writer of :attr:`operation_display_state`;
        ``value`` must belong to the closed
        :class:`OperationDisplayState` vocabulary (legacy alias
        strings are rejected).
        """
        from ralph.display.auto_integrate_message import OperationDisplayState

        if not isinstance(value, OperationDisplayState):
            raise ValueError(f"invalid operation_display_state: {value!r}")
        return self.model_copy(update={"operation_display_state": value.value})

    def clear_recovery_record_retained(self) -> Self:
        """Clear the retained-record fact and its typed reason together."""
        return self.model_copy(
            update={
                "recovery_record_retained": False,
                "recovery_record_retained_reason": None,
            }
        )

    def set_retry_deadline(self, value: float | None) -> Self:
        """Record the typed persistent retry deadline.

        Single writer of :attr:`retry_deadline`; ``value`` must be
        ``float`` (monotonic seconds) or ``None`` to clear the
        deadline. The reducer and the budget module both call this
        setter instead of writing the field through ``model_copy``.
        """
        if value is not None and not isinstance(value, (int, float)):
            raise ValueError(f"invalid retry_deadline: {value!r}")
        return self.model_copy(update={"retry_deadline": value})

    def record_consecutive_conflicts(self, value: int) -> Self:
        """Record the bounded consecutive-conflict counter.

        Single writer of :attr:`consecutive_conflicts`; ``value`` is
        clamped to ``[0, MAX_CONSECUTIVE_RESOLVER_ATTEMPTS]`` so a
        caller that hands in a stale or oversized count cannot push
        the ladder past the configured cap. ``None`` is treated as 0.
        """
        # Lazy import: auto_integrate_conflict_budget imports
        # ``RebaseState`` only under ``TYPE_CHECKING``, but importing
        # the constant eagerly here would force a real import cycle
        # through ``rebase_state`` -> ``auto_integrate_conflict_budget``
        # -> ``rebase_state`` at module load. Doing it inside the
        # function body keeps the import graph clean.
        from ralph.pipeline.auto_integrate_conflict_budget import (
            MAX_CONSECUTIVE_RESOLVER_ATTEMPTS,
        )

        coerced = 0 if value is None else int(value)
        clamped = max(0, min(coerced, MAX_CONSECUTIVE_RESOLVER_ATTEMPTS))
        return self.model_copy(update={"consecutive_conflicts": clamped})

    def advance_conflict_strategy(self, reason: str) -> Self:
        """Advance the durable conflict-resolution ladder by one rung.

        Single writer of :attr:`conflict_strategy_index`,
        :attr:`conflict_strategies_tried`,
        :attr:`resolution_exhausted`, and
        :attr:`resolution_exhaustion_reason`. Appends the current
        strategy with the supplied ``reason`` to the tried list,
        increments the index modulo
        :data:`CONFLICT_RESOLUTION_STRATEGIES`, and sets
        ``resolution_exhausted=True`` once the cap is reached. The
        exhaustion reason is the joined history of every tried
        strategy.
        """
        from ralph.pipeline.auto_integrate_resolution_state import (
            CONFLICT_RESOLUTION_STRATEGIES,
        )

        n = len(CONFLICT_RESOLUTION_STRATEGIES)
        index = self.conflict_strategy_index
        tried = self.conflict_strategies_tried
        if index < n:
            tried = (*tried, f"{CONFLICT_RESOLUTION_STRATEGIES[index]}: {reason}")
            index += 1
        exhausted = index >= n
        return self.model_copy(
            update={
                "conflict_strategy_index": index,
                "conflict_strategies_tried": tried,
                "resolution_exhausted": exhausted,
                "resolution_exhaustion_reason": "; ".join(tried) if exhausted else None,
            }
        )

    def project_conflict_strategy_from(self, source: RebaseState) -> Self:
        """Project the conflict-strategy block one-way from ``source``.

        Exact one-way projection of exactly four fields: index,
        tried, exhausted, exhausted-reason. Used to copy a typed
        prior's strategy block onto a new record without re-deriving
        it from prose.
        """
        return self.model_copy(
            update={
                "conflict_strategy_index": source.conflict_strategy_index,
                "conflict_strategies_tried": source.conflict_strategies_tried,
                "resolution_exhausted": source.resolution_exhausted,
                "resolution_exhaustion_reason": source.resolution_exhaustion_reason,
            }
        )

    def set_intervention_required(self, reason: str, *, diagnostic: bool = False) -> Self:
        """Record a persistent INTERVENTION_REQUIRED transition.

        Single writer of the persistent
        :attr:`last_outcome` /
        :attr:`last_reason` transition that signals operator
        intervention. ``diagnostic=True`` records the
        ``DIAGNOSTIC_INTERVENTION_REQUIRED`` variant for evidence
        diagnosis flows; the default is plain
        ``INTERVENTION_REQUIRED``.
        """
        outcome = (
            RebaseOutcomeKind.DIAGNOSTIC_INTERVENTION_REQUIRED
            if diagnostic
            else RebaseOutcomeKind.INTERVENTION_REQUIRED
        )
        return self.model_copy(update={"last_outcome": outcome, "last_reason": reason})

    def is_terminal_action(self) -> bool:
        """Whether the last outcome kind is terminal."""
        return self.last_outcome is not None and self.last_outcome.is_terminal()

    def is_diagnostic_action(self) -> bool:
        """Whether the last outcome kind represents an evidence diagnosis action."""
        return self.last_outcome in (
            RebaseOutcomeKind.DIAGNOSING,
            RebaseOutcomeKind.EVIDENCE_DIAGNOSIS,
            RebaseOutcomeKind.DIAGNOSTIC_INTERVENTION_REQUIRED,
        )

    def is_rebasing_or_merging(self) -> bool:
        """Whether the outcome is a rebase or merge."""
        return self.last_outcome in (RebaseOutcomeKind.REBASED, RebaseOutcomeKind.MERGED)

    def is_conflict_resolution(self) -> bool:
        """Whether the outcome represents active conflict resolution."""
        return _is_conflict_resolution(self)

    def is_landing(self) -> bool:
        """Whether the outcome represents a landing in progress or completed."""
        return _is_landing(self)

    def is_skipped(self) -> bool:
        """Whether the outcome was skipped."""
        return self.last_outcome == RebaseOutcomeKind.SKIPPED

    @property
    def is_intervention_required(self) -> bool:
        """Whether operator intervention is required."""
        return self.last_outcome in (
            RebaseOutcomeKind.INTERVENTION_REQUIRED,
            RebaseOutcomeKind.DIAGNOSTIC_INTERVENTION_REQUIRED,
            RebaseOutcomeKind.UNKNOWN,
        )


_DECISION_DISPLAY_MAP: dict[str, str] = {
    "report_terminal_success": "terminal",
    "report_terminal_failure": "terminal",
    "report_intervention_required": "intervention_required",
}

_OUTCOME_DISPLAY_MAP: dict[RebaseOutcomeKind, str] = {
    RebaseOutcomeKind.INTEGRATING: "integrating",
    RebaseOutcomeKind.WAITING: "waiting",
    RebaseOutcomeKind.DIAGNOSING: "evidence_diagnosis",
    RebaseOutcomeKind.EVIDENCE_DIAGNOSIS: "evidence_diagnosis",
    RebaseOutcomeKind.INTERVENTION_REQUIRED: "intervention_required",
    RebaseOutcomeKind.DIAGNOSTIC_INTERVENTION_REQUIRED: "intervention_required",
    RebaseOutcomeKind.TERMINAL: "terminal",
    RebaseOutcomeKind.COMPLETED: "terminal",
}

#: Publication decisions that keep the obligation open. The
#: terminal decision (``publication_terminal``) is handled
#: separately and is NOT in this set; the closed-obligation
#: display state is "terminal", not "publishing".
_PUBLISHING_DECISIONS: frozenset[str] = frozenset(
    {
        "publish_now",
        "publishing",
        "publish_deferred_remote",
        "publish_deferred_freshness",
    }
)


def _resolve_operation_display_state(rebase: RebaseState) -> str | None:
    """Derive the eight-category display state from current authority outputs.

    Pure: takes a :class:`RebaseState` snapshot and returns one of the
    eight :class:`OperationDisplayState` values (or ``None`` when no
    authority has reported yet). The function never infers a state
    from prose alone; every branch reads a typed field whose owner
    is a single authority.
    """
    decision_state = _decision_display_state(rebase)
    if decision_state is not None:
        return decision_state
    return _prose_display_state(rebase)


def _decision_display_state(rebase: RebaseState) -> str | None:
    if rebase.last_decision in _DECISION_DISPLAY_MAP:
        return _DECISION_DISPLAY_MAP[rebase.last_decision]
    if _is_evidence_diagnosis(rebase):
        return "evidence_diagnosis"
    if _is_conflict_resolution(rebase):
        return "conflict_resolution"
    if rebase.last_publication_decision == "publication_terminal":
        return "terminal"
    if rebase.last_publication_decision in _PUBLISHING_DECISIONS:
        return "publishing"
    return None


def _prose_display_state(rebase: RebaseState) -> str | None:
    if rebase.is_landing():
        return "landing"
    if rebase.is_terminal_action():
        return "terminal"
    if rebase.last_outcome is not None:
        return _OUTCOME_DISPLAY_MAP.get(rebase.last_outcome)
    return None


def _is_evidence_diagnosis(rebase: RebaseState) -> bool:
    return (
        rebase.last_outcome in (RebaseOutcomeKind.DIAGNOSING, RebaseOutcomeKind.EVIDENCE_DIAGNOSIS)
        or bool(rebase.diagnostic_ownership)
        or rebase.recovery_record_retained
    )


def _is_conflict_resolution(rebase: RebaseState) -> bool:
    return (
        rebase.last_outcome == RebaseOutcomeKind.CONFLICT
        or rebase.resolving_rebase
        or rebase.resolving_merge
    )


def _is_landing(rebase: RebaseState) -> bool:
    return (
        rebase.last_outcome in (RebaseOutcomeKind.REBASED, RebaseOutcomeKind.MERGED)
        and not rebase.fast_forwarded
    ) or (
        rebase.last_outcome
        in (
            RebaseOutcomeKind.REBASED,
            RebaseOutcomeKind.MERGED,
            RebaseOutcomeKind.FAST_FORWARDED,
            RebaseOutcomeKind.SKIPPED,
            RebaseOutcomeKind.REMOTE_PUSHED,
        )
        and rebase.fast_forwarded
    )
