"""Dirty-tree decision-row tests for the integration decision table.

The decision table maps every dirt shape x in-progress operation x
target/remote relation to exactly one deterministic action
(``PRODUCT_CRITERIA.md``, "Open symptom: a dirty tree after planning"):

* Phase-owned blocking dirt -> ``COMMIT_VIA_COMMIT_PHASE`` (run the
  owning phase's commit first; never integrate around it, never wait on
  it).
* Residue / unowned dirt, and dirt of unknown attribution -> exactly
  ``SAVE_AND_RESTORE`` (save to a backup ref, restore the baseline,
  never discard, never a silent revert).
* Non-blocking shapes (``LINE_ENDING_ONLY`` — clean after refresh, and
  scratch ``UNTRACKED_ONLY``) never route to a cleanup action.
* An in-progress operation is resolved FIRST: no cleanup action is ever
  returned while an operation owns the tree.
* Under repeat pressure the answer climbs the autonomous fallback
  ladder one rung per repeat — a different, stronger action each time,
  saturating at the last-resort rung — and never re-runs the direct
  action, never waits, and never dispatches the resolver for dirt.

Everything is pure in-memory: no Git, no subprocess, no filesystem, no
clock, no sleep. This is the ``audit_test_policy`` contract.
"""

from __future__ import annotations

from collections import OrderedDict
from functools import cache
from itertools import product

import pytest

from ralph.pipeline.integration_decision import (
    CLEANUP_LADDER_ORDER,
    AttemptState,
    CleanupLadderRung,
    DecisionContext,
    DirtAttribution,
    EscalationRung,
    NextAction,
    cleanup_action_for,
    decide,
)
from ralph.pipeline.integration_observation import (
    DirtShape,
    HeadState,
    InProgressKind,
    InProgressOwnership,
    IntegrationRecordState,
    Observation,
    TargetVsFeature,
    TargetVsRemote,
    WorktreeState,
)

pytestmark = pytest.mark.timeout_seconds(30)

# ---------------------------------------------------------------------------
# The dirty-tree space.
# ---------------------------------------------------------------------------

_ALL_SHAPES: tuple[DirtShape, ...] = tuple(DirtShape)
_ALL_ATTRIBUTIONS: tuple[DirtAttribution, ...] = tuple(DirtAttribution)
_ALL_KINDS: tuple[InProgressKind, ...] = tuple(InProgressKind)
_ALL_OWNERSHIP: tuple[InProgressOwnership, ...] = tuple(InProgressOwnership)
_ALL_TARGET_VS_FEATURE: tuple[TargetVsFeature, ...] = tuple(TargetVsFeature)
_ALL_TARGET_VS_REMOTE: tuple[TargetVsRemote, ...] = tuple(TargetVsRemote)

#: Every shape the table treats as blocking tracked dirt.
_BLOCKING_SHAPES: frozenset[DirtShape] = frozenset(
    {
        DirtShape.UNSTAGED_MODIFICATION,
        DirtShape.STAGED_ONLY,
        DirtShape.STAGED_AND_UNSTAGED,
        DirtShape.MODE_ONLY,
        DirtShape.INTENT_TO_ADD,
        DirtShape.TYPE_CHANGE,
        DirtShape.DELETED,
        DirtShape.RENAMED_OR_COPIED,
        DirtShape.UNTRACKED_BLOCKING,
    }
)

#: Deterministic ordering of the blocking shapes for parametrized ids.
_BLOCKING_SHAPES_SORTED: tuple[DirtShape, ...] = tuple(
    sorted(_BLOCKING_SHAPES, key=lambda shape: shape.value)
)

#: Every cleanup-ladder action: the direct actions plus the rung
#: destinations. No other branch of the table may return these.
_CLEANUP_ACTIONS: frozenset[NextAction] = frozenset(
    {
        NextAction.COMMIT_VIA_COMMIT_PHASE,
        NextAction.SAVE_AND_RESTORE,
        NextAction.PRESERVE_VIA_TEMP_INDEX,
        NextAction.QUARANTINE_AND_RESTORE,
        NextAction.REBUILD_IN_FRESH_WORKTREE,
        NextAction.ADAPT_ENVIRONMENT_AND_RETRY,
        NextAction.LAST_RESORT_RECOVERY,
    }
)


