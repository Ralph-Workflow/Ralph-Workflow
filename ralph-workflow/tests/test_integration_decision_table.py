"""Table-driven and property-based tests for the integration decision table.

The decision table for Ralph's auto-integration subsystem must be total
and safe: every combination of the seven :class:`Observation` dimensions
maps to exactly one :class:`NextAction`, and no combination yields a
discard-class action or a resolver dispatch on an unknown reading.

These tests enumerate the complete finite state space (77,760
observations crossed with each escalation rung) and assert the
contract directly. They are pure in-memory: no subprocess, no
filesystem, no clock, no ``time.sleep``. This is a project audit
contract under ``ralph.testing.audit_test_policy``.
"""

from __future__ import annotations

from functools import cache

import pytest

from ralph.pipeline.integration_decision import (
    LADDER_ORDER,
    AttemptState,
    DecisionContext,
    EscalationRung,
    NextAction,
    decide,
)
from ralph.pipeline.integration_observation import (
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
# Exhaustive observation builder.
# ---------------------------------------------------------------------------

_ALL_TARGET_VS_FEATURE: tuple[TargetVsFeature, ...] = tuple(TargetVsFeature)
_ALL_TARGET_VS_REMOTE: tuple[TargetVsRemote, ...] = tuple(TargetVsRemote)
_ALL_WORKTREE: tuple[WorktreeState, ...] = tuple(WorktreeState)
_ALL_HEAD: tuple[HeadState, ...] = tuple(HeadState)
_ALL_KIND: tuple[InProgressKind, ...] = tuple(InProgressKind)
_ALL_OWNERSHIP: tuple[InProgressOwnership, ...] = tuple(InProgressOwnership)
_ALL_RECORD: tuple[IntegrationRecordState, ...] = tuple(IntegrationRecordState)


@cache
def _build_observations() -> tuple[Observation, ...]:
    """Materialise every combination of the seven dimensions."""
    return tuple(
        Observation(
            target_vs_feature=target_vs_feature,
            target_vs_remote=target_vs_remote,
            worktree=worktree,
            head=head,
            in_progress_kind=in_progress_kind,
            in_progress_ownership=in_progress_ownership,
            record=record,
            agent_active=agent_active,
        )
        for target_vs_feature in _ALL_TARGET_VS_FEATURE
        for target_vs_remote in _ALL_TARGET_VS_REMOTE
        for worktree in _ALL_WORKTREE
        for head in _ALL_HEAD
        for in_progress_kind in _ALL_KIND
        for in_progress_ownership in _ALL_OWNERSHIP
        for record in _ALL_RECORD
        for agent_active in (True, False)
    )


@cache
def _build_unknown_observations() -> tuple[Observation, ...]:
    """Materialise every observation where at least one dimension is UNKNOWN.

    Resolver dispatch is only ever admitted with ``REAL_CONFLICT`` evidence,
    so the unknown rule must be asserted across every possible unknown
    combination.
    """
    return tuple(obs for obs in _build_observations() if obs.has_unknown())


# ---------------------------------------------------------------------------
# Observation contract.
# ---------------------------------------------------------------------------


def test_observation_has_unknown_is_consistent_with_enum_membership() -> None:
    """``Observation.has_unknown`` is True iff an enum dimension caps UNKNOWN."""
    obs = Observation(
        target_vs_feature=TargetVsFeature.EQUAL,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=WorktreeState.CLEAN,
        head=HeadState.ATTACHED,
        in_progress_kind=InProgressKind.NONE,
        in_progress_ownership=InProgressOwnership.UNKNOWN,
        record=IntegrationRecordState.CONSISTENT,
        agent_active=False,
    )
    assert obs.has_unknown() is True

    obs_clean = Observation(
        target_vs_feature=TargetVsFeature.EQUAL,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=WorktreeState.CLEAN,
        head=HeadState.ATTACHED,
        in_progress_kind=InProgressKind.NONE,
        in_progress_ownership=InProgressOwnership.OWNED,
        record=IntegrationRecordState.CONSISTENT,
        agent_active=False,
    )
    assert obs_clean.has_unknown() is False


# ---------------------------------------------------------------------------
# Total mapping: rule 7.
# ---------------------------------------------------------------------------


_LADDER_ATTEMPTS: tuple[AttemptState, ...] = tuple(AttemptState(rung=rung) for rung in LADDER_ORDER)


#: Members the default (shape-free) context must reach across the
#: exhaustive observation space. The dirty-tree cleanup members are
#: deliberately excluded: they require an explicit DirtShape refinement
#: in the context, and their reachability across the dirt-shape space is
#: proven by ``tests/test_integration_dirt_decisions.py``.
_DEFAULT_CONTEXT_MEMBERS: frozenset[NextAction] = frozenset(
    set(NextAction)
    - {
        NextAction.COMMIT_VIA_COMMIT_PHASE,
        NextAction.SAVE_AND_RESTORE,
        NextAction.PRESERVE_VIA_TEMP_INDEX,
        NextAction.QUARANTINE_AND_RESTORE,
        NextAction.REBUILD_IN_FRESH_WORKTREE,
        NextAction.ADAPT_ENVIRONMENT_AND_RETRY,
        NextAction.LAST_RESORT_RECOVERY,
    }
)


def test_decide_total_mapping_covers_every_state_and_reaches_every_member() -> None:
    """Every observation crossed with every rung yields one NextAction; the full
    enum is reachable (rule 7 completeness).

    The default (shape-free) context must reach every member that does
    not require a dirt-shape refinement; the cleanup-ladder members are
    reachable only through a shaped context, which the exhaustive
    dirt-shape suite walks.
    """
    observations = _build_observations()
    assert len(observations) == 77760
    reached: set[NextAction] = set()
    for observation in observations:
        for attempts in _LADDER_ATTEMPTS:
            ctx = DecisionContext(
                observation=observation,
                attempts=attempts,
            )
            action = decide(ctx)
            assert isinstance(action, NextAction), (
                f"non-action returned for {observation} at rung {attempts.rung}: {action!r}"
            )
            reached.add(action)
    assert reached == _DEFAULT_CONTEXT_MEMBERS, (
        f"unreachable members: {_DEFAULT_CONTEXT_MEMBERS - reached}; "
        f"unexpected members: {reached - _DEFAULT_CONTEXT_MEMBERS}"
    )


# ---------------------------------------------------------------------------
# Rule 1: UNKNOWN never blocks and never dispatches an agent.
# ---------------------------------------------------------------------------


def test_decide_unknown_observation_never_dispatches_resolver() -> None:
    """Rule 1: no unknown reading ever routes to ``DISPATCH_RESOLVER``."""
    unknown_observations = _build_unknown_observations()
    assert unknown_observations, "expected at least one unknown observation"
    for observation in unknown_observations:
        for attempts in _LADDER_ATTEMPTS:
            ctx = DecisionContext(observation=observation, attempts=attempts)
            assert decide(ctx) is not NextAction.DISPATCH_RESOLVER, (
                f"resolver dispatched on unknown reading: {observation} @ {attempts.rung}"
            )


def test_decide_unknown_observation_yields_wait_or_pending() -> None:
    """Rule 1/2: unknown observations only yield ``WAIT_FOR_ENVIRONMENT`` or
    ``CONTINUE_WITH_INTEGRATION_PENDING``.
    """
    for observation in _build_unknown_observations():
        for attempts in (
            AttemptState(unknown_attempts=0, unknown_budget=5),
            AttemptState(unknown_attempts=5, unknown_budget=5),
        ):
            ctx = DecisionContext(observation=observation, attempts=attempts)
            action = decide(ctx)
            assert action in (
                NextAction.WAIT_FOR_ENVIRONMENT,
                NextAction.CONTINUE_WITH_INTEGRATION_PENDING,
            ), f"unknown observation yielded non-wait action {action}"


# ---------------------------------------------------------------------------
# Rule 2: WAIT_FOR_ENVIRONMENT budget exhaustion.
# ---------------------------------------------------------------------------


def test_decide_wait_for_environment_holds_until_budget_then_continues() -> None:
    """Rule 2: WAIT_FOR_ENVIRONMENT holds until the unknown budget is spent."""
    obs = Observation(
        target_vs_feature=TargetVsFeature.UNKNOWN,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=WorktreeState.CLEAN,
        head=HeadState.ATTACHED,
        in_progress_kind=InProgressKind.NONE,
        in_progress_ownership=InProgressOwnership.OWNED,
        record=IntegrationRecordState.CONSISTENT,
        agent_active=False,
    )
    attempts = AttemptState(unknown_attempts=0, unknown_budget=3)
    assert decide(DecisionContext(observation=obs, attempts=attempts)) is (
        NextAction.WAIT_FOR_ENVIRONMENT
    )
    attempts_one = attempts.with_attempt(obs, NextAction.WAIT_FOR_ENVIRONMENT, is_unknown=True)
    assert decide(DecisionContext(observation=obs, attempts=attempts_one)) is (
        NextAction.WAIT_FOR_ENVIRONMENT
    )
    attempts_two = attempts_one.with_attempt(obs, NextAction.WAIT_FOR_ENVIRONMENT, is_unknown=True)
    assert attempts_two.unknown_attempts == 2
    assert decide(DecisionContext(observation=obs, attempts=attempts_two)) is (
        NextAction.WAIT_FOR_ENVIRONMENT
    )
    attempts_spent = attempts_two.with_attempt(
        obs, NextAction.WAIT_FOR_ENVIRONMENT, is_unknown=True
    )
    assert attempts_spent.unknown_budget_exhausted() is True
    assert decide(DecisionContext(observation=obs, attempts=attempts_spent)) is (
        NextAction.CONTINUE_WITH_INTEGRATION_PENDING
    )


# ---------------------------------------------------------------------------
# Rule 3: Landed work release short-circuit.
# ---------------------------------------------------------------------------


def test_decide_landed_work_short_circuits_to_land() -> None:
    """Rule 3: clean worktree + feature contained yields LAND at every rung."""
    for target_vs_feature in (TargetVsFeature.EQUAL, TargetVsFeature.AHEAD):
        obs = Observation(
            target_vs_feature=target_vs_feature,
            target_vs_remote=TargetVsRemote.EQUAL,
            worktree=WorktreeState.CLEAN,
            head=HeadState.ATTACHED,
            in_progress_kind=InProgressKind.NONE,
            in_progress_ownership=InProgressOwnership.OWNED,
            record=IntegrationRecordState.CONSISTENT,
            agent_active=False,
        )
        for rung in LADDER_ORDER:
            ctx = DecisionContext(observation=obs, attempts=AttemptState(rung=rung))
            assert decide(ctx) is NextAction.LAND, (
                f"contained feature on {target_vs_feature} @ {rung} did not land"
            )


def test_decide_contained_feature_never_dispatches_resolver() -> None:
    """Rule 3 corollary: contained feature never yields ``DISPATCH_RESOLVER``."""
    for target in (TargetVsFeature.EQUAL, TargetVsFeature.AHEAD):
        for worktree in WorktreeState:
            if worktree is WorktreeState.UNMERGED:
                continue
            obs = Observation(
                target_vs_feature=target,
                target_vs_remote=TargetVsRemote.EQUAL,
                worktree=worktree,
                head=HeadState.ATTACHED,
                in_progress_kind=InProgressKind.NONE,
                in_progress_ownership=InProgressOwnership.OWNED,
                record=IntegrationRecordState.CONSISTENT,
                agent_active=False,
            )
            for rung in LADDER_ORDER:
                ctx = DecisionContext(observation=obs, attempts=AttemptState(rung=rung))
                assert decide(ctx) is not NextAction.DISPATCH_RESOLVER, (
                    f"contained feature on {target} {worktree} @ {rung} falsely resolves"
                )


# ---------------------------------------------------------------------------
# Rule 4: REAL_CONFLICT routes to DISPATCH_RESOLVER.
# ---------------------------------------------------------------------------


def test_decide_real_conflict_uses_dispatch_resolver() -> None:
    """Rule 4: ``REAL_CONFLICT`` (unmerged worktree or foreign op) routes to the resolver."""
    unmerged = Observation(
        target_vs_feature=TargetVsFeature.BEHIND,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=WorktreeState.UNMERGED,
        head=HeadState.ATTACHED,
        in_progress_kind=InProgressKind.NONE,
        in_progress_ownership=InProgressOwnership.OWNED,
        record=IntegrationRecordState.CONSISTENT,
        agent_active=False,
    )
    for rung in LADDER_ORDER:
        ctx = DecisionContext(observation=unmerged, attempts=AttemptState(rung=rung))
        assert decide(ctx) is NextAction.DISPATCH_RESOLVER, f"unmerged tree should resolve @ {rung}"

    for kind in (
        InProgressKind.REBASE,
        InProgressKind.MERGE,
        InProgressKind.CHERRY_PICK,
    ):
        foreign = Observation(
            target_vs_feature=TargetVsFeature.BEHIND,
            target_vs_remote=TargetVsRemote.EQUAL,
            worktree=WorktreeState.CLEAN,
            head=HeadState.ATTACHED,
            in_progress_kind=kind,
            in_progress_ownership=InProgressOwnership.FOREIGN,
            record=IntegrationRecordState.CONSISTENT,
            agent_active=False,
        )
        for rung in LADDER_ORDER:
            ctx = DecisionContext(observation=foreign, attempts=AttemptState(rung=rung))
            assert decide(ctx) is NextAction.DISPATCH_RESOLVER, (
                f"foreign {kind} @ {rung} should resolve"
            )


# ---------------------------------------------------------------------------
# Rule 5: owned in-progress ops route to RESUME_OWNED_OPERATION.
# ---------------------------------------------------------------------------


def test_decide_owned_in_progress_resumes_owned_operation() -> None:
    """Rule 5: owned in-progress ops yield RESUME_OWNED_OPERATION at every rung."""
    for kind in (
        InProgressKind.REBASE,
        InProgressKind.MERGE,
        InProgressKind.CHERRY_PICK,
    ):
        obs = Observation(
            target_vs_feature=TargetVsFeature.BEHIND,
            target_vs_remote=TargetVsRemote.EQUAL,
            worktree=WorktreeState.CLEAN,
            head=HeadState.ATTACHED,
            in_progress_kind=kind,
            in_progress_ownership=InProgressOwnership.OWNED,
            record=IntegrationRecordState.CONSISTENT,
            agent_active=False,
        )
        for rung in LADDER_ORDER:
            ctx = DecisionContext(observation=obs, attempts=AttemptState(rung=rung))
            assert decide(ctx) is NextAction.RESUME_OWNED_OPERATION, (
                f"owned {kind} @ {rung} should resume"
            )


# ---------------------------------------------------------------------------
# Rule 6: escalation ladder advances on repeat.
# ---------------------------------------------------------------------------


def test_decide_ladder_advances_after_signature_repeats() -> None:
    """Rule 6: advancing one rung per ``(signature, action)`` repeat threshold.

    Uses a ``HEAD=DETACHED`` observation so the action routes through the
    ladder (rule 7 / rule 8 — clean worktree + detached HEAD is repairable;
    REPAIR is the ladder's rung 0, REBUILD_ON_TARGET the rung 1, and
    CONTINUE_WITH_INTEGRATION_PENDING the terminal rung).
    """
    obs = Observation(
        target_vs_feature=TargetVsFeature.BEHIND,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=WorktreeState.CLEAN,
        head=HeadState.DETACHED,
        in_progress_kind=InProgressKind.NONE,
        in_progress_ownership=InProgressOwnership.OWNED,
        record=IntegrationRecordState.CONSISTENT,
        agent_active=False,
    )
    base = AttemptState(rung=EscalationRung.REPAIR, repeat_threshold=2)
    assert base.rung is EscalationRung.REPAIR
    assert decide(DecisionContext(observation=obs, attempts=base)) is NextAction.REPAIR
    first = base.with_attempt(obs, NextAction.REPAIR, is_unknown=False)
    assert first.rung is EscalationRung.REPAIR
    assert decide(DecisionContext(observation=obs, attempts=first)) is NextAction.REPAIR
    second = first.with_attempt(obs, NextAction.REPAIR, is_unknown=False)
    assert second.rung is EscalationRung.DISPATCH_RESOLVER
    assert decide(DecisionContext(observation=obs, attempts=second)) is (
        NextAction.DISPATCH_RESOLVER
    )
    third = second.with_attempt(obs, NextAction.DISPATCH_RESOLVER, is_unknown=False)
    assert third.rung is EscalationRung.DISPATCH_RESOLVER
    fourth = third.with_attempt(obs, NextAction.DISPATCH_RESOLVER, is_unknown=False)
    assert fourth.rung is EscalationRung.REBUILD_ON_TARGET
    assert decide(DecisionContext(observation=obs, attempts=fourth)) is (
        NextAction.REBUILD_ON_TARGET
    )
    fifth = fourth.with_attempt(obs, NextAction.REBUILD_ON_TARGET, is_unknown=False)
    assert fifth.rung is EscalationRung.REBUILD_ON_TARGET
    sixth = fifth.with_attempt(obs, NextAction.REBUILD_ON_TARGET, is_unknown=False)
    assert sixth.rung is EscalationRung.CONTINUE_WITH_INTEGRATION_PENDING
    assert decide(DecisionContext(observation=obs, attempts=sixth)) is (
        NextAction.CONTINUE_WITH_INTEGRATION_PENDING
    )
    terminal = sixth.with_attempt(
        obs, NextAction.CONTINUE_WITH_INTEGRATION_PENDING, is_unknown=False
    )
    assert terminal.rung is EscalationRung.CONTINUE_WITH_INTEGRATION_PENDING
    over_terminal = terminal.with_attempt(
        obs, NextAction.CONTINUE_WITH_INTEGRATION_PENDING, is_unknown=False
    )
    assert over_terminal.rung is EscalationRung.CONTINUE_WITH_INTEGRATION_PENDING


def test_decide_ladder_reaches_resolver_rung_with_real_conflict_signature() -> None:
    """A repeated same-signature signature on the resolver rung dispatches resolver
    only through the ladder, never via an unknown reading."""
    obs = Observation(
        target_vs_feature=TargetVsFeature.BEHIND,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=WorktreeState.CLEAN,
        head=HeadState.ATTACHED,
        in_progress_kind=InProgressKind.NONE,
        in_progress_ownership=InProgressOwnership.OWNED,
        record=IntegrationRecordState.STALE,
        agent_active=False,
    )
    ctx = DecisionContext(
        observation=obs,
        attempts=AttemptState(rung=EscalationRung.DISPATCH_RESOLVER),
    )
    assert decide(ctx) is NextAction.DISPATCH_RESOLVER


def test_decide_ladder_stays_put_when_action_changes() -> None:
    """Different action at the same signature does not count as a repeat."""
    obs = Observation(
        target_vs_feature=TargetVsFeature.BEHIND,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=WorktreeState.CLEAN,
        head=HeadState.DETACHED,
        in_progress_kind=InProgressKind.NONE,
        in_progress_ownership=InProgressOwnership.OWNED,
        record=IntegrationRecordState.CONSISTENT,
        agent_active=False,
    )
    base = AttemptState(rung=EscalationRung.REPAIR, repeat_threshold=2)
    bumped_repair = base.with_attempt(obs, NextAction.REPAIR, is_unknown=False)
    bumped_rebuild = base.with_attempt(obs, NextAction.REBUILD_ON_TARGET, is_unknown=False)
    assert bumped_repair.rung is EscalationRung.REPAIR
    assert bumped_rebuild.rung is EscalationRung.REPAIR
    assert bumped_repair.repeat_counts == {
        (bumped_repair.signature(obs), NextAction.REPAIR.value): 1,
    }
    assert bumped_rebuild.repeat_counts == {
        (bumped_rebuild.signature(obs), NextAction.REBUILD_ON_TARGET.value): 1,
    }


# ---------------------------------------------------------------------------
# Rule 8: dirty/staged/untracked worktree without conflict => COMMIT.
# ---------------------------------------------------------------------------


def test_decide_dirty_worktree_without_conflict_commits() -> None:
    """Rule 8: dirty/staged/untracked worktree without conflict yields COMMIT."""
    for dirty_worktree in (
        WorktreeState.TRACKED_CHANGES,
        WorktreeState.STAGED,
        WorktreeState.UNTRACKED,
    ):
        obs = Observation(
            target_vs_feature=TargetVsFeature.BEHIND,
            target_vs_remote=TargetVsRemote.EQUAL,
            worktree=dirty_worktree,
            head=HeadState.ATTACHED,
            in_progress_kind=InProgressKind.NONE,
            in_progress_ownership=InProgressOwnership.OWNED,
            record=IntegrationRecordState.CONSISTENT,
            agent_active=False,
        )
        for rung in LADDER_ORDER:
            ctx = DecisionContext(observation=obs, attempts=AttemptState(rung=rung))
            assert decide(ctx) is NextAction.COMMIT, (
                f"dirty worktree {dirty_worktree} @ {rung} should commit"
            )


# ---------------------------------------------------------------------------
# No discard-class action exists in the enum; AttemptState is immutable.
# ---------------------------------------------------------------------------


def test_next_action_enum_excludes_discard_actions() -> None:
    """The enum has no ``DISCARD``-class member by design."""
    member_names = {m.name for m in NextAction}
    assert "DISCARD" not in member_names
    assert "FORCE_RESET" not in member_names
    assert "ABORT" not in member_names


def test_attempt_state_is_immutable_and_bump_returns_new_instance() -> None:
    """``AttemptState`` is frozen: ``with_attempt`` returns a new value."""
    obs = Observation(
        target_vs_feature=TargetVsFeature.BEHIND,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=WorktreeState.CLEAN,
        head=HeadState.ATTACHED,
        in_progress_kind=InProgressKind.NONE,
        in_progress_ownership=InProgressOwnership.OWNED,
        record=IntegrationRecordState.CONSISTENT,
        agent_active=False,
    )
    base = AttemptState()
    bumped = base.with_attempt(obs, NextAction.REPAIR, is_unknown=False)
    assert bumped is not base
    assert base.repeat_counts == {}
    assert bumped.repeat_counts[(base.signature(obs), NextAction.REPAIR.value)] == 1


def test_attempt_state_unknown_attempt_does_not_touch_repeat_counts() -> None:
    """An unknown cycle increments ``unknown_attempts``, not the repeat counters."""
    obs = Observation(
        target_vs_feature=TargetVsFeature.UNKNOWN,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=WorktreeState.CLEAN,
        head=HeadState.ATTACHED,
        in_progress_kind=InProgressKind.NONE,
        in_progress_ownership=InProgressOwnership.OWNED,
        record=IntegrationRecordState.CONSISTENT,
        agent_active=False,
    )
    base = AttemptState()
    bumped = base.with_attempt(obs, NextAction.WAIT_FOR_ENVIRONMENT, is_unknown=True)
    assert bumped.rung is EscalationRung.REPAIR
    assert bumped.unknown_attempts == 1
    assert bumped.repeat_counts == base.repeat_counts


def test_attempt_state_signature_is_stable_across_observation_fields() -> None:
    """Signature depends only on the durable invariant dimensions."""
    obs_a = Observation(
        target_vs_feature=TargetVsFeature.BEHIND,
        target_vs_remote=TargetVsRemote.UNREACHABLE,
        worktree=WorktreeState.CLEAN,
        head=HeadState.ATTACHED,
        in_progress_kind=InProgressKind.NONE,
        in_progress_ownership=InProgressOwnership.OWNED,
        record=IntegrationRecordState.CONSISTENT,
        agent_active=True,
    )
    obs_b = Observation(
        target_vs_feature=TargetVsFeature.BEHIND,
        target_vs_remote=TargetVsRemote.BEHIND,
        worktree=WorktreeState.CLEAN,
        head=HeadState.ATTACHED,
        in_progress_kind=InProgressKind.NONE,
        in_progress_ownership=InProgressOwnership.OWNED,
        record=IntegrationRecordState.CONSISTENT,
        agent_active=False,
    )
    assert AttemptState().signature(obs_a) == AttemptState().signature(obs_b)


def test_attempt_state_key_pair_combines_signature_and_action() -> None:
    """``key_pair`` returns the ``(signature, action.value)`` pair the ladder counts."""
    obs = Observation(
        target_vs_feature=TargetVsFeature.BEHIND,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=WorktreeState.CLEAN,
        head=HeadState.ATTACHED,
        in_progress_kind=InProgressKind.NONE,
        in_progress_ownership=InProgressOwnership.OWNED,
        record=IntegrationRecordState.CONSISTENT,
        agent_active=False,
    )
    state = AttemptState()
    sig = state.signature(obs)
    assert state.key_pair(obs, NextAction.REPAIR) == (sig, NextAction.REPAIR.value)
    assert state.key_pair(obs, NextAction.REBUILD_ON_TARGET) == (
        sig,
        NextAction.REBUILD_ON_TARGET.value,
    )
