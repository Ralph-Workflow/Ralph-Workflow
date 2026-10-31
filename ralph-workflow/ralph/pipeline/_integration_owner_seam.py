"""The one seam every integration boundary uses to ask the single owner.

Ralph's integration lifecycle has exactly ONE decision owner for the
MUTATION ladder: :mod:`ralph.pipeline.integration_owner`. The owner
observes the live worktree, consults the durable attempt ledger and
projects the pure decision table onto the closed
:class:`~ralph.pipeline._integration_action.IntegrationAction`
vocabulary. Every other seam -- the runner's dispatch invariant, the
run loop's blocking seam, the diagnostic handoff -- asks the owner
through this module and acts on its verdict. No seam re-derives the
ladder from a durable record.

The split between the owner and the durable record facts
---------------------------------------------------------

The owner's projection can only ever produce the five mutation-ladder
actions:

* ``RESOLVE_ACTIVE_OPERATION``
* ``CONTINUE_GIT_OPERATION``
* ``REINTEGRATE_AGAINST_TARGET``
* ``LAND_TARGET``
* ``WAIT_FOR_BOUNDED_RETRY``

The remaining two members of the closed vocabulary are statements
about DURABLE STATE, which no live observation can express:

* ``PUBLISH_AFTER_LANDING`` -- the remote-publication obligation. It
  lives in the rebase state and is decided by
  :mod:`ralph.pipeline._publication_state_machine`; the durable
  integration record does not carry it.
* ``COMPLETE_WITH_PROOF`` -- the positive completion proof. It lives in
  the diagnostic path's four-condition Git proof
  (:mod:`ralph.pipeline._integration_diagnostic_proof`); the durable
  integration record does not carry it.

A durable record fact that cannot prove a next step the boundary can
act on is NOT an ``IntegrationAction`` at all: it is a typed
:class:`~ralph.pipeline.integration_owner.RecordFactOutcome` verdict
(``STALE_INTENT`` / ``DIAGNOSTIC_BUDGET_SPENT``), decided by
:func:`ralph.pipeline._integration_record_facts.apply_record_facts`
against the LIVE observation (Git outranks the record). Each outcome
maps to a cleanup-ladder rung in
:data:`~ralph.pipeline.integration_owner.NEXT_ACTION_TO_LADDER_RUNG`
(the first rung: preserve the work to backup refs, then re-enter the
rebase-first path), so a fail-closed record fact escalates autonomously
through the ladder — never to a terminal block on an operator. The
retired projection's ``REQUIRE_INTERVENTION`` value survives only in
:mod:`ralph.pipeline._integration_action` with zero production
producers.

**The owner decides the mutation ladder; record-fact completion and
publication stay with the seam.** :func:`decide_seam_action` is that
seam: it asks the owner for the ladder and then overlays the durable
record facts through
:func:`ralph.pipeline._integration_record_facts.apply_record_facts`. A
seam that needs ``PUBLISH_AFTER_LANDING`` or ``COMPLETE_WITH_PROOF``
consults the driver that owns those facts -- it must never re-derive
them from a private decision table of its own.

The seam also carries the CONFIGURED target branch and remote name into
the owner, so the observation is taken against the branch and remote
the operator configured rather than against a hardcoded pair.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.pipeline._integration_action import IntegrationAction
from ralph.pipeline._integration_record_facts import apply_record_facts
from ralph.pipeline.integration_ladder import LadderStepResult, run_ladder_rung
from ralph.pipeline.integration_observation import (
    DirtShape,
    TargetVsFeature,
    WorktreeVerdict,
    classify_worktree,
    read_worktree_porcelain,
)
from ralph.pipeline.integration_owner import (
    RecordFactOutcome,
    decide_integration_verdict,
    ladder_rung_for_record_fact_outcome,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.auto_integrate_record import IntegrationRecord
    from ralph.pipeline.integration_actions import Clock
    from ralph.pipeline.integration_attempt_ledger import AttemptLedger
    from ralph.pipeline.integration_decision import CleanupLadderRung
    from ralph.pipeline.integration_ladder import (
        EnvironmentProbe,
        FileQuarantine,
        GitPlumbing,
    )
    from ralph.pipeline.integration_observation import Observation

__all__ = [
    "PlanningSyncVerdict",
    "decide_planning_sync",
    "decide_seam_action",
    "execute_planning_ladder_rung",
]


@dataclass(frozen=True)
class PlanningSyncVerdict:
    """Typed verdict for the planning gate's "is the branch in sync?" question.

    The planning gate consumes this instead of branching on prose reason
    strings: the same observation always yields the same verdict, at the
    planning gate, the boundary and the resolution gate alike.

    * ``action`` — the seam action integration will take next (the
      boundary's answer, overlaid with the durable record facts). When
      ``record_fact`` is not ``None`` the durable record facts could
      not prove a next step: the action is the coarse projection of the
      escalation rung (git plumbing) and the typed ``record_fact``
      verdict names why the ladder runs.
    * ``observation`` — the live seven-dimension snapshot behind the
      decision (reasons are rendered from it, never stored).
    * ``worktree`` / ``dirt_shape`` — the single classifier's verdict for
      the same tree and its typed dirt refinement; a blocking shape is
      the signal that residue cleanup (save-and-restore) must run before
      planning proceeds.
    * ``in_sync`` — the feature contains the target tip (``EQUAL`` or
      ``AHEAD`` on the target/feature axis).
    * ``may_plan`` — planning may start: the branch is in sync AND the
      classifier allows dispatch AND no fail-closed record fact holds
      planning. An unknown worktree probe, blocking dirt, an
      out-of-sync branch or a typed record-fact verdict all hold
      planning, each with the ``reason`` naming the current observation
      or the stale durable claim.
    * ``ladder_rung`` — the rung-level projection of a cleanup-ladder
      decision (``None`` for every other decision): when it is not
      ``None`` the caller dispatches the rung through
      :func:`execute_planning_ladder_rung` (which runs
      :func:`ralph.pipeline.integration_ladder.run_ladder_rung`)
      instead of acting on the coarse ``action`` alone. A verdict whose
      ``record_fact`` is not ``None`` carries the escalation rung (the
      first rung: preserve the work to backup refs).
    * ``record_fact`` — the typed fail-closed record-fact verdict
      (``None`` for every ordinary decision): when it is not ``None``
      the durable record could not prove a next step and the verdict
      escalates through the ladder — never to an operator hand-off.
    """

    action: IntegrationAction
    observation: Observation
    worktree: WorktreeVerdict
    dirt_shape: DirtShape
    in_sync: bool
    may_plan: bool
    reason: str
    ladder_rung: CleanupLadderRung | None = None
    record_fact: RecordFactOutcome | None = None


def decide_seam_action(
    root: Path,
    *,
    record: IntegrationRecord | None = None,
    agent_active: bool = False,
    ledger_path: Path | None = None,
    retained: bool = False,
    target: str | None = None,
    remote: str | None = None,
) -> IntegrationAction | RecordFactOutcome:
    """Ask the owner what integration does next, then overlay the record facts.

    Args:
        root: The workspace root the decision is made for. It anchors
            the durable attempt ledger when ``ledger_path`` is not
            given.
        record: The durable integration record the caller already
            holds (``None`` when the caller holds none). The owner
            reconciles it with the observed view: a caller holding a
            record the observation does not see holds a stale view, and
            the owner fails closed to the repair rung.
        agent_active: Whether an agent phase is active in the
            worktree. Recorded on the observation and rendered into
            the owner's reason.
        ledger_path: Override for the attempt-ledger file path.
        retained: True when the caller holds a RETAINED integration
            obligation. A retained obligation with no durable record
            behind it has nothing the boundary can act on, so the seam
            fails closed to the typed
            :attr:`RecordFactOutcome.STALE_INTENT` verdict.
        target: The CONFIGURED integration target branch name. ``None``
            uses the director's default (``main``). A caller with a
            configured target must pass it so the observation is taken
            against the configured branch.
        remote: The CONFIGURED remote name. ``None`` uses the
            director's default (``origin``).

    Returns:
        The action the seam must act on: the owner's mutation-ladder
        answer, overlaid with the durable record facts. When a live
        durable claim cannot prove a next step, the return is a typed
        :class:`RecordFactOutcome` verdict instead of an
        :class:`IntegrationAction`; the caller routes it to the
        autonomous fallback ladder through
        :func:`~ralph.pipeline.integration_owner.ladder_rung_for_record_fact_outcome`.
        The overlay is applied against the LIVE observation, so a stale
        durable claim never changes the owner's answer.
    """
    verdict = decide_integration_verdict(
        root,
        record=record,
        agent_active=agent_active,
        ledger_path=ledger_path,
        target=target,
        remote=remote,
    )
    return apply_record_facts(
        verdict.action,
        record,
        retained=retained,
        observation=verdict.observation,
    )


def decide_planning_sync(
    root: Path,
    *,
    record: IntegrationRecord | None = None,
    agent_active: bool = False,
    ledger_path: Path | None = None,
    retained: bool = False,
    target: str | None = None,
    remote: str | None = None,
) -> PlanningSyncVerdict:
    """Ask the owner whether planning may start, and return the typed verdict.

    The decision is charged to the durable ledger exactly once, through
    :func:`decide_integration_verdict` — the same single charge path
    every other seam uses — so a planning hold that repeats the same
    observation escalates through the owner's budget instead of looping.
    The durable record facts are overlaid on the owner's action exactly
    as :func:`decide_seam_action` does; a stale durable claim never
    turns a current, healthy Git observation into a terminal block.

    The worktree verdict comes from the ONE classifier
    (:func:`classify_worktree`) on the same root, so the planning gate,
    the boundary and the resolution gate cannot disagree about the tree.
    ``may_plan`` is derived, not decided per seam: the branch is in
    sync (the feature contains the target tip) and the classifier
    allows dispatch.
    """
    verdict = decide_integration_verdict(
        root,
        record=record,
        agent_active=agent_active,
        ledger_path=ledger_path,
        target=target,
        remote=remote,
    )
    action = apply_record_facts(
        verdict.action,
        record,
        retained=retained,
        observation=verdict.observation,
    )
    worktree = classify_worktree(read_worktree_porcelain(root))
    in_sync = verdict.observation.target_vs_feature in (
        TargetVsFeature.EQUAL,
        TargetVsFeature.AHEAD,
    )
    # A fail-closed record fact holds planning and escalates through the
    # cleanup ladder: the verdict carries the typed outcome and the
    # escalation rung (the first rung preserves the work to backup
    # refs), and the coarse action is the rung's git-plumbing
    # projection. The ladder — never an operator hand-off — owns the
    # escalation.
    record_fact: RecordFactOutcome | None = None
    ladder_rung = verdict.ladder_rung
    if isinstance(action, RecordFactOutcome):
        record_fact = action
        ladder_rung = ladder_rung_for_record_fact_outcome(action)
        action = IntegrationAction.CONTINUE_GIT_OPERATION
    may_plan = in_sync and worktree.dispatch_allowed and record_fact is None
    if record_fact is not None:
        reason = (
            f"planning held: durable record facts cannot prove a next step "
            f"({record_fact.value}); preserving work to backup refs and escalating "
            f"through cleanup-ladder rung {ladder_rung_for_record_fact_outcome(record_fact).name.lower()}"
        )
    elif may_plan:
        reason = "planning may proceed: branch is in sync and the worktree gate allows dispatch"
    elif not in_sync:
        reason = (
            "planning held: branch does not contain the target tip "
            f"({verdict.observation.target_vs_feature.value}); integration will run first"
        )
    else:
        reason = f"planning held: {worktree.reason}"
    return PlanningSyncVerdict(
        action=action,
        observation=verdict.observation,
        worktree=worktree,
        dirt_shape=worktree.dirt_shape,
        in_sync=in_sync,
        may_plan=may_plan,
        reason=reason,
        ladder_rung=ladder_rung,
        record_fact=record_fact,
    )


def execute_planning_ladder_rung(
    root: Path,
    verdict: PlanningSyncVerdict,
    *,
    ledger: AttemptLedger,
    target: str | None = None,
    expected_target_sha: str | None = None,
    git: GitPlumbing | None = None,
    fs: FileQuarantine | None = None,
    env: EnvironmentProbe | None = None,
    clock: Clock | None = None,
    state_dir: Path | None = None,
) -> LadderStepResult | None:
    """Dispatch the cleanup-ladder rung the owner's verdict selected, once.

    This is the caller contract documented on
    :data:`ralph.pipeline.integration_owner.NEXT_ACTION_TO_LADDER_RUNG`:
    a verdict whose ``ladder_rung`` is not ``None`` executes that rung
    through :func:`ralph.pipeline.integration_ladder.run_ladder_rung`
    instead of acting on the coarse ``action`` alone. ``None`` rung
    means the decision is not a cleanup-ladder rung and no ladder
    executor runs — the function returns ``None`` and touches nothing.

    The rung executes with the repo-wide attempt ``ledger`` (every rung
    records its own outcome evidence into it before and after acting),
    the state dir and the injected clock/plumbing seams, so a boundary
    holding the verdict runs any rung with one call. The verdict's typed
    in-progress state reaches the dispatcher, and ``expected_target_sha``
    — the target sha observed at decision time — is forwarded so the
    fresh-worktree rung fails closed on a concurrent target move.

    The caller consumes the returned
    :class:`~ralph.pipeline.integration_ladder.LadderStepResult` and
    re-observes before deciding again: acting twice on one observation
    is what the durable ledger's escalation budget exists to prevent.

    Rung 6 (the hard reset) is deliberately NOT armed from this seam. A
    safe reset needs a blocking signature re-observed within the current
    tick plus the real feature tip and branch, and the planning verdict
    carries none of them; wiring placeholders would reset toward the
    target and strand committed feature work. The seam therefore passes
    no signature and the rung answers with the explicit
    :data:`~ralph.pipeline.integration_ladder.LAST_RESORT_DISABLED_NO_SIGNATURE`
    verdict, never touching the tree.
    """
    if verdict.ladder_rung is None:
        return None
    return run_ladder_rung(
        verdict.ladder_rung,
        root=root,
        ledger=ledger,
        git=git,
        fs=fs,
        env=env,
        clock=clock,
        state_dir=state_dir,
        target="main" if target is None else target,
        in_progress_kind=verdict.observation.in_progress_kind,
        expected_target_sha=expected_target_sha,
    )