def _observation(
    *,
    target_vs_feature: TargetVsFeature,
    target_vs_remote: TargetVsRemote,
    worktree: WorktreeState,
    in_progress_kind: InProgressKind,
    in_progress_ownership: InProgressOwnership,
    record: IntegrationRecordState = IntegrationRecordState.NONE,
    head: HeadState = HeadState.ATTACHED,
) -> Observation:
    """Build one observation over the dimensions the dirt rows consult."""
    return Observation(
        target_vs_feature=target_vs_feature,
        target_vs_remote=target_vs_remote,
        worktree=worktree,
        head=head,
        in_progress_kind=in_progress_kind,
        in_progress_ownership=in_progress_ownership,
        record=record,
        agent_active=False,
    )


def _worktree_for_shape(shape: DirtShape) -> WorktreeState:
    """The coarse state each shape refines (for building observations)."""
    if shape in (DirtShape.NONE, DirtShape.LINE_ENDING_ONLY):
        return WorktreeState.TRACKED_CHANGES
    if shape is DirtShape.UNMERGED:
        return WorktreeState.UNMERGED
    if shape in (DirtShape.UNTRACKED_ONLY, DirtShape.UNTRACKED_BLOCKING):
        return WorktreeState.UNTRACKED
    return WorktreeState.TRACKED_CHANGES


def _context(
    observation: Observation,
    shape: DirtShape,
    attribution: DirtAttribution,
    attempts: AttemptState | None = None,
) -> DecisionContext:
    return DecisionContext(
        observation=observation,
        attempts=attempts if attempts is not None else AttemptState(),
        dirt_shape=shape,
        dirt_attribution=attribution,
    )


# ---------------------------------------------------------------------------
# The ladder contract.
# ---------------------------------------------------------------------------


def test_cleanup_ladder_maps_every_rung_to_exactly_one_action() -> None:
    """The fallback ladder is total and preservation-first in name."""
    assert len(CLEANUP_LADDER_ORDER) == 6
    assert len(set(CLEANUP_LADDER_ORDER)) == 6
    mapped = {rung: cleanup_action_for(rung) for rung in CleanupLadderRung}
    assert set(mapped) == set(CleanupLadderRung)
    assert mapped[CleanupLadderRung.NORMAL_CLEANUP] is NextAction.SAVE_AND_RESTORE
    forbidden = ("discard", "drop", "reset", "abort", "wipe", "delete", "force", "throw")
    offenders = [
        action.name
        for action in mapped.values()
        if any(token in action.name.lower() for token in forbidden)
    ]
    assert offenders == [], f"discard-class ladder actions: {offenders}"


def test_every_new_cleanup_action_is_chargeable_and_projected() -> None:
    """The ladder actions participate in the durable budget and the projection."""
    from ralph.pipeline.integration_owner import (
        NEXT_ACTION_TO_INTEGRATION_ACTION,
        is_chargeable,
    )

    for action in _CLEANUP_ACTIONS:
        assert is_chargeable(action), f"{action.name} must spend durable budget"
        assert action in NEXT_ACTION_TO_INTEGRATION_ACTION


# ---------------------------------------------------------------------------
# Direct actions: shape x attribution.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shape", _BLOCKING_SHAPES_SORTED)
@pytest.mark.parametrize(
    "target_vs_feature",
    (TargetVsFeature.BEHIND, TargetVsFeature.DIVERGED),
)
@pytest.mark.parametrize(
    "target_vs_remote",
    (TargetVsRemote.EQUAL, TargetVsRemote.BEHIND, TargetVsRemote.UNREACHABLE),
)
def test_phase_owned_dirt_commits_through_the_commit_phase(
    shape: DirtShape,
    target_vs_feature: TargetVsFeature,
    target_vs_remote: TargetVsRemote,
) -> None:
    """Phase-owned dirt maps to COMMIT_VIA_COMMIT_PHASE, whatever the target."""
    observation = _observation(
        target_vs_feature=target_vs_feature,
        target_vs_remote=target_vs_remote,
        worktree=_worktree_for_shape(shape),
        in_progress_kind=InProgressKind.NONE,
        in_progress_ownership=InProgressOwnership.OWNED,
    )

    action = decide(
        _context(observation, shape, DirtAttribution.PHASE_OWNED),
    )

    assert action is NextAction.COMMIT_VIA_COMMIT_PHASE


