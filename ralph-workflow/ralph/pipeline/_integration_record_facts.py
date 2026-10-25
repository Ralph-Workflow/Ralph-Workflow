"""Durable record facts: the integration decisions the observation table cannot express.

Ralph's integration lifecycle has exactly ONE owner for the MUTATION
ladder: :mod:`ralph.pipeline.integration_owner`. The owner observes the
live worktree, consults the durable attempt ledger and projects the pure
decision table onto the closed
:class:`~ralph.pipeline._integration_action.IntegrationAction`
vocabulary. Its projection can only ever produce the five mutation-ladder
actions:

* ``RESOLVE_ACTIVE_OPERATION``
* ``CONTINUE_GIT_OPERATION``
* ``REINTEGRATE_AGAINST_TARGET``
* ``LAND_TARGET``
* ``WAIT_FOR_BOUNDED_RETRY``

Three members of the closed vocabulary are statements about DURABLE
STATE rather than about the live worktree, so no pure observation can
express them:

* ``PUBLISH_AFTER_LANDING`` -- a durable publication obligation;
* ``COMPLETE_WITH_PROOF`` -- a durable completion proof.

(The retired projection's ``REQUIRE_INTERVENTION`` fail-closed value
stays in :mod:`ralph.pipeline._integration_action` with zero production
producers; fail-closed record facts are typed
:class:`~ralph.pipeline.integration_owner.RecordFactOutcome` verdicts
routed to the autonomous fallback ladder instead — see below.)

**The owner decides the mutation ladder; record-fact completion and
publication stay with the seam.** This module is that single shared
seam: every integration boundary asks the owner for the mutation ladder
and then applies the durable record facts through
:func:`apply_record_facts`, so no seam can re-derive a private decision
table of its own.

Which durable facts live here
-----------------------------

* the record's intervention flag (``diagnostic_intervention_required``)
  -- the fail-closed diagnostic-budget-spent verdict
  (:func:`record_requires_intervention`), applied against the LIVE
  observation so a stale claim cannot outrank Git (see "Git outranks
  the record" below);
* the persisted bounded-retry deadline (``retry_deadline``) -- the
  wait-versus-ladder-escalation decision at the release-refusal sites
  (:func:`busy_refusal_outcome`).

Fail-closed verdicts route to the ladder, never to a person
------------------------------------------------------------

When a durable record fact cannot prove a next step the boundary can
act on — the intervention flag is set and live Git does not supersede
it, a retained obligation has no record behind it, or a bounded-retry
deadline is missing, malformed or expired — the overlay returns a TYPED
:class:`~ralph.pipeline.integration_owner.RecordFactOutcome` verdict
(``STALE_INTENT`` / ``DIAGNOSTIC_BUDGET_SPENT``), never
``IntegrationAction.REQUIRE_INTERVENTION``. Each outcome maps to a
cleanup-ladder rung in
:data:`~ralph.pipeline.integration_owner.NEXT_ACTION_TO_LADDER_RUNG`:
the ladder preserves the work to backup refs FIRST and re-enters the
rebase-first path, ending in rung 6 (hard reset) — an autonomous
escalation, never a terminal block on an operator.

The publication obligation and the completion proof are NOT projected
here. A durable :class:`~ralph.pipeline.auto_integrate_record.IntegrationRecord`
carries neither: the remote-publication obligation lives in the rebase
state (and is decided by
:mod:`ralph.pipeline._publication_state_machine`), and the completion
proof is the diagnostic path's four-condition Git proof (and is decided
by :mod:`ralph.pipeline._integration_diagnostic_proof`). Both keep their
own record-fact drivers, so nothing is dropped by the owner's projection
covering only the mutation ladder.

Git outranks the record
-----------------------

The durable record holds INTENT (what the boundary meant to do),
OWNERSHIP (which worktree owns the operation), ATTEMPT HISTORY (how
many repairs were tried) and LEGACY-FORMAT IDENTITY (a record written
by an older Ralph version). It never holds a fact Git can answer.

A durable claim therefore outranks the live Git observation ONLY while
the obligation it describes is still live. When the live observation
proves the obligation is discharged -- or that the record belongs to
another worktree -- the durable claim is stale intent, and a stale
claim must NOT override a current, healthy Git observation. When the
claim IS still live but cannot prove a next step, the overlay fails
closed to a typed :class:`~ralph.pipeline.integration_owner.RecordFactOutcome`
verdict routed to the autonomous fallback ladder — never to a terminal
block on a person. :func:`live_git_supersedes_record` is that test, and
:func:`apply_record_facts` applies it.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Final

from ralph.pipeline._integration_action import IntegrationAction, RebaseOutcomeKind
from ralph.pipeline.integration_observation import (
    InProgressKind,
    IntegrationRecordState,
    TargetVsFeature,
    WorktreeState,
)
from ralph.pipeline.integration_owner import RecordFactOutcome
from ralph.pipeline.rebase_state import RebaseState

if TYPE_CHECKING:
    from ralph.pipeline.auto_integrate_record import IntegrationRecord
    from ralph.pipeline.integration_observation import Observation

__all__ = [
    "RECORD_FACT_ACTIONS",
    "apply_record_facts",
    "busy_refusal_outcome",
    "busy_refusal_state",
    "live_git_supersedes_record",
    "record_requires_intervention",
]


#: The closed set of integration actions that are decided from DURABLE
#: RECORD FACTS rather than from a live observation. The owner's
#: projection never produces any of them; the seams overlay them from
#: the durable facts through :func:`apply_record_facts`. A fail-closed
#: record fact that cannot prove a next step is NOT an
#: ``IntegrationAction`` at all: it is a typed
#: :class:`~ralph.pipeline.integration_owner.RecordFactOutcome` verdict
#: routed to the autonomous fallback ladder.
RECORD_FACT_ACTIONS: Final[frozenset[IntegrationAction]] = frozenset(
    {
        IntegrationAction.PUBLISH_AFTER_LANDING,
        IntegrationAction.COMPLETE_WITH_PROOF,
    }
)


def record_requires_intervention(record: IntegrationRecord | None) -> bool:
    """True when the durable record's diagnostic repair budget is spent.

    The flag is written by the diagnostic path when the repair-attempt
    cap is reached. It is durable ATTEMPT HISTORY: it says "the
    repairs this record describes have been tried and exhausted". It
    says NOTHING about the live Git state, so it may only outrank the
    live observation while the obligation it describes is still live --
    see :func:`live_git_supersedes_record`.
    """
    if record is None:
        return False
    return record.diagnostic_intervention_required is True


def live_git_supersedes_record(observation: Observation | None) -> bool:
    """True when the live Git observation supersedes a durable claim.

    The durable record holds intent, ownership, attempt history and
    legacy-format identity -- never a fact Git can answer. Two live
    facts make a durable claim stale:

    * the record belongs to ANOTHER worktree (the observation projects
      it as :attr:`~ralph.pipeline.integration_observation.IntegrationRecordState.FOREIGN`),
      so its claims are not about this workspace;
    * the integration obligation is DISCHARGED -- the feature is
      contained in the target, the worktree is clean and no Git
      operation is in flight -- so a claim that "a repair is stuck"
      describes a situation that no longer exists.

    An observation that cannot answer (an ``unknown`` dimension) never
    supersedes a durable claim: only a PROVEN healthy Git state may
    release a fail-closed verdict.
    """
    if observation is None:
        return False
    if observation.record is IntegrationRecordState.FOREIGN:
        return True
    return (
        observation.target_vs_feature in (TargetVsFeature.EQUAL, TargetVsFeature.AHEAD)
        and observation.worktree is WorktreeState.CLEAN
        and observation.in_progress_kind is InProgressKind.NONE
    )


def apply_record_facts(
    action: IntegrationAction,
    record: IntegrationRecord | None,
    *,
    retained: bool = False,
    observation: Observation | None = None,
) -> IntegrationAction | RecordFactOutcome:
    """Overlay the durable record facts on the owner's mutation-ladder answer.

    Args:
        action: The action the owner produced for the live observation.
        record: The durable record the caller holds (``None`` when the
            caller holds none).
        retained: True when the caller holds a RETAINED integration
            obligation. A retained obligation with no durable record
            behind it has nothing the boundary can act on, so the seam
            fails closed to
            :attr:`RecordFactOutcome.STALE_INTENT`.
        observation: The LIVE observation the owner's answer was made
            from. When it proves the durable claim stale (see
            :func:`live_git_supersedes_record`) the durable claim does
            not outrank it: Git outranks the record. ``None`` means the
            caller supplied no live evidence, and the fail-closed
            behaviour stands.

    Returns:
        The action the seam must act on. The owner's answer stands
        unless a durable record fact overrides it, and a durable record
        fact wins ONLY while the obligation it describes is still live:
        a stale durable claim never turns a current, healthy Git
        observation into anything other than the owner's answer. When a
        live durable claim cannot prove a next step, the return is a
        typed :class:`RecordFactOutcome` verdict
        (``DIAGNOSTIC_BUDGET_SPENT`` for the spent diagnostic repair
        budget, ``STALE_INTENT`` for an unprovable retained obligation)
        that the caller routes to the autonomous fallback ladder — each
        outcome maps to a cleanup-ladder rung in
        :data:`~ralph.pipeline.integration_owner.NEXT_ACTION_TO_LADDER_RUNG`.
    """
    if observation is not None and live_git_supersedes_record(observation):
        return action
    if record_requires_intervention(record):
        return RecordFactOutcome.DIAGNOSTIC_BUDGET_SPENT
    if record is None and retained:
        return RecordFactOutcome.STALE_INTENT
    return action


def busy_refusal_state(retry_deadline: float | None) -> RebaseState:
    """Build the transient scratch state that carries a persisted retry deadline.

    The state is never persisted: it exists only so the durable deadline
    can be read through the typed rebase-state fields. The legacy
    checkpoint-retention site in ``auto_integrate_recovery.py``
    intentionally keeps extra identity kwargs and is NOT migrated.
    """
    return RebaseState(
        last_outcome=RebaseOutcomeKind.CONFLICT,
        retry_deadline=retry_deadline,
    )


def busy_refusal_outcome(
    retry_deadline: float | None,
    *,
    clock: object = None,
) -> IntegrationAction | RecordFactOutcome:
    """Map a persisted ``retry_deadline`` to the typed retry decision.

    A durable, unexpired deadline authorises ``WAIT_FOR_BOUNDED_RETRY``;
    a missing, malformed, or expired deadline fails closed to the typed
    :attr:`RecordFactOutcome.STALE_INTENT` verdict, which the caller
    routes to the autonomous fallback ladder (the ladder preserves the
    work to backup refs and re-enters the rebase-first path — never an
    operator hand-off). The architecture forbids an unbounded wait, so
    an unusable deadline is never treated as "wait forever".
    """
    if not isinstance(retry_deadline, (int, float)) or isinstance(retry_deadline, bool):
        return RecordFactOutcome.STALE_INTENT
    now = _current_time(clock)
    if now is None:
        now = time.monotonic()
    if now >= float(retry_deadline):
        return RecordFactOutcome.STALE_INTENT
    return IntegrationAction.WAIT_FOR_BOUNDED_RETRY


def _current_time(clock: object) -> float | None:
    """Read the injected clock; ``None`` when it cannot answer.

    A callable clock is preferred; a numeric clock is accepted as a
    fixed reading. Anything else (and any raised error) leaves the
    decision to the monotonic fallback, so an unusable clock can never
    authorise an unbounded wait.
    """
    if callable(clock):
        try:
            value: object = clock()
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str):
                return float(value)
            return None
        except Exception:
            return None
    if isinstance(clock, (int, float)):
        return float(clock)
    return None
