"""Recovery hops must leave the cycle timer in a state that still bounds work.

Two hops got it wrong in opposite directions. A commit failure routes to the
recovery failed route, which is a terminal to the reducer but NOT the end of the
run — the effect router turns it back into the phase the run was in — so
concluding the cycle there switched the deadline off for the rest of a cycle
that kept running, and it could never re-arm because starting a timer requires
an inactive one. The missing-plan-handoff recovery does the reverse: it routes
all the way back to planning with the timer still running, so planning time is
charged to the finished cycle and the next cycle inherits its spent clock.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pytest

from ralph.pipeline._runner_state_helpers import recover_missing_plan_handoff
from ralph.pipeline.cycle_timing import RoutingTiming
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.prompts._missing_plan_handoff_error import MissingPlanHandoffError

_DEFAULTS_DIR = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
_CONSUMED = 4000.0


@lru_cache(maxsize=1)
def _pipeline() -> object:
    return load_policy(_DEFAULTS_DIR).pipeline


def _in_cycle(phase: str, *, consumed: float = _CONSUMED) -> PipelineState:
    return PipelineState(
        phase=phase,
        budget_caps={"iteration": 5},
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=consumed,
    )


def test_a_commit_failure_leaves_the_cycle_deadline_armed() -> None:
    """The failed route re-enters the cycle, so it must not disarm the timer.

    Disarming it let the remainder of the cycle run unbounded: the deadline
    stopped redirecting, the agent stopped being warned, and the operator's
    budget item went silent — for a cycle that was still going.
    """
    state, _effects = reducer_reduce(
        _in_cycle("development_commit"),
        PipelineEvent.COMMIT_FAILURE,
        _pipeline(),
        routing_timing=RoutingTiming(monotonic_now=0.0, total_elapsed_seconds=_CONSUMED),
    )

    assert state.cycle_timebox_active is True


def test_the_deadline_still_redirects_after_a_commit_failure() -> None:
    """The premise: an over-budget cycle is still finalized after the hop."""
    failed, _effects = reducer_reduce(
        _in_cycle("development_commit"),
        PipelineEvent.COMMIT_FAILURE,
        _pipeline(),
        routing_timing=RoutingTiming(monotonic_now=0.0, total_elapsed_seconds=_CONSUMED),
    )
    # The run resumes inside the cycle and burns past its budget.
    resumed = failed.copy_with(phase="development_analysis")

    redirected, _effects = reducer_reduce(
        resumed,
        PipelineEvent.ANALYSIS_LOOPBACK,
        _pipeline(),
        routing_timing=RoutingTiming(monotonic_now=0.0, total_elapsed_seconds=16_000.0),
    )

    assert redirected.phase == "development_final_commit_cleanup"
    assert redirected.cycle_timebox_redirect_reason is not None


def test_recovering_a_missing_plan_handoff_concludes_the_cycle(tmp_path: Path) -> None:
    """Routing back to planning leaves the cycle behind; the timer must stop.

    Left running, the timer charges the re-plan to the finished cycle and — if
    planning analysis is bypassed — the NEXT cycle is judged on the previous
    one's spent clock and redirected to its final commit before development
    ever runs.
    """
    recovered = recover_missing_plan_handoff(
        state=_in_cycle("development", consumed=7300.0),
        pipeline_policy=_pipeline(),
        checkpoint_path=tmp_path / "checkpoint.json",
        subscriber=None,
        exc=MissingPlanHandoffError("no plan handoff at .agent/PLAN.md"),
    )

    assert recovered.phase == _pipeline().entry_phase
    assert recovered.cycle_timebox_active is False


def test_the_cycle_after_a_missing_plan_handoff_gets_a_fresh_budget(
    tmp_path: Path,
) -> None:
    """The premise: the freshly planned cycle actually gets to develop."""
    recovered = recover_missing_plan_handoff(
        state=_in_cycle("development", consumed=7300.0),
        pipeline_policy=_pipeline(),
        checkpoint_path=tmp_path / "checkpoint.json",
        subscriber=None,
        exc=MissingPlanHandoffError("no plan handoff at .agent/PLAN.md"),
    )
    # Planning analysis has no loop budget left, so it is bypassed and the new
    # cycle starts on entry to development rather than on the declared edge.
    spent = recovered.with_loop_iteration("planning_analysis_iteration", 3)

    advanced, _effects = reducer_reduce(
        spent,
        PipelineEvent.AGENT_SUCCESS,
        _pipeline(),
        routing_timing=RoutingTiming(monotonic_now=0.0, total_elapsed_seconds=0.0),
    )

    assert advanced.phase == "development"
    assert advanced.cycle_timebox_consumed_seconds == pytest.approx(0.0)


def test_a_recovery_re_entry_is_still_bound_by_the_deadline() -> None:
    """The hop back into the guarded phase must be checked like any other entry.

    Keeping the timer armed across the hop is only half the contract: the
    runtime re-enters the phase by writing ``state.phase`` directly, bypassing
    the routing boundary where the deadline is enforced. An expired cycle
    therefore bought one unbudgeted development invocation per hop — and since
    each hop also resets the retry chain, a persistently failing agent could
    ping-pong through it indefinitely with the budget long gone.
    """
    from ralph.pipeline.cycle_timing import apply_cycle_timebox

    expired = _in_cycle("failed_terminal", consumed=14_400.0)

    decision = apply_cycle_timebox(
        expired,
        "development",
        policy=_pipeline(),
        routing_timing=RoutingTiming(monotonic_now=0.0, total_elapsed_seconds=14_400.0),
    )

    assert decision.target_phase == "development_final_commit_cleanup"
    assert decision.redirected is True


def test_the_runtime_re_entry_applies_that_decision(tmp_path: Path) -> None:
    """Pinning the helper is not enough — the re-entry path must consult it.

    Driven through the real inline-effect handler, because the defect is that
    this branch writes ``state.phase`` directly instead of routing.
    """
    from ralph.pipeline import runner as runner_module
    from ralph.pipeline.effects import PreparePromptEffect
    from ralph.workspace.scope import WorkspaceScope

    # The handler materializes the target's prompt, which requires the plan
    # handoff; without it the missing-handoff recovery fires first.
    (tmp_path / ".agent").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".agent" / "PLAN.md").write_text("# Plan\n", encoding="utf-8")
    (tmp_path / "PROMPT.md").write_text("Do the work\n", encoding="utf-8")
    expired = _in_cycle("failed_terminal", consumed=14_400.0).copy_with(
        previous_phase="development"
    )

    updated = runner_module._handle_inline_effect(
        effect=PreparePromptEffect(phase="development", drain="development"),
        state=expired,
        pipeline_policy=_pipeline(),
        artifacts_policy=load_policy(_DEFAULTS_DIR).artifacts,
        workspace_scope=WorkspaceScope(tmp_path),
    )

    assert updated.phase == "development_final_commit_cleanup"
    assert updated.cycle_timebox_active is False