@pytest.mark.parametrize("shape", _BLOCKING_SHAPES_SORTED)
@pytest.mark.parametrize(
    "attribution",
    (DirtAttribution.UNOWNED, DirtAttribution.UNKNOWN),
)
def test_residue_and_ambiguous_dirt_saves_and_restores(
    shape: DirtShape,
    attribution: DirtAttribution,
) -> None:
    """Residue (and ambiguous attribution) maps to SAVE_AND_RESTORE."""
    observation = _observation(
        target_vs_feature=TargetVsFeature.BEHIND,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=_worktree_for_shape(shape),
        in_progress_kind=InProgressKind.NONE,
        in_progress_ownership=InProgressOwnership.OWNED,
    )

    action = decide(_context(observation, shape, attribution))

    assert action is NextAction.SAVE_AND_RESTORE


@pytest.mark.parametrize(
    ("shape", "expected"),
    (
        (DirtShape.LINE_ENDING_ONLY, "clean"),
        (DirtShape.NONE, "legacy"),
        (DirtShape.UNTRACKED_ONLY, "legacy"),
    ),
)
def test_non_blocking_shapes_never_route_to_cleanup(
    shape: DirtShape,
    expected: str,
) -> None:
    """Clean-after-refresh behaves like clean; legacy shapes keep COMMIT."""
    observation = _observation(
        target_vs_feature=TargetVsFeature.BEHIND,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=_worktree_for_shape(shape),
        in_progress_kind=InProgressKind.NONE,
        in_progress_ownership=InProgressOwnership.OWNED,
    )

    shaped = decide(_context(observation, shape, DirtAttribution.UNOWNED))

    if expected == "clean":
        clean_observation = Observation(
            target_vs_feature=observation.target_vs_feature,
            target_vs_remote=observation.target_vs_remote,
            worktree=WorktreeState.CLEAN,
            head=observation.head,
            in_progress_kind=observation.in_progress_kind,
            in_progress_ownership=observation.in_progress_ownership,
            record=observation.record,
            agent_active=observation.agent_active,
        )
        assert shaped is decide(DecisionContext(observation=clean_observation))
    else:
        assert shaped is NextAction.COMMIT


# ---------------------------------------------------------------------------
# The ordering rule: an operation is resolved first.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shape", _BLOCKING_SHAPES_SORTED)
@pytest.mark.parametrize(
    "kind",
    (InProgressKind.REBASE, InProgressKind.MERGE, InProgressKind.CHERRY_PICK),
)
def test_owned_operation_outranks_dirt_cleanup(shape: DirtShape, kind: InProgressKind) -> None:
    """Dirt coexisting with an owned operation is the operation's state."""
    observation = _observation(
        target_vs_feature=TargetVsFeature.BEHIND,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=_worktree_for_shape(shape),
        in_progress_kind=kind,
        in_progress_ownership=InProgressOwnership.OWNED,
    )

    action = decide(_context(observation, shape, DirtAttribution.UNOWNED))

    assert action is NextAction.RESUME_OWNED_OPERATION


@pytest.mark.parametrize("shape", _BLOCKING_SHAPES_SORTED)
@pytest.mark.parametrize(
    "kind",
    (InProgressKind.REBASE, InProgressKind.MERGE, InProgressKind.CHERRY_PICK),
)
def test_foreign_operation_is_a_real_conflict_not_dirt(
    shape: DirtShape,
    kind: InProgressKind,
) -> None:
    """A foreign operation routes to the resolver rung, never cleanup."""
    observation = _observation(
        target_vs_feature=TargetVsFeature.BEHIND,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=_worktree_for_shape(shape),
        in_progress_kind=kind,
        in_progress_ownership=InProgressOwnership.FOREIGN,
    )

    action = decide(_context(observation, shape, DirtAttribution.UNOWNED))

    assert action is NextAction.DISPATCH_RESOLVER


