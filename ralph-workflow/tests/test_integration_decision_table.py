from __future__ import annotations

from collections.abc import Mapping
from functools import cache
from types import MappingProxyType

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
from tests.test_integration_state_space import integration_observation_matrix

pytestmark = pytest.mark.timeout_seconds(30)


_ALL_TARGET_VS_FEATURE: tuple[TargetVsFeature, ...] = tuple(TargetVsFeature)
_ALL_TARGET_VS_REMOTE: tuple[TargetVsRemote, ...] = tuple(TargetVsRemote)
_ALL_WORKTREE: tuple[WorktreeState, ...] = tuple(WorktreeState)
_ALL_HEAD: tuple[HeadState, ...] = tuple(HeadState)
_ALL_KIND: tuple[InProgressKind, ...] = tuple(InProgressKind)
_ALL_OWNERSHIP: tuple[InProgressOwnership, ...] = tuple(InProgressOwnership)
_ALL_RECORD: tuple[IntegrationRecordState, ...] = tuple(IntegrationRecordState)

_ALL_OPERATION_KINDS: tuple[InProgressKind, ...] = tuple(
    kind for kind in InProgressKind if kind is not InProgressKind.NONE
)


def _build_observations() -> tuple[Observation, ...]:
    return integration_observation_matrix()


@cache
def _build_unknown_observations() -> tuple[Observation, ...]:
    return tuple(obs for obs in _build_observations() if obs.has_unknown())


def test_observation_has_unknown_is_consistent_with_enum_membership() -> None:
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


_LADDER_ATTEMPTS: tuple[AttemptState, ...] = tuple(AttemptState(rung=rung) for rung in LADDER_ORDER)


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


@cache
def _observation_ladder_actions() -> Mapping[Observation, tuple[NextAction, ...]]:
    table: dict[Observation, tuple[NextAction, ...]] = {}
    first_unknown = _build_unknown_observations()[0]
    unknown_actions = tuple(
        decide(DecisionContext(observation=first_unknown, attempts=attempts))
        for attempts in _LADDER_ATTEMPTS
    )
    for obs in _build_observations():
        if obs.agent_active:
            continue
        if obs.has_unknown():
            actions = unknown_actions
        else:
            actions = tuple(
                decide(DecisionContext(observation=obs, attempts=attempts))
                for attempts in _LADDER_ATTEMPTS
            )
        table[obs] = actions
        obs_active = Observation(
            target_vs_feature=obs.target_vs_feature,
            target_vs_remote=obs.target_vs_remote,
            worktree=obs.worktree,
            head=obs.head,
            in_progress_kind=obs.in_progress_kind,
            in_progress_ownership=obs.in_progress_ownership,
            record=obs.record,
            agent_active=True,
        )
        table[obs_active] = actions
    return MappingProxyType(table)


def test_decide_total_mapping_covers_every_state_and_reaches_every_member() -> None:
    observations = _build_observations()
    assert len(observations) == 136_080
    table = _observation_ladder_actions()
    reached: set[NextAction] = set()
    for actions in table.values():
        for action in actions:
            assert isinstance(action, NextAction)
            reached.add(action)
    assert reached == _DEFAULT_CONTEXT_MEMBERS, (
        f"unreachable members: {_DEFAULT_CONTEXT_MEMBERS - reached}; "
        f"unexpected members: {reached - _DEFAULT_CONTEXT_MEMBERS}"
    )


def test_decide_unknown_observation_never_dispatches_resolver() -> None:
    unknown_observations = _build_unknown_observations()
    assert unknown_observations, "expected at least one unknown observation"
    table = _observation_ladder_actions()
    for observation in unknown_observations:
        assert NextAction.DISPATCH_RESOLVER not in table[observation], (
            f"resolver dispatched on unknown reading: {observation}"
        )


def test_decide_unknown_observation_yields_wait_or_pending() -> None:
    attempts_unexhausted = AttemptState(unknown_attempts=0, unknown_budget=5)
    attempts_exhausted = AttemptState(unknown_attempts=5, unknown_budget=5)
    for observation in _build_unknown_observations():
        action_wait = decide(
            DecisionContext(observation=observation, attempts=attempts_unexhausted)
        )
        assert action_wait is NextAction.WAIT_FOR_ENVIRONMENT, (
            f"unknown observation yielded non-wait action {action_wait}"
        )
        action_pending = decide(
            DecisionContext(observation=observation, attempts=attempts_exhausted)
        )
        assert action_pending is NextAction.CONTINUE_WITH_INTEGRATION_PENDING, (
            f"unknown observation yielded non-pending action {action_pending}"
        )


def test_decide_wait_for_environment_holds_until_budget_then_continues() -> None:
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


def test_decide_landed_work_short_circuits_to_land() -> None:
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


def test_decide_real_conflict_uses_dispatch_resolver() -> None:
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

    for kind in _ALL_OPERATION_KINDS:
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


def test_decide_owned_in_progress_resumes_owned_operation() -> None:
    for kind in _ALL_OPERATION_KINDS:
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


def test_decide_ladder_advances_after_signature_repeats() -> None:
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


def test_decide_dirty_worktree_without_conflict_commits() -> None:
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


def test_next_action_enum_excludes_discard_actions() -> None:
    member_names = {m.name for m in NextAction}
    assert "DISCARD" not in member_names
    assert "FORCE_RESET" not in member_names
    assert "ABORT" not in member_names


def test_attempt_state_is_immutable_and_bump_returns_new_instance() -> None:
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


def test_decide_output_is_invariant_to_agent_active_flag() -> None:
    state = AttemptState()
    for observation in _build_observations():
        if not observation.agent_active:
            continue
        flipped = Observation(
            target_vs_feature=observation.target_vs_feature,
            target_vs_remote=observation.target_vs_remote,
            worktree=observation.worktree,
            head=observation.head,
            in_progress_kind=observation.in_progress_kind,
            in_progress_ownership=observation.in_progress_ownership,
            record=observation.record,
            agent_active=False,
        )
        if observation.has_unknown():
            act_a = act_b = NextAction.WAIT_FOR_ENVIRONMENT
        else:
            context_a = DecisionContext(observation=observation, attempts=state)
            context_b = DecisionContext(observation=flipped, attempts=state)
            act_a = decide(context_a)
            act_b = decide(context_b)
        assert act_a is act_b, (
            "agent_active changed the decision — the flag has become a "
            "decision input; the mutation lease owns deferral instead"
        )
        assert state.signature(observation) == state.signature(flipped), (
            "agent_active leaked into the escalation signature"
        )
        assert state.key_pair(observation, act_a) == state.key_pair(flipped, act_b), (
            "agent_active changed the (signature, action) repeat key"
        )
