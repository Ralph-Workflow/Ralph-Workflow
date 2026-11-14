from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.timeout_clock import FakeClock
from ralph.phases.commit_review import CommitReviewGateFailed, CommitReviewRejected
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.agent_failure_ledger import AgentFailureLedger, AttemptFact, Cooldown
from ralph.recovery.agent_unavailability_tracker import UnavailabilityEntry
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.unavailability_reason import UnavailabilityReason

if TYPE_CHECKING:
    from ralph.policy.models import PipelinePolicy


def _minimal_policy() -> PipelinePolicy:
    with tempfile.TemporaryDirectory() as directory:
        return load_policy(Path(directory) / ".agent").pipeline


def _controller(clock: FakeClock, entries: dict[str, UnavailabilityEntry]) -> RecoveryController:
    return RecoveryController(
        options=RecoveryControllerOptions(
            cycle_cap=10,
            clock=clock,
            unavailability_entries=entries,
        )
    )


def _entry(unavailable_until_ms: int) -> UnavailabilityEntry:
    return UnavailabilityEntry(
        unavailable_until_ms=unavailable_until_ms,
        reason=UnavailabilityReason.NO_OUTPUT_AT_START,
        attempt=0,
        base_backoff_ms=5000,
        max_backoff_ms=5000,
    )


def test_agent_failure_waits_when_every_chain_agent_is_in_cooldown() -> None:
    clock = FakeClock(start=0.0)
    state = PipelineState(
        phase="development",
        phase_chains={
            "development": AgentChainState(
                agents=["claude", "opencode", "agy"],
                current_index=2,
                retries=0,
            )
        },
    )
    controller = _controller(
        clock,
        {
            "claude": _entry(5000),
            "opencode": _entry(8000),
            "agy": _entry(10000),
        },
    )

    reduced_state, effects = reduce(
        state, PipelineEvent.AGENT_FAILURE, _minimal_policy(), recovery=controller
    )

    assert reduced_state.phase == "development"
    assert reduced_state.is_waiting_state is True
    assert reduced_state.last_retry_delay_ms == 5000
    assert effects == []


def test_agent_failure_falls_over_when_an_agent_is_available() -> None:
    clock = FakeClock(start=0.0)
    state = PipelineState(
        phase="development",
        phase_chains={
            "development": AgentChainState(
                agents=["claude", "opencode", "agy"],
                current_index=2,
                retries=3,
            )
        },
    )
    controller = _controller(
        clock,
        {
            "claude": _entry(5000),
            "opencode": _entry(8000),
        },
    )

    reduced_state, _effects = reduce(
        state, PipelineEvent.AGENT_FAILURE, _minimal_policy(), recovery=controller
    )
    chain = reduced_state.chain_for_phase("development")

    assert chain is not None
    assert chain.current_index == 2
    assert reduced_state.is_waiting_state is False


def test_failed_route_does_not_treat_agent_cooldown_as_terminal_intervention() -> None:
    from ralph.pipeline import run_loop

    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(consecutive_limit=1, total_limit=10, clock=clock)
    outcome = ledger.record(
        AttemptFact(
            phase="planning",
            agent="claude",
            artifact_valid=False,
            workspace_changed=False,
        )
    )
    assert isinstance(outcome.status, Cooldown)
    controller = RecoveryController(
        options=RecoveryControllerOptions(cycle_cap=10, clock=clock, ledger=ledger)
    )
    state = PipelineState(
        phase="failed_terminal",
        previous_phase="planning",
        recovery_epoch=3,
        last_execution_result_status="failed",
        phase_chains={"planning": AgentChainState(agents=["claude"])},
    )

    requires_intervention = run_loop.ledger_requires_intervention(
        state,
        controller,
    )

    assert requires_intervention is False


def test_refused_rollback_stops_the_run_even_while_a_reviewer_is_cooling_down() -> None:
    from ralph.pipeline import run_loop

    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(consecutive_limit=1, total_limit=10, clock=clock)
    outcome = ledger.record(
        AttemptFact(
            phase="development_commit_review",
            agent="claude",
            artifact_valid=False,
            workspace_changed=False,
        )
    )
    assert isinstance(outcome.status, Cooldown)
    controller = RecoveryController(
        options=RecoveryControllerOptions(cycle_cap=10, clock=clock, ledger=ledger)
    )
    policy = _minimal_policy()
    rejected = PipelineState(
        phase="development_commit_review",
        rollback_ref="refs/ralph/proposals/run-1/1",
        phase_chains={"development_commit_review": AgentChainState(agents=["claude", "codex"])},
    )

    refused, _effects = reduce(
        rejected,
        CommitReviewGateFailed(detail="Rollback preflight failed: damaged repository"),
        policy,
        recovery=controller,
    )

    assert refused.phase == policy.recovery.failed_route
    assert run_loop.failed_route_is_recoverable(refused, controller) is False


def test_rejection_at_the_ceiling_rolls_back_and_stays_recoverable() -> None:
    from ralph.pipeline import run_loop

    controller = RecoveryController(
        options=RecoveryControllerOptions(cycle_cap=10, clock=FakeClock(start=0.0))
    )
    policy = _minimal_policy()
    state = PipelineState(phase="development_commit_review", commit_review_rejections=1)

    at_ceiling, _effects = reduce(
        state,
        CommitReviewRejected(reason="Repeated damage", ref="refs/ralph/proposals/run-1/1"),
        policy,
        recovery=controller,
    )
    rolled_back = at_ceiling.copy_with(rollback_pending=False, rollback_ref=None)

    assert at_ceiling.phase == policy.recovery.failed_route
    assert at_ceiling.rollback_pending is True
    assert run_loop.failed_route_is_recoverable(rolled_back, controller) is True


def test_failed_route_stays_recoverable_without_a_kept_rejected_proposal() -> None:
    from ralph.pipeline import run_loop

    controller = RecoveryController(
        options=RecoveryControllerOptions(cycle_cap=10, clock=FakeClock(start=0.0))
    )
    state = PipelineState(phase="failed_terminal", previous_phase="planning")

    assert run_loop.failed_route_is_recoverable(state, controller) is True