def test_unmerged_worktree_outranks_dirt_shapes() -> None:
    """Unmerged paths are a real conflict whatever the shape context says."""
    observation = _observation(
        target_vs_feature=TargetVsFeature.BEHIND,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=WorktreeState.UNMERGED,
        in_progress_kind=InProgressKind.NONE,
        in_progress_ownership=InProgressOwnership.OWNED,
    )

    action = decide(
        _context(observation, DirtShape.UNMERGED, DirtAttribution.UNOWNED),
    )

    assert action is NextAction.DISPATCH_RESOLVER


def test_unknown_observation_never_uses_the_cleanup_rows() -> None:
    """UNKNOWN precedes the dirt rows: back off, never clean up blindly."""
    observation = _observation(
        target_vs_feature=TargetVsFeature.UNKNOWN,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=WorktreeState.TRACKED_CHANGES,
        in_progress_kind=InProgressKind.NONE,
        in_progress_ownership=InProgressOwnership.OWNED,
    )

    action = decide(
        _context(observation, DirtShape.STAGED_ONLY, DirtAttribution.UNOWNED),
    )

    assert action is NextAction.WAIT_FOR_ENVIRONMENT


def test_repairable_record_precedes_dirt_cleanup() -> None:
    """A stale record is repaired before any dirt cleanup runs."""
    observation = _observation(
        target_vs_feature=TargetVsFeature.BEHIND,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=WorktreeState.TRACKED_CHANGES,
        in_progress_kind=InProgressKind.NONE,
        in_progress_ownership=InProgressOwnership.OWNED,
        record=IntegrationRecordState.STALE,
    )

    action = decide(
        _context(observation, DirtShape.STAGED_ONLY, DirtAttribution.UNOWNED),
    )

    assert action is NextAction.REPAIR


# ---------------------------------------------------------------------------
# The escalation ladder: each repeat is a stronger action.
# ---------------------------------------------------------------------------


def _pressure_trace(attribution: DirtAttribution) -> list[NextAction]:
    """Simulate the boundary's charged cycles against one dirty signature.

    Each cycle decides, then folds the charge back through
    :meth:`AttemptState.with_attempt` — the same bump rules the durable
    ledger mirrors — so the trace is exactly what a live boundary with a
    restart-proof ledger observes.
    """
    observation = _observation(
        target_vs_feature=TargetVsFeature.BEHIND,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=WorktreeState.TRACKED_CHANGES,
        in_progress_kind=InProgressKind.NONE,
        in_progress_ownership=InProgressOwnership.OWNED,
    )
    shape = DirtShape.STAGED_ONLY
    attempts = AttemptState()
    trace = []
    for _ in range(10):
        action = decide(_context(observation, shape, attribution, attempts))
        trace.append(action)
        attempts = attempts.with_attempt(observation, action, is_unknown=False)
    return trace


def test_residue_repeats_climb_the_fallback_ladder() -> None:
    """Residue escalates rung by rung, saturating at the last resort."""
    trace = _pressure_trace(DirtAttribution.UNOWNED)

    assert trace[:2] == [NextAction.SAVE_AND_RESTORE, NextAction.SAVE_AND_RESTORE]
    assert trace[2] is NextAction.PRESERVE_VIA_TEMP_INDEX
    assert trace[3] is NextAction.QUARANTINE_AND_RESTORE
    assert trace[4] is NextAction.REBUILD_IN_FRESH_WORKTREE
    assert trace[5] is NextAction.ADAPT_ENVIRONMENT_AND_RETRY
    assert trace[6] is NextAction.LAST_RESORT_RECOVERY
    assert trace[7:] == [NextAction.LAST_RESORT_RECOVERY] * 3


def test_phase_owned_repeats_climb_the_same_ladder() -> None:
    """Phase-owned dirt that repeats (the phase never commits) escalates too."""
    trace = _pressure_trace(DirtAttribution.PHASE_OWNED)

    assert trace[:2] == [
        NextAction.COMMIT_VIA_COMMIT_PHASE,
        NextAction.COMMIT_VIA_COMMIT_PHASE,
    ]
    assert trace[2] is NextAction.PRESERVE_VIA_TEMP_INDEX
    assert trace[6] is NextAction.LAST_RESORT_RECOVERY


def test_cleanup_ladder_never_dispatches_the_resolver_for_dirt() -> None:
    """Dirt is a condition no agent can change — at every depth."""
    for attribution in (DirtAttribution.PHASE_OWNED, DirtAttribution.UNOWNED):
        trace = _pressure_trace(attribution)
        assert NextAction.DISPATCH_RESOLVER not in trace
        assert NextAction.WAIT_FOR_ENVIRONMENT not in trace


