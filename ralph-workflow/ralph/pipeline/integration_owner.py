"""Single owner adapter for Ralph's integration decisions.

This module is the ONE place an integration decision is produced. Every
other seam (the director, the run loop, the recovery paths) asks this
module and acts on its verdict. The owner performs exactly five steps
per decision, in this order:

1. **Observe** - build the seven-dimension
   :class:`~ralph.pipeline.integration_observation.Observation` through the
   existing director machinery
   (:meth:`ralph.pipeline.integration_director.IntegrationDirector.observe`).
   A caller that already holds an observation may pass it in; the owner
   then never probes the workspace twice.
2. **Load the ledger** - read the durable, restart-proof attempt ledger
   published in :mod:`ralph.pipeline.integration_attempt_ledger`.
3. **Decide** - refine the observation's worktree state into a typed
   dirt shape through the one classifier, then call the pure, total
   decision function
   :func:`ralph.pipeline.integration_decision.decide` with the
   observation, the ledger state and the shape/attribution refinement.
   The table is the only place that decides *what* to do; the
   refinement is what lets its dirty-tree rows answer the cleanup
   ladder instead of the legacy ``COMMIT``.
4. **Charge** - apply the durable charge for that decision (see the
   charge rule below). The charge is durable BEFORE the caller runs the
   action, so a crash between the charge and the effect still counts the
   attempt: a spent rung never resurrects on restart.
5. **Project** - map the resulting
   :class:`~ralph.pipeline.integration_decision.NextAction` onto the
   :class:`~ralph.pipeline._integration_action.IntegrationAction` closed
   vocabulary and render a reason derived from the CURRENT observation.

Configured target and remote
----------------------------

The owner observes the CONFIGURED integration target branch and remote
name, never a hardcoded pair. Every entry point therefore accepts the
configured ``target`` and ``remote`` and hands them to the director
(:meth:`ralph.pipeline.integration_director.IntegrationDirector.observe`),
which is the only component that reads the target/feature/remote refs.
When a caller supplies no configured name, the director's own defaults
(``main`` / ``origin``) apply; a caller that HAS a configured target
(``release``, ``tacos``, ...) must pass it, otherwise the observation
would be taken against a branch the operator never configured and a
configured non-default target could never converge.

Charge rule
-----------

A decision is **chargeable** (mutation-class) when its ``NextAction``
asks the boundary to change integration state. The chargeable actions
are :data:`MUTATION_CLASS_ACTIONS`:

``COMMIT``, ``LAND``, ``REINTEGRATE``, ``RESUME_OWNED_OPERATION``,
``REPAIR``, ``DISPATCH_RESOLVER``, ``REBUILD_ON_TARGET``.

* A chargeable decision is charged to the durable ledger **exactly
  once**: one repeat count for the observation's ``(signature, action)``
  key, persisted before the caller acts. Two charges for one decision
  would burn the escalation budget twice for a single attempt, which is
  exactly the drift this owner removes.
* A **non-chargeable** decision consumes **no escalation budget**: it
  records no repeat count and never advances the ladder rung. The
  non-chargeable actions are ``CONTINUE_WITH_INTEGRATION_PENDING`` (the
  escalation ladder is exhausted, so the boundary stops spending budget)
  and ``WAIT_FOR_ENVIRONMENT`` (nothing was observed that could be
  acted on).
* A pure environment wait is the one case that still writes to the
  ledger: it records the unknown observation against the durable
  **unknown-attempt budget**, a separate finite budget whose only job
  is to make the wait finite. Without it the boundary would wait
  forever, which is the livelock this owner exists to remove. The
  unknown-attempt budget is NOT escalation budget - it never records a
  repeat count and never advances the rung.

Ledger seams
------------

The owner owns the WHOLE lifecycle of the durable attempt ledger:

* :func:`load_integration_attempts` is the read seam. It returns the
  durable ledger projected onto the decision-facing
  :class:`~ralph.pipeline.integration_decision.AttemptState`, so a
  caller that decides for itself never touches
  :class:`~ralph.pipeline.integration_attempt_ledger.AttemptLedger`
  directly.
* :func:`charge_integration_attempt` is the write seam and the ONLY
  function in the codebase that writes the durable ledger. Every
  decision - the owner's own and the director's - is charged through
  it, so exactly one persisted charge exists per decision.

A caller that asks this module for a full verdict
(:func:`decide_integration_verdict`) goes through both seams, so it
can never charge twice either.

Projection contract
-------------------

:data:`NEXT_ACTION_TO_INTEGRATION_ACTION` is total over the
:class:`~ralph.pipeline.integration_decision.NextAction` members: every
member maps to exactly one
:class:`~ralph.pipeline._integration_action.IntegrationAction`. A member
without a mapping (a future addition to the closed vocabulary) raises
:class:`ValueError` instead of silently defaulting - the projection
fails closed. An import-time invariant keeps the table exhaustive, so a
new ``NextAction`` member cannot ship without a mapping.

Import-cycle note
-----------------

The director is imported lazily: the director delegates its ledger read
/ bump / persist to this module, so a module-level import in both
directions would be an import cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final

from ralph.pipeline._integration_action import IntegrationAction
from ralph.pipeline._record_fact_outcome import RecordFactOutcome
from ralph.pipeline.integration_attempt_ledger import AttemptLedger
from ralph.pipeline.integration_decision import (
    CLEANUP_LADDER_ORDER,
    AttemptState,
    CleanupLadderRung,
    DecisionContext,
    DirtAttribution,
    NextAction,
    decide,
)
from ralph.pipeline.integration_observation import (
    DirtShape,
    IntegrationRecordState,
    classify_worktree,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.auto_integrate_record import IntegrationRecord
    from ralph.pipeline.integration_attempt_ledger import AttemptLedgerState
    from ralph.pipeline.integration_observation import Observation

__all__ = [
    "MUTATION_CLASS_ACTIONS",
    "NEXT_ACTION_TO_INTEGRATION_ACTION",
    "NEXT_ACTION_TO_LADDER_RUNG",
    "IntegrationOwnerDecision",
    "RecordFactOutcome",
    "charge_integration_attempt",
    "decide_integration",
    "decide_integration_verdict",
    "is_chargeable",
    "ladder_rung_for_action",
    "ladder_rung_for_record_fact_outcome",
    "load_integration_attempts",
    "project_integration_action",
]


#: The mutation-class decisions: the actions that ask the boundary to
#: change integration state. Exactly these decisions are charged to the
#: durable attempt ledger; every other decision spends no escalation
#: budget. See the module docstring for the full charge rule.
MUTATION_CLASS_ACTIONS: Final[frozenset[NextAction]] = frozenset(
    {
        NextAction.COMMIT,
        NextAction.LAND,
        NextAction.REINTEGRATE,
        NextAction.RESUME_OWNED_OPERATION,
        NextAction.REPAIR,
        NextAction.DISPATCH_RESOLVER,
        NextAction.REBUILD_ON_TARGET,
        # Dirty-tree cleanup ladder (U1 contract): every rung mutates
        # refs, the index, the tree or the git config, so every rung is
        # charged against the durable budget — intent before, outcome
        # after.
        NextAction.COMMIT_VIA_COMMIT_PHASE,
        NextAction.SAVE_AND_RESTORE,
        NextAction.PRESERVE_VIA_TEMP_INDEX,
        NextAction.QUARANTINE_AND_RESTORE,
        NextAction.REBUILD_IN_FRESH_WORKTREE,
        NextAction.ADAPT_ENVIRONMENT_AND_RETRY,
        NextAction.LAST_RESORT_RECOVERY,
    }
)

#: Total projection from the decision vocabulary onto the integration
#: action vocabulary. Every ``NextAction`` member maps to exactly one
#: ``IntegrationAction``. The table is public so a caller (and a test)
#: can inspect the contract without reaching into a private name; it is
#: a constant and MUST NOT be mutated at runtime.
#:
#: =======================================  ==============================
#: ``NextAction``                           ``IntegrationAction``
#: =======================================  ==============================
#: ``COMMIT``                               ``CONTINUE_GIT_OPERATION``
#: ``LAND``                                 ``LAND_TARGET``
#: ``REINTEGRATE``                          ``REINTEGRATE_AGAINST_TARGET``
#: ``RESUME_OWNED_OPERATION``               ``CONTINUE_GIT_OPERATION``
#: ``REPAIR``                               ``RESOLVE_ACTIVE_OPERATION``
#: ``DISPATCH_RESOLVER``                    ``RESOLVE_ACTIVE_OPERATION``
#: ``REBUILD_ON_TARGET``                    ``REINTEGRATE_AGAINST_TARGET``
#: ``CONTINUE_WITH_INTEGRATION_PENDING``    ``WAIT_FOR_BOUNDED_RETRY``
#: ``WAIT_FOR_ENVIRONMENT``                 ``WAIT_FOR_BOUNDED_RETRY``
#: =======================================  ==============================
#:
#: The two wait-shaped decisions share one action because neither asks
#: the boundary to mutate anything: the escalation ladder is exhausted
#: (``CONTINUE_WITH_INTEGRATION_PENDING``) or the environment could not
#: be read (``WAIT_FOR_ENVIRONMENT``). The repair and resolver rungs
#: share ``RESOLVE_ACTIVE_OPERATION`` because both keep working on the
#: in-flight integration operation.
NEXT_ACTION_TO_INTEGRATION_ACTION: Final[dict[NextAction, IntegrationAction]] = {
    NextAction.COMMIT: IntegrationAction.CONTINUE_GIT_OPERATION,
    NextAction.LAND: IntegrationAction.LAND_TARGET,
    NextAction.REINTEGRATE: IntegrationAction.REINTEGRATE_AGAINST_TARGET,
    NextAction.RESUME_OWNED_OPERATION: IntegrationAction.CONTINUE_GIT_OPERATION,
    NextAction.REPAIR: IntegrationAction.RESOLVE_ACTIVE_OPERATION,
    NextAction.DISPATCH_RESOLVER: IntegrationAction.RESOLVE_ACTIVE_OPERATION,
    NextAction.REBUILD_ON_TARGET: IntegrationAction.REINTEGRATE_AGAINST_TARGET,
    NextAction.CONTINUE_WITH_INTEGRATION_PENDING: IntegrationAction.WAIT_FOR_BOUNDED_RETRY,
    NextAction.WAIT_FOR_ENVIRONMENT: IntegrationAction.WAIT_FOR_BOUNDED_RETRY,
    # Dirty-tree cleanup ladder (U1 contract). The coarse lifecycle
    # vocabulary cannot express the distinction between "commit via the
    # commit phase" and "save to a backup ref and restore" — the seams
    # that need it consume the typed NextAction / PlanningSyncVerdict —
    # so the projection groups them by what the boundary does at this
    # seam: git plumbing (continue), a rebuild against the target
    # (reintegrate), or a bounded wait on the machine (wait).
    NextAction.COMMIT_VIA_COMMIT_PHASE: IntegrationAction.CONTINUE_GIT_OPERATION,
    NextAction.SAVE_AND_RESTORE: IntegrationAction.CONTINUE_GIT_OPERATION,
    NextAction.PRESERVE_VIA_TEMP_INDEX: IntegrationAction.CONTINUE_GIT_OPERATION,
    NextAction.QUARANTINE_AND_RESTORE: IntegrationAction.CONTINUE_GIT_OPERATION,
    NextAction.REBUILD_IN_FRESH_WORKTREE: IntegrationAction.REINTEGRATE_AGAINST_TARGET,
    NextAction.ADAPT_ENVIRONMENT_AND_RETRY: IntegrationAction.WAIT_FOR_BOUNDED_RETRY,
    NextAction.LAST_RESORT_RECOVERY: IntegrationAction.CONTINUE_GIT_OPERATION,
}

#: Rung-level projection of the cleanup-ladder decisions onto the
#: executor entry points in
#: :mod:`ralph.pipeline.integration_ladder`. The coarse
#: :class:`IntegrationAction` vocabulary cannot express the distinction
#: between the ladder rungs (that is why they share
#: ``CONTINUE_GIT_OPERATION`` / ``REINTEGRATE_AGAINST_TARGET`` /
#: ``WAIT_FOR_BOUNDED_RETRY`` above); this table restores it: a caller
#: that holds an :class:`IntegrationOwnerDecision` with a non-None
#: ``ladder_rung`` dispatches the rung through
#: :func:`ralph.pipeline.integration_ladder.run_ladder_rung` instead of
#: treating the action as a generic git continuation. ``None`` means the
#: decision is not a cleanup-ladder rung and no ladder executor runs.
#:
#: The table also routes the typed fail-closed
#: :class:`RecordFactOutcome` verdicts (durable record facts the pure
#: observation cannot express). Both route to the FIRST rung,
#: ``NORMAL_CLEANUP``: the ladder preserves the work to backup refs
#: before any destructive step and re-enters the rebase-first path, so
#: a stale durable claim escalates autonomously — never to an operator
#: hand-off. The mapping is total over the cleanup-ladder actions and
#: the record-fact outcomes and constant — it MUST NOT be mutated at
#: runtime.
#:
#: =====================================================
#: Key                                    Rung
#: =====================================================
#: ``NextAction`` cleanup-ladder members  (see above)
#: ``RecordFactOutcome.STALE_INTENT``     ``NORMAL_CLEANUP``
#: ``RecordFactOutcome.DIAGNOSTIC_BUDGET_SPENT`` ``NORMAL_CLEANUP``
#: =====================================================
NEXT_ACTION_TO_LADDER_RUNG: Final[dict[NextAction | RecordFactOutcome, CleanupLadderRung]] = {
    NextAction.SAVE_AND_RESTORE: CleanupLadderRung.NORMAL_CLEANUP,
    NextAction.PRESERVE_VIA_TEMP_INDEX: CleanupLadderRung.ALTERNATE_PRESERVATION,
    NextAction.QUARANTINE_AND_RESTORE: CleanupLadderRung.QUARANTINE,
    NextAction.REBUILD_IN_FRESH_WORKTREE: CleanupLadderRung.FRESH_WORKTREE,
    NextAction.ADAPT_ENVIRONMENT_AND_RETRY: CleanupLadderRung.ENVIRONMENT_DEGRADE,
    NextAction.LAST_RESORT_RECOVERY: CleanupLadderRung.LAST_RESORT,
    RecordFactOutcome.STALE_INTENT: CleanupLadderRung.NORMAL_CLEANUP,
    RecordFactOutcome.DIAGNOSTIC_BUDGET_SPENT: CleanupLadderRung.NORMAL_CLEANUP,
}

#: Import-time invariant: the rung projection covers exactly the rungs
#: of the cleanup ladder, in order — a new rung without routing breaks
#: the import instead of silently defaulting to no executor.
if set(NEXT_ACTION_TO_LADDER_RUNG.values()) != set(CLEANUP_LADDER_ORDER):
    raise RuntimeError(
        "NEXT_ACTION_TO_LADDER_RUNG must route exactly the CLEANUP_LADDER_ORDER rungs"
    )

#: Import-time invariant: every typed fail-closed record-fact outcome
#: has rung routing — an outcome without routing would be a dead-end
#: verdict the boundary cannot act on (a silent return to blocking on a
#: person). Uses ``if``/``raise`` (not ``assert``) so it survives
#: ``python -O``.
_RECORD_FACT_OUTCOMES_WITHOUT_RUNG = set(RecordFactOutcome) - set(NEXT_ACTION_TO_LADDER_RUNG)
if _RECORD_FACT_OUTCOMES_WITHOUT_RUNG:
    raise RuntimeError(
        "RecordFactOutcome members without ladder-rung routing: "
        + ", ".join(sorted(member.value for member in _RECORD_FACT_OUTCOMES_WITHOUT_RUNG))
    )


@dataclass(frozen=True)
class IntegrationOwnerDecision:
    """The owner's full verdict for one integration decision.

    ``action`` is the projected integration action, ``reason`` explains
    it from the CURRENT observation, ``observation`` is the seven-
    dimension snapshot the decision was made from, ``attempts`` is the
    durable ledger state AFTER the charge, and ``escalation_charged``
    records whether the decision consumed escalation budget (a
    mutation-class decision) rather than being a pure wait.
    ``ladder_rung`` is the rung-level projection of a cleanup-ladder
    decision (``None`` for every other decision): when it is not
    ``None`` the caller dispatches the rung through
    :func:`ralph.pipeline.integration_ladder.run_ladder_rung` instead
    of acting on the coarse ``action`` alone.
    ``dirt_shape`` / ``dirt_attribution`` are the typed dirt refinement
    the decision was made from (``NONE`` / ``UNKNOWN`` when the tree
    carried no blocking dirt).
    """

    action: IntegrationAction
    reason: str
    observation: Observation
    attempts: AttemptLedgerState
    escalation_charged: bool
    ladder_rung: CleanupLadderRung | None = None
    dirt_shape: DirtShape = DirtShape.NONE
    dirt_attribution: DirtAttribution = DirtAttribution.UNKNOWN


def project_integration_action(next_action: NextAction) -> IntegrationAction:
    """Project ``next_action`` onto the closed ``IntegrationAction`` vocabulary.

    The projection is total over the ``NextAction`` members. A
    member without a mapping raises :class:`ValueError`: an unmapped
    member must never silently default to a permissive action, because
    the projected action is what the pipeline acts on.
    """
    try:
        return NEXT_ACTION_TO_INTEGRATION_ACTION[next_action]
    except KeyError as exc:
        raise ValueError(f"no IntegrationAction mapping for NextAction {next_action!r}") from exc


def is_chargeable(next_action: NextAction) -> bool:
    """True when ``next_action`` is a mutation-class (chargeable) decision.

    A chargeable decision is charged to the durable attempt ledger
    exactly once; a non-chargeable decision spends no escalation
    budget. See :data:`MUTATION_CLASS_ACTIONS` and the module docstring.
    """
    return next_action in MUTATION_CLASS_ACTIONS


def ladder_rung_for_action(next_action: NextAction) -> CleanupLadderRung | None:
    """Project ``next_action`` onto its fallback-ladder rung, if it has one.

    ``None`` means the decision is not a cleanup-ladder rung; a rung
    means the caller executes it through
    :func:`ralph.pipeline.integration_ladder.run_ladder_rung`. The
    routing is a pure projection over :data:`NEXT_ACTION_TO_LADDER_RUNG`:
    the decision core never learns about executors.
    """
    return NEXT_ACTION_TO_LADDER_RUNG.get(next_action)


def ladder_rung_for_record_fact_outcome(outcome: RecordFactOutcome) -> CleanupLadderRung:
    """Project a typed fail-closed record-fact outcome onto its ladder rung.

    The routing is a pure projection over :data:`NEXT_ACTION_TO_LADDER_RUNG`
    and is total over :class:`RecordFactOutcome` (enforced at import
    time): the caller that holds a typed stale-intent or
    diagnostic-budget-spent verdict dispatches the returned rung through
    :func:`ralph.pipeline.integration_ladder.run_ladder_rung` — the
    ladder preserves the work to backup refs first and never ends in an
    operator hand-off.
    """
    return NEXT_ACTION_TO_LADDER_RUNG[outcome]


def decide_integration(
    root: Path,
    *,
    record: IntegrationRecord | None = None,
    agent_active: bool = False,
    ledger_path: Path | None = None,
    observation: Observation | None = None,
    unknown_budget: int = 3,
    repeat_threshold: int = 2,
    target: str | None = None,
    remote: str | None = None,
) -> tuple[IntegrationAction, str]:
    """Decide what integration does next for ``root`` and explain why.

    Args:
        root: The workspace root the decision is made for. It anchors
            the durable attempt ledger when ``ledger_path`` is not
            given.
        record: The durable integration record the caller already holds
            (``None`` when the caller holds none). The owner reconciles
            it with the observed view: a caller that holds a record the
            observation does not see is holding a stale view, and the
            owner fails closed to the repair rung.
        agent_active: Whether an agent phase is active in the worktree.
        ledger_path: Override for the attempt-ledger file path.
        observation: A pre-built observation to decide from. ``None``
            makes the owner observe through the director machinery.
        unknown_budget: The unknown-attempt budget applied to a
            freshly-loaded ledger state.
        repeat_threshold: The repeat threshold applied to a
            freshly-loaded ledger state.
        target: The CONFIGURED integration target branch name. ``None``
            uses the director's default (``main``). A caller with a
            configured target must pass it so the observation is taken
            against the configured branch.
        remote: The CONFIGURED remote name. ``None`` uses the
            director's default (``origin``).

    Returns:
        The projected :class:`IntegrationAction` and a reason derived
        from the current observation.
    """
    verdict = decide_integration_verdict(
        root,
        record=record,
        agent_active=agent_active,
        ledger_path=ledger_path,
        observation=observation,
        unknown_budget=unknown_budget,
        repeat_threshold=repeat_threshold,
        target=target,
        remote=remote,
    )
    return verdict.action, verdict.reason


def decide_integration_verdict(
    root: Path,
    *,
    record: IntegrationRecord | None = None,
    agent_active: bool = False,
    ledger_path: Path | None = None,
    observation: Observation | None = None,
    unknown_budget: int = 3,
    repeat_threshold: int = 2,
    target: str | None = None,
    remote: str | None = None,
    dirt_shape: DirtShape | None = None,
    dirt_attribution: DirtAttribution = DirtAttribution.UNOWNED,
) -> IntegrationOwnerDecision:
    """Decide, charge and project; return the owner's full verdict.

    This is the seam the director delegates to: it owns the ledger
    read, the ledger bump and the ledger persist, and it returns the
    charged ledger state so the caller never has to re-read it.

    The CONFIGURED ``target`` and ``remote`` reach the observation
    layer: they are handed to the director, which reads the
    target/feature/remote refs against those names.

    The dirt-shape refinement is what makes the cleanup ladder
    reachable through the owner: ``dirt_shape`` refines the
    dirty-tree rows of the decision table (rule 9), so a dirty tree
    with a blocking shape answers the deterministic cleanup action
    instead of the legacy ``COMMIT`` and the verdict carries the
    matching :class:`~ralph.pipeline.integration_decision.CleanupLadderRung`.
    ``None`` refines internally from the observation itself, through
    the ONE classifier's state arm
    (:func:`~ralph.pipeline.integration_observation.classify_worktree`)
    — a pure projection, no second probe. ``dirt_attribution``
    defaults to ``UNOWNED``: the boundary holds no phase ledger, so
    dirt it observes is residue, never a phase's own work (which the
    commit phase's seam packages, never this one).
    """
    current = (
        observation
        if observation is not None
        else _observe(
            root,
            agent_active=agent_active,
            target=target,
            remote=remote,
        )
    )
    current = _reconcile_record(current, record)
    shape = dirt_shape if dirt_shape is not None else _refine_dirt_shape(current)
    attempts = load_integration_attempts(
        root,
        ledger_path=ledger_path,
        unknown_budget=unknown_budget,
        repeat_threshold=repeat_threshold,
    )
    next_action = decide(
        DecisionContext(
            observation=current,
            attempts=attempts,
            dirt_shape=shape,
            dirt_attribution=dirt_attribution,
        )
    )
    action = project_integration_action(next_action)
    charged = charge_integration_attempt(
        root,
        observation=current,
        next_action=next_action,
        ledger_path=ledger_path,
        unknown_budget=unknown_budget,
        repeat_threshold=repeat_threshold,
    )
    return IntegrationOwnerDecision(
        action=action,
        reason=_reason(current, next_action, action),
        observation=current,
        attempts=charged,
        escalation_charged=is_chargeable(next_action),
        ladder_rung=ladder_rung_for_action(next_action),
        dirt_shape=shape,
        dirt_attribution=dirt_attribution,
    )


def load_integration_attempts(
    root: Path,
    *,
    ledger_path: Path | None = None,
    unknown_budget: int = 3,
    repeat_threshold: int = 2,
) -> AttemptState:
    """Load the durable attempt ledger and project it onto the decision state.

    This is the owner's ledger READ seam: it is the only supported way
    for a caller to learn where the escalation ladder stands, so the
    durable ledger is read in exactly one place.

    Args:
        root: The workspace root the ledger is anchored to.
        ledger_path: Override for the attempt-ledger file path.
        unknown_budget: The unknown-attempt budget applied to a
            freshly-loaded ledger state.
        repeat_threshold: The repeat threshold applied to a
            freshly-loaded ledger state.

    Returns:
        The durable ledger state projected onto the decision-facing
        :class:`~ralph.pipeline.integration_decision.AttemptState`. The
        projection is lossless for the decision: the fields the durable
        shape adds (``version``, the saturating aggregate and the
        global action charge) are not inputs to the decision table.
    """
    ledger = AttemptLedger(
        root,
        ledger_path=ledger_path,
        unknown_budget=unknown_budget,
        repeat_threshold=repeat_threshold,
    )
    return _to_attempt_state(ledger.read())


def charge_integration_attempt(
    root: Path,
    *,
    observation: Observation,
    next_action: NextAction,
    ledger_path: Path | None = None,
    unknown_budget: int = 3,
    repeat_threshold: int = 2,
) -> AttemptLedgerState:
    """Charge one decided action through the owner's single charge path.

    This is the ONLY function that writes the durable attempt ledger.
    A caller that has already decided what integration does next hands
    that decision over here; the owner applies the charge rule (see the
    module docstring) and persists the result.

    Args:
        root: The workspace root the ledger is anchored to.
        observation: The observation the decision was made from. Its
            state signature is the key the durable repeat count is
            recorded under.
        next_action: The decided action. A mutation-class action is
            charged once against the escalation budget; a pure
            environment wait is charged against the separate, finite
            unknown-attempt budget; every other action writes nothing.
        ledger_path: Override for the attempt-ledger file path.
        unknown_budget: The unknown-attempt budget applied to a
            freshly-loaded ledger state.
        repeat_threshold: The repeat threshold applied to a
            freshly-loaded ledger state.

    Returns:
        The durable ledger state AFTER the charge, or the unchanged
        durable state when the decision wrote nothing.
    """
    ledger = AttemptLedger(
        root,
        ledger_path=ledger_path,
        unknown_budget=unknown_budget,
        repeat_threshold=repeat_threshold,
    )
    state = ledger.read()
    charged = _charge(ledger, observation, next_action, attempts=_to_attempt_state(state))
    return state if charged is None else charged


def _observe(
    root: Path,
    *,
    agent_active: bool,
    target: str | None,
    remote: str | None,
) -> Observation:
    """Observe ``root`` through the existing director machinery.

    The director is imported lazily because the director delegates its
    ledger work to this module: a module-level import in both
    directions would be an import cycle.

    The CONFIGURED target branch and remote name reach the observation
    layer here. The director's own defaults (``main`` / ``origin``) are
    used ONLY when the caller supplies no configured name, so a
    configured non-default target/remote is observed against the names
    the operator configured instead of the hardcoded pair.
    """
    from ralph.pipeline.integration_director import (
        DEFAULT_REMOTE,
        DEFAULT_TARGET_BRANCH,
        IntegrationDirector,
    )

    return IntegrationDirector(
        root,
        target=DEFAULT_TARGET_BRANCH if target is None else target,
        remote=DEFAULT_REMOTE if remote is None else remote,
        agent_active=agent_active,
    ).observe()


def _reconcile_record(observation: Observation, record: IntegrationRecord | None) -> Observation:
    """Fail closed when the caller's record view contradicts the observation.

    A caller that holds a durable record while the observation proves
    there is none is holding a stale view (the record was released
    mid-cycle). Treating that as "no record" would let the boundary
    decide as if no integration obligation existed, so the owner
    projects it onto the repairable record state instead.
    """
    if record is None or observation.record is not IntegrationRecordState.NONE:
        return observation
    return replace(observation, record=IntegrationRecordState.STALE)


def _refine_dirt_shape(observation: Observation) -> DirtShape:
    """Refine the observation's worktree state into a typed dirt shape.

    The projection runs through the ONE classifier
    (:func:`~ralph.pipeline.integration_observation.classify_worktree`)
    state arm, so the owner, the planning gate and the residue cleanup
    can never disagree about the tree. The state arm is deliberately
    conservative: it refines only what the observation already proves
    (a ``TRACKED_CHANGES`` tree is an ``UNSTAGED_MODIFICATION`` shape),
    and it never invents evidence the observation does not carry —
    ``LINE_ENDING_ONLY`` / ``UNTRACKED_BLOCKING`` refinements require
    content probes a caller that holds them passes explicitly via
    ``dirt_shape``. An unknown worktree reading yields
    :attr:`DirtShape.UNKNOWN`, which the decision table never treats
    as blocking evidence, so an unreadable probe still backs off
    instead of selecting a cleanup rung.
    """
    return classify_worktree(observation.worktree).dirt_shape


def _to_attempt_state(state: AttemptLedgerState) -> AttemptState:
    """Project the durable ledger state onto the in-memory decision state.

    :class:`AttemptLedgerState` is the persisted shape (it carries a
    ``version`` field plus the durable aggregates); ``AttemptState`` is
    the in-memory shape the decision table consumes. The two share
    field names and semantics, so the projection is a per-field copy.
    """
    return AttemptState(
        rung=state.rung,
        repeat_counts=state.repeat_counts,
        unknown_attempts=state.unknown_attempts,
        unknown_budget=state.unknown_budget,
        repeat_threshold=state.repeat_threshold,
    )


def _charge(
    ledger: AttemptLedger,
    observation: Observation,
    next_action: NextAction,
    *,
    attempts: AttemptState,
) -> AttemptLedgerState | None:
    """Apply the durable charge for one decision; return the new state.

    At most ONE ledger write happens per decision:

    * a mutation-class decision is charged exactly once against the
      durable escalation budget (one repeat count for the observation's
      ``(signature, action)`` key) and returns the charged state;
    * a pure environment wait is charged against the separate, finite
      unknown-attempt budget so the wait can end - it returns the
      charged state because the unknown budget IS a durable write, but
      it spends no escalation budget: it records no repeat count and
      never advances the rung;
    * every other non-chargeable decision writes nothing at all and
      returns ``None``, so no escalation budget is spent on a decision
      that changed nothing.
    """
    signature = attempts.signature(observation)
    action_value: str = next_action.value
    if next_action is NextAction.WAIT_FOR_ENVIRONMENT:
        return ledger.charge(signature, action_value, is_unknown=True)
    if not is_chargeable(next_action):
        return None
    return ledger.charge(signature, action_value, is_unknown=False)


def _reason(observation: Observation, next_action: NextAction, action: IntegrationAction) -> str:
    """Render the decision reason from the CURRENT observation only.

    Every field in the reason comes from the observation that produced
    the decision. No stored message (a durable record phase, a last
    reason, a persisted probe error) is ever echoed back, so a reason
    can never describe a state the boundary has already left behind.
    """
    return (
        f"worktree={observation.worktree.value}, "
        f"target_vs_feature={observation.target_vs_feature.value}, "
        f"target_vs_remote={observation.target_vs_remote.value}, "
        f"head={observation.head.value}, "
        f"in_progress={observation.in_progress_kind.value}/"
        f"{observation.in_progress_ownership.value}, "
        f"record={observation.record.value}, "
        f"agent_active={str(observation.agent_active).lower()}: "
        f"{next_action.value} -> {action.value}"
    )


def _assert_projection_is_total() -> None:
    """Fail closed when the projection table is not exhaustive.

    A new ``NextAction`` member without a mapping must break the import
    instead of silently defaulting to a permissive action. The check
    uses ``if``/``raise RuntimeError`` (not ``assert``) so it survives
    ``python -O``.
    """
    mapped = set(NEXT_ACTION_TO_INTEGRATION_ACTION)
    members = set(NextAction)
    missing = members - mapped
    if missing:
        raise RuntimeError(
            "NextAction members without an IntegrationAction mapping: "
            + ", ".join(sorted(member.value for member in missing))
        )
    unknown = mapped - members
    if unknown:
        raise RuntimeError(
            "IntegrationAction mapping for unknown NextAction members: "
            + ", ".join(sorted(member.value for member in unknown))
        )


_assert_projection_is_total()
