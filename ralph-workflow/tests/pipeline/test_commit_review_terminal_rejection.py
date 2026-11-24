from __future__ import annotations

import functools
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.phases.commit_review import CommitReviewRejected
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.cycle_timing import RoutingTiming
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.agent_failure_ledger import Cooldown
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions

if TYPE_CHECKING:
    from ralph.policy.models import PipelinePolicy

_DEFAULTS_DIR = Path(__file__).resolve().parents[2] / "ralph" / "policy" / "defaults"
_REVIEW_PHASE = "development_commit_review"


@functools.cache
def _policy() -> PipelinePolicy:
    return load_policy(_DEFAULTS_DIR).pipeline


@pytest.mark.parametrize("condition", ("rejection_limit", "cycle_timebox"))
def test_rejection_always_restores_every_file_and_retries_development(condition: str) -> None:
    policy = _policy()
    max_rejections = policy.loop_counters["commit_review_rejections"].default_max
    ref = f"refs/ralph/proposals/rejection-always-retries/{condition}"
    if condition == "rejection_limit":
        state = PipelineState(
            phase=_REVIEW_PHASE,
            commit_review_rejections=max_rejections - 1,
        )
        elapsed_seconds = 1.0
    else:
        assert policy.cycle_timebox is not None
        state = PipelineState(
            phase=_REVIEW_PHASE,
            cycle_timebox_active=True,
        )
        elapsed_seconds = float(policy.cycle_timebox.duration_seconds) + 1.0

    rejected, effects = reduce(
        state,
        CommitReviewRejected(reason="proposal rejected", ref=ref, hard=True),
        policy,
        routing_timing=RoutingTiming(total_elapsed_seconds=elapsed_seconds),
    )

    assert rejected.phase == "development"
    assert rejected.rollback_pending is True
    assert rejected.rollback_ref == ref
    assert rejected.last_error is None
    assert effects == []


def test_wu_a2_terminal_regression_normal_rejection_still_requests_development_rollback() -> None:
    policy = _policy()
    ref = "refs/ralph/proposals/wu-a2-terminal/normal"

    rejected, effects = reduce(
        PipelineState(phase=_REVIEW_PHASE, commit_review_rejections=0),
        CommitReviewRejected(reason="proposal rejected", ref=ref, hard=True),
        policy,
        routing_timing=RoutingTiming(total_elapsed_seconds=1.0),
    )

    assert rejected.phase == "development"
    assert rejected.rollback_pending is True
    assert rejected.rollback_ref == ref
    assert effects == []


def test_rejection_cools_the_proposal_author_before_retrying_development() -> None:
    policy = _policy()
    clock = FakeClock(start=0.0)
    controller = RecoveryController(options=RecoveryControllerOptions(cycle_cap=10, clock=clock))
    author = "agy/gemini-4-argon-low"
    fallback = "pi/minimax/MiniMax-M3"
    state = PipelineState(
        phase=_REVIEW_PHASE,
        previous_phase="development",
        phase_chains={
            "development": AgentChainState(
                agents=[author, fallback],
                current_index=0,
            )
        },
    )

    rejected, _effects = reduce(
        state,
        CommitReviewRejected(
            reason="Protected document overwritten: AGENTS.md",
            ref="refs/ralph/proposals/rejected-author/1",
            hard=True,
        ),
        policy,
        recovery=controller,
    )

    assert rejected.phase == "development"
    assert isinstance(controller.agent_eligibility("development", author), Cooldown)
    selection = controller.preferred_agent_index(
        "development",
        [author, fallback],
        current_index=0,
    )
    assert selection.agent == fallback
    assert rejected.agent_cooldowns is not None
    assert author in rejected.agent_cooldowns["unavailable_until_epoch_ms"]


@pytest.mark.parametrize("terminal_condition", ("rejection_limit", "cycle_timebox"))
def test_rejection_accounts_the_attempt_then_requests_complete_rollback(
    terminal_condition: str,
) -> None:
    policy = _policy()
    commit_phase = "development_commit"
    commit_policy = policy.phases[commit_phase].commit_policy
    assert commit_policy is not None
    reset_fields = tuple(commit_policy.loop_resets)
    assert reset_fields

    ref = f"refs/ralph/proposals/wu-3-terminal/{terminal_condition}"
    max_rejections = policy.loop_counters["commit_review_rejections"].default_max
    if terminal_condition == "rejection_limit":
        state = PipelineState(
            phase=_REVIEW_PHASE,
            commit_review_rejections=max_rejections - 1,
        )
        elapsed_seconds = 1.0
        expected_rejections = max_rejections
    else:
        assert policy.cycle_timebox is not None
        state = PipelineState(
            phase=_REVIEW_PHASE,
            cycle_timebox_active=True,
            commit_review_rejections=0,
        )
        elapsed_seconds = float(policy.cycle_timebox.duration_seconds) + 1.0
        expected_rejections = 1

    for reset_field in reset_fields:
        state = state.with_loop_iteration(reset_field, 2)

    rejected, effects = reduce(
        state,
        CommitReviewRejected(reason="proposal rejected", ref=ref, hard=True),
        policy,
        routing_timing=RoutingTiming(total_elapsed_seconds=elapsed_seconds),
    )

    assert rejected.phase == "development"
    for reset_field in reset_fields:
        assert rejected.get_loop_iteration(reset_field) == 0
    assert rejected.cycle_timing_start_index == state.cycle_timing_start_index
    assert rejected.commit_review_rejections == expected_rejections
    assert rejected.rollback_pending is True
    assert rejected.rollback_ref == ref
    assert effects == []