# ---------------------------------------------------------------------------
# Totality over the dirty-tree space.
# ---------------------------------------------------------------------------


@cache
def _dirty_space() -> tuple[tuple[Observation, DirtShape, DirtAttribution], ...]:
    """Materialise the dirt refinement over the operation/target space.

    14 shapes x 3 attributions x 4 operation kinds x 3 ownerships x 5
    target/feature relations x 6 target/remote relations = 15,120
    (context, observation) pairs: the complete dirt-shape decision space
    at the fresh attempt state.
    """
    return tuple(
        (
            _observation(
                target_vs_feature=target_vs_feature,
                target_vs_remote=target_vs_remote,
                worktree=_worktree_for_shape(shape),
                in_progress_kind=kind,
                in_progress_ownership=ownership,
            ),
            shape,
            attribution,
        )
        for shape, attribution, kind, ownership, target_vs_feature, target_vs_remote in product(
            _ALL_SHAPES,
            _ALL_ATTRIBUTIONS,
            _ALL_KINDS,
            _ALL_OWNERSHIP,
            _ALL_TARGET_VS_FEATURE,
            _ALL_TARGET_VS_REMOTE,
        )
    )


def test_every_dirt_shape_state_has_exactly_one_defined_action() -> None:
    """The dirt rows are total: no (shape, attribution, operation, target) falls
    through, and no reachable row answers dirt with a resolver or a discard."""
    undefined: list[str] = []
    for observation, shape, attribution in _dirty_space():
        context = _context(observation, shape, attribution)
        action = decide(context)
        if not isinstance(action, NextAction):
            undefined.append(f"{shape.value}/{attribution.value}: {action!r}")
            continue
        quiescent_dirt = (
            observation.in_progress_kind is InProgressKind.NONE
            and not observation.has_unknown()
            and observation.worktree in (WorktreeState.TRACKED_CHANGES, WorktreeState.STAGED)
            and shape in _BLOCKING_SHAPES
            and observation.record is IntegrationRecordState.NONE
            and observation.head is HeadState.ATTACHED
        )
        if quiescent_dirt and action is NextAction.DISPATCH_RESOLVER:
            undefined.append(
                f"{shape.value}/{attribution.value}: resolver dispatched for dirt",
            )
    assert undefined == [], "\n".join(undefined[:10])


def test_cleanup_actions_reachable_only_through_repeat_pressure() -> None:
    """At the fresh attempt state no ladder rung above NORMAL_CLEANUP appears."""
    observation = _observation(
        target_vs_feature=TargetVsFeature.BEHIND,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=WorktreeState.TRACKED_CHANGES,
        in_progress_kind=InProgressKind.NONE,
        in_progress_ownership=InProgressOwnership.OWNED,
    )
    rung_actions = {cleanup_action_for(rung) for rung in CLEANUP_LADDER_ORDER[1:]}
    for shape in _BLOCKING_SHAPES:
        for attribution in DirtAttribution:
            action = decide(_context(observation, shape, attribution))
            assert action not in rung_actions, (
                f"{shape.value}/{attribution.value} escalated without repeats: {action.name}"
            )


def test_dirt_signature_repeat_counts_drive_the_climb() -> None:
    """The climb is a function of the durable (signature, action) counts."""
    observation = _observation(
        target_vs_feature=TargetVsFeature.BEHIND,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=WorktreeState.STAGED,
        in_progress_kind=InProgressKind.NONE,
        in_progress_ownership=InProgressOwnership.OWNED,
    )
    signature = AttemptState().signature(observation)
    pressured = AttemptState(
        rung=EscalationRung.REPAIR,
        repeat_counts=OrderedDict(
            {
                (signature, NextAction.SAVE_AND_RESTORE.value): 5,
            }
        ),
    )

    action = decide(
        _context(
            observation,
            DirtShape.STAGED_ONLY,
            DirtAttribution.UNOWNED,
            pressured,
        ),
    )

    # Five charged attempts at this signature: the first two answered
    # the direct action, so the climb stands at rung 5.
    assert action is NextAction.ADAPT_ENVIRONMENT_AND_RETRY
