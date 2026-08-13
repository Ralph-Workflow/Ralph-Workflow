"""Reducer-level tests for the plan-to-final-commit cycle timebox.

These tests drive the pure ``reduce`` state machine with an explicit
:class:`RoutingTiming` (a fake monotonic clock + computed elapsed seconds) so
they are fully deterministic and never wait on real wall-clock time. They cover
timer boundaries, deadline enforcement at the routing boundary, final-commit
exclusion, restart/resume safety, and the derived 80% soft-warning point.
"""

from __future__ import annotations

from pathlib import Path

from ralph.pipeline.cycle_timing import RoutingTiming, cycle_timebox_warning
from ralph.pipeline.events import AnalysisDecisionEvent, PhaseFailureEvent, PipelineEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.policy.models import PhaseWorkflowFallback

_DEFAULTS_DIR = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"


def _policy():
    return load_policy(_DEFAULTS_DIR).pipeline


def _rt(elapsed: float) -> RoutingTiming:
    """Build a RoutingTiming whose total elapsed equals ``elapsed``."""
    return RoutingTiming(monotonic_now=elapsed, total_elapsed_seconds=elapsed)


def _state(phase: str, **overrides: object) -> PipelineState:
    return PipelineState(phase=phase, **overrides)


# ---------------------------------------------------------------------------
# Timer boundaries and reset
# ---------------------------------------------------------------------------


def test_planning_time_does_not_start_timer() -> None:
    """Time spent in planning/planning_analysis does not start the cycle timer."""
    policy = _policy()
    state = _state("planning")
    # planning AGENT_SUCCESS -> planning_analysis; timer must remain stopped.
    next_state, _ = reduce(
        state, "agent_success", policy, routing_timing=_rt(10_000.0)
    )
    assert next_state.phase == "planning_analysis"
    assert next_state.cycle_timebox_active is False


def test_timer_starts_on_planning_to_development_handoff() -> None:
    """The first entry to development while inactive starts the timer."""
    policy = _policy()
    state = _state("planning_analysis")
    # planning_analysis ANALYSIS_SUCCESS -> development (the start transition).
    next_state, _ = reduce(
        state, "analysis_success", policy, routing_timing=_rt(0.0)
    )
    assert next_state.phase == "development"
    assert next_state.cycle_timebox_active is True
    assert next_state.cycle_timebox_consumed_seconds == 0.0


def test_no_reset_on_intermediate_activity() -> None:
    """Advancing through commit/analysis phases preserves the active timer."""
    policy = _policy()
    state = _state("development_commit_cleanup", cycle_timebox_active=True,
                    cycle_timebox_consumed_seconds=500.0)
    # commit_cleanup has role commit_cleanup; AGENT_SUCCESS -> development_commit.
    next_state, _ = reduce(
        state, "agent_success", policy, routing_timing=_rt(500.0)
    )
    # Timer remains active; the reducer does not touch consumed on non-guarded
    # transitions (the runner folds elapsed time).
    assert next_state.cycle_timebox_active is True
    assert next_state.cycle_timebox_consumed_seconds == 500.0


def test_entering_final_commit_path_ends_timing() -> None:
    """Routing into the final-commit cleanup phase ends cycle timing."""
    policy = _policy()
    state = _state(
        "development_analysis",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=3000.0,
    )
    # development_analysis decision 'completed' -> development_final_commit_cleanup.
    event = AnalysisDecisionEvent(phase="development_analysis", decision="completed")
    next_state, _ = reduce(state, event, policy, routing_timing=_rt(3000.0))
    assert next_state.phase == "development_final_commit_cleanup"
    assert next_state.cycle_timebox_active is False
    assert next_state.cycle_timebox_consumed_seconds == 3000.0


# ---------------------------------------------------------------------------
# Deadline enforcement at development re-entry
# ---------------------------------------------------------------------------


def _request_changes_to_development(
    state: PipelineState, elapsed: float
) -> PipelineState:
    policy = _policy()
    event = AnalysisDecisionEvent(phase="development_analysis", decision="request_changes")
    next_state, _ = reduce(state, event, policy, routing_timing=_rt(elapsed))
    return next_state


def test_development_reentry_permitted_before_deadline() -> None:
    state = _state(
        "development_analysis",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=100.0,
    )
    next_state = _request_changes_to_development(state, 100.0)
    assert next_state.phase == "development"
    assert next_state.cycle_timebox_active is True


def test_development_reentry_redirected_at_deadline() -> None:
    state = _state(
        "development_analysis",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=7200.0,
    )
    next_state = _request_changes_to_development(state, 7200.0)
    assert next_state.phase == "development_final_commit_cleanup"
    assert next_state.cycle_timebox_active is False


def test_development_reentry_redirected_after_deadline() -> None:
    state = _state(
        "development_analysis",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=8000.0,
    )
    next_state = _request_changes_to_development(state, 8000.0)
    assert next_state.phase == "development_final_commit_cleanup"
    assert next_state.cycle_timebox_active is False


def test_custom_deadline_keeps_redirect_at_new_boundary() -> None:
    """A custom duration redirects at its own boundary (AC-13/AC-15)."""
    policy = _policy()
    # Replace the duration with a 600s custom deadline via a copy.
    from ralph.policy.models import CycleTimeboxPolicy

    custom = policy.model_copy(update={
        "cycle_timebox": CycleTimeboxPolicy(
            duration_seconds=600.0,
            start_source="planning_analysis",
            start_entry="development",
            guarded_entry="development",
            end_entry="development_final_commit_cleanup",
            finalization_target="development_final_commit_cleanup",
        )
    })
    state = _state(
        "development_analysis",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=600.0,
    )
    event = AnalysisDecisionEvent(phase="development_analysis", decision="request_changes")
    next_state, _ = reduce(state, event, custom, routing_timing=_rt(600.0))
    assert next_state.phase == "development_final_commit_cleanup"
    # And the derived 80% warning point is 480s for the custom deadline.
    assert custom.cycle_timebox.warning_threshold_seconds == 480.0


def test_first_development_entry_always_permitted_even_if_clock_advanced() -> None:
    """The first entry while inactive starts the timer and is never redirected."""
    state = _state("planning_analysis")  # inactive
    policy = _policy()
    next_state, _ = reduce(
        state, "analysis_success", policy, routing_timing=_rt(99_999.0)
    )
    assert next_state.phase == "development"
    assert next_state.cycle_timebox_active is True


# ---------------------------------------------------------------------------
# Deadline enforcement on workflow_fallback routes (FR-4: every guarded entry)
# ---------------------------------------------------------------------------


def _policy_with_development_fallback():
    """Default policy with ``development_analysis.workflow_fallback -> development``.

    This adds a non-analysis-decision route that can enter the guarded
    development entry, so the cycle deadline guard must cover it just like the
    ``request_changes`` loopback. Both the non-recoverable PhaseFailureEvent
    path and the exhausted-agent-chain path can take this route.
    """
    policy = _policy()
    dev_analysis = policy.phases["development_analysis"]
    return policy.model_copy(
        update={
            "phases": {
                **policy.phases,
                "development_analysis": dev_analysis.model_copy(
                    update={"workflow_fallback": PhaseWorkflowFallback(target="development")}
                ),
            }
        }
    )


def test_phase_failure_workflow_fallback_enforces_deadline() -> None:
    """A non-recoverable phase failure's workflow_fallback into the guarded
    development entry redirects to finalization at the cycle deadline.

    Covers FR-4 / AC-8 for the workflow_fallback route (the DA-020
    counterexample): every policy route about to enter the configured guarded
    phase must enforce the active deadline, not only the analysis
    ``request_changes`` decision.
    """
    policy = _policy_with_development_fallback()
    state = _state(
        "development_analysis",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=7200.0,
    )
    event = PhaseFailureEvent(
        phase="development_analysis",
        reason="non-recoverable handler failure",
        recoverable=False,
    )
    next_state, _ = reduce(state, event, policy, routing_timing=_rt(7200.0))
    assert next_state.phase == "development_final_commit_cleanup"
    assert next_state.cycle_timebox_active is False


def test_phase_failure_workflow_fallback_permitted_before_deadline() -> None:
    """Before the deadline the same workflow_fallback enters development normally."""
    policy = _policy_with_development_fallback()
    state = _state(
        "development_analysis",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=100.0,
    )
    event = PhaseFailureEvent(
        phase="development_analysis",
        reason="non-recoverable handler failure",
        recoverable=False,
    )
    next_state, _ = reduce(state, event, policy, routing_timing=_rt(100.0))
    assert next_state.phase == "development"
    assert next_state.cycle_timebox_active is True


def test_agent_failure_workflow_fallback_enforces_deadline() -> None:
    """An exhausted-agent-chain workflow_fallback into the guarded development
    entry also redirects to finalization at the cycle deadline (FR-4)."""
    policy = _policy_with_development_fallback()
    state = _state(
        "development_analysis",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=7200.0,
        phase_chains={
            "development_analysis": AgentChainState(
                agents=["claude"], current_index=0, retries=3
            )
        },
    )
    next_state, _ = reduce(
        state, PipelineEvent.AGENT_FAILURE, policy, routing_timing=_rt(7200.0)
    )
    assert next_state.phase == "development_final_commit_cleanup"
    assert next_state.cycle_timebox_active is False


# ---------------------------------------------------------------------------
# Transition-specific start (FR-2: only the declared source->target starts)
# ---------------------------------------------------------------------------


def test_timer_does_not_start_on_unrelated_route_into_guarded_phase() -> None:
    """Only the declared start_source -> start_entry transition starts the timer.

    An unrelated route into the guarded phase while the cycle is inactive
    must NOT start or reset the cycle (FR-2, AC-1).
    """
    from ralph.pipeline.cycle_timing import apply_cycle_timebox

    policy = _policy()
    # Inactive cycle, routing from a phase that is NOT the declared start_source
    # (planning_analysis) into development (the guarded/start entry).
    state = _state("development_commit")
    decision = apply_cycle_timebox(
        state, "development", policy=policy, routing_timing=_rt(0.0)
    )
    assert decision.timing_started is False
    assert decision.state.cycle_timebox_active is False
    # The entry is still permitted (not redirected) — it just isn't timed.
    assert decision.redirected is False
    assert decision.target_phase == "development"


def test_timer_starts_only_from_declared_start_source() -> None:
    """Entering the start entry from the declared start_source starts the timer."""
    from ralph.pipeline.cycle_timing import apply_cycle_timebox

    policy = _policy()
    state = _state("planning_analysis")  # == start_source
    decision = apply_cycle_timebox(
        state, "development", policy=policy, routing_timing=_rt(0.0)
    )
    assert decision.timing_started is True
    assert decision.state.cycle_timebox_active is True


# ---------------------------------------------------------------------------
# Resume safety / checkpoint round-trips
# ---------------------------------------------------------------------------


def test_consumed_time_preserved_across_serialize_roundtrip() -> None:
    state = _state(
        "development_commit",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=3210.5,
    )
    restored = PipelineState.model_validate_json(state.model_dump_json())
    assert restored.cycle_timebox_active is True
    assert restored.cycle_timebox_consumed_seconds == 3210.5


def test_legacy_checkpoint_without_cycle_state_resumes_safely() -> None:
    """An older checkpoint lacking cycle fields loads with safe defaults."""
    legacy = _state("development", cycle_timebox_active=False)
    # Serialize, then strip the cycle fields to simulate a legacy checkpoint.
    import json

    data = json.loads(legacy.model_dump_json())
    data.pop("cycle_timebox_active", None)
    data.pop("cycle_timebox_consumed_seconds", None)
    restored = PipelineState.model_validate(data)
    assert restored.cycle_timebox_active is False
    assert restored.cycle_timebox_consumed_seconds == 0.0


# ---------------------------------------------------------------------------
# Soft warning (80% derived point)
# ---------------------------------------------------------------------------


def test_warning_absent_before_threshold() -> None:
    policy = _policy()
    state = _state("development_analysis", cycle_timebox_active=True,
                    cycle_timebox_consumed_seconds=100.0)
    assert (
        cycle_timebox_warning(
            state, "development", policy=policy, routing_timing=_rt(100.0)
        )
        is None
    )


def test_warning_present_at_80_percent_under_default() -> None:
    policy = _policy()
    state = _state("development_analysis", cycle_timebox_active=True,
                    cycle_timebox_consumed_seconds=5760.0)
    warning = cycle_timebox_warning(
        state, "development", policy=policy, routing_timing=_rt(5760.0)
    )
    assert warning is not None
    assert warning["elapsed_seconds"] == 5760.0
    # 7200 - 5760 = 1440s = 24 minutes remaining.
    assert warning["remaining_seconds"] == 1440.0
    assert warning["finalization_target"] == "development_final_commit_cleanup"


def test_warning_absent_for_inactive_cycle() -> None:
    policy = _policy()
    state = _state("planning_analysis")  # inactive
    assert (
        cycle_timebox_warning(
            state, "development", policy=policy, routing_timing=_rt(5760.0)
        )
        is None
    )


def test_warning_absent_for_non_guarded_phase() -> None:
    policy = _policy()
    state = _state("development_analysis", cycle_timebox_active=True,
                    cycle_timebox_consumed_seconds=5760.0)
    assert (
        cycle_timebox_warning(
            state, "development_commit", policy=policy, routing_timing=_rt(5760.0)
        )
        is None
    )

# ---------------------------------------------------------------------------
# Runner per-step fold simulation
# ---------------------------------------------------------------------------


def _runner_sim_step(
    state: PipelineState,
    event: str | object,
    policy: object,
    box: list[float | None],
    fake_clock: list[float],
) -> PipelineState:
    """Mirror the timing logic the runner applies around each ``reduce`` call.

    This replicates the sample → reduce → fold → update-sample sequence from
    ``_run_pipeline_step`` so tests can verify the per-step accumulation and
    crash-resume behavior without the full runner/display/registry stack.
    """
    now = fake_clock[0]
    last = box[0]
    delta = (
        max(0.0, now - last)
        if (state.cycle_timebox_active and last is not None)
        else 0.0
    )
    rt = RoutingTiming(
        monotonic_now=now,
        total_elapsed_seconds=state.cycle_timebox_consumed_seconds + delta,
    )
    next_state, _ = reduce(state, event, policy, routing_timing=rt)
    if (
        state.cycle_timebox_active
        and delta > 0.0
    ):
        next_state = next_state.model_copy(
            update={
                "cycle_timebox_consumed_seconds": (
                    next_state.cycle_timebox_consumed_seconds + delta
                ),
            }
        )
    box[0] = now
    return next_state


def _custom_policy(duration: float = 100.0):
    """Policy with a short cycle deadline for fast multi-step tests."""
    from ralph.policy.models import CycleTimeboxPolicy

    policy = _policy()
    return policy.model_copy(
        update={
            "cycle_timebox": CycleTimeboxPolicy(
                duration_seconds=duration,
                start_source="planning_analysis",
                start_entry="development",
                guarded_entry="development",
                end_entry="development_final_commit_cleanup",
                finalization_target="development_final_commit_cleanup",
            )
        }
    )


def test_per_step_fold_accumulates_consumed_time_across_steps() -> None:
    """Multiple development-loop steps accumulate wall-clock into consumed."""
    custom = _custom_policy(duration=100.0)
    box: list[float | None] = [None]
    clock = [0.0]

    # Step 1: planning_analysis → development (timer starts, consumed = 0).
    state = _runner_sim_step(
        _state("planning_analysis"), "analysis_success", custom, box, clock
    )
    assert state.phase == "development"
    assert state.cycle_timebox_active is True
    assert state.cycle_timebox_consumed_seconds == 0.0

    # Step 2: advance clock 40s, development → development_commit_cleanup.
    clock[0] = 40.0
    state = _runner_sim_step(state, "agent_success", custom, box, clock)
    assert state.cycle_timebox_active is True
    assert state.cycle_timebox_consumed_seconds == 40.0

    # Step 3: advance clock 30s more (70s total), commit_cleanup → commit.
    clock[0] = 70.0
    state = _runner_sim_step(state, "agent_success", custom, box, clock)
    assert state.cycle_timebox_active is True
    assert state.cycle_timebox_consumed_seconds == 70.0

    # Step 4: advance clock 35s more (105s total > 100s deadline).
    # development_analysis → development (loopback) should redirect.
    clock[0] = 105.0
    state = _runner_sim_step(
        state,
        AnalysisDecisionEvent(phase="development_analysis", decision="request_changes"),
        custom,
        box,
        clock,
    )
    assert state.phase == "development_final_commit_cleanup"
    assert state.cycle_timebox_active is False


def test_consumed_time_survives_checkpoint_roundtrip_and_continues() -> None:
    """Serialized consumed time carries the budget across a simulated restart."""
    custom = _custom_policy(duration=200.0)
    box: list[float | None] = [None]
    clock = [0.0]

    # Accumulate 80s of cycle time.
    state = _runner_sim_step(
        _state("planning_analysis"), "analysis_success", custom, box, clock
    )
    clock[0] = 80.0
    state = _runner_sim_step(state, "agent_success", custom, box, clock)
    assert state.cycle_timebox_consumed_seconds == 80.0

    # Simulate a crash-resume: serialize → deserialize → fresh box/clock.
    restored = PipelineState.model_validate_json(state.model_dump_json())
    assert restored.cycle_timebox_active is True
    assert restored.cycle_timebox_consumed_seconds == 80.0

    box2: list[float | None] = [None]
    clock2 = [90.0]  # 10s passed during restart

    # First step after resume: delta=0 (no prior sample), total_elapsed=80.
    # Advance to development_commit_cleanup.
    next_state = _runner_sim_step(restored, "agent_success", custom, box2, clock2)
    assert next_state.cycle_timebox_consumed_seconds == 80.0  # delta was 0

    # Second step after resume: 20s more, total should be 100.
    clock2[0] = 110.0
    next_state = _runner_sim_step(next_state, "agent_success", custom, box2, clock2)
    assert next_state.cycle_timebox_consumed_seconds == 100.0

    # Continue to 210s total (> 200s deadline) → redirect on next loopback.
    clock2[0] = 210.0
    next_state = _runner_sim_step(
        next_state,
        AnalysisDecisionEvent(phase="development_analysis", decision="request_changes"),
        custom,
        box2,
        clock2,
    )
    assert next_state.phase == "development_final_commit_cleanup"
    assert next_state.cycle_timebox_active is False


def test_new_cycle_starts_after_final_commit() -> None:
    """After the final-commit path clears timing, a new planning→dev cycle restarts."""
    custom = _custom_policy(duration=500.0)
    box: list[float | None] = [None]
    clock = [0.0]

    # First cycle: start, accumulate, end via final-commit.
    state = _runner_sim_step(
        _state("planning_analysis"), "analysis_success", custom, box, clock
    )
    clock[0] = 100.0
    state = _runner_sim_step(state, "agent_success", custom, box, clock)
    assert state.cycle_timebox_consumed_seconds == 100.0

    # Enter final-commit cleanup → timing cleared.
    clock[0] = 110.0
    state = _runner_sim_step(
        state,
        AnalysisDecisionEvent(phase="development_analysis", decision="completed"),
        custom,
        box,
        clock,
    )
    assert state.phase == "development_final_commit_cleanup"
    assert state.cycle_timebox_active is False
    assert state.cycle_timebox_consumed_seconds == 110.0  # preserved through conclusion + fold

    # Simulate post-commit phases cycling back to planning_analysis → development.
    # The final-commit and post-commit phases are not the guarded entry, so
    # timing stays stopped. Eventually a new planning_analysis → development
    # handoff starts a fresh cycle with consumed=0.
    # We construct a state at planning_analysis with timing stopped:
    post_state = _state("planning_analysis")  # fresh, inactive
    clock[0] = 5000.0  # Much later — previous consumed time is irrelevant
    new_dev = _runner_sim_step(post_state, "analysis_success", custom, box, clock)
    assert new_dev.phase == "development"
    assert new_dev.cycle_timebox_active is True
    assert new_dev.cycle_timebox_consumed_seconds == 0.0  # fresh cycle


# ---------------------------------------------------------------------------
# DA-015: distinct start_entry vs guarded_entry
# ---------------------------------------------------------------------------


def test_distinct_start_entry_starts_timer_separately_from_guarded_entry() -> None:
    """When start_entry != guarded_entry, timer starts at start_entry entry."""
    from ralph.policy.models import CycleTimeboxPolicy

    policy = _policy().model_copy(
        update={
            "cycle_timebox": CycleTimeboxPolicy(
                duration_seconds=500.0,
                start_source="planning_analysis",
                start_entry="development",
                guarded_entry="development_analysis",
                end_entry="development_final_commit_cleanup",
                finalization_target="development_final_commit_cleanup",
            )
        }
    )
    # Enter development (start_entry) while inactive → timer starts.
    state = _state("planning_analysis")
    next_state, _ = reduce(state, "analysis_success", policy, routing_timing=_rt(0.0))
    assert next_state.phase == "development"
    assert next_state.cycle_timebox_active is True
    assert next_state.cycle_timebox_consumed_seconds == 0.0

    # Timer was started by start_entry; routing to end_entry concludes it.
    # This proves start_entry and end_entry are distinct and functional.
    concluded = _state(
        "development_analysis",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=400.0,
    )
    # analysis_success from development_analysis routes to final-commit.
    end_state, _ = reduce(
        concluded, "analysis_success", policy, routing_timing=_rt(400.0)
    )
    assert end_state.phase == "development_final_commit_cleanup"
    assert end_state.cycle_timebox_active is False
    # Consumed is preserved through conclusion.
    assert end_state.cycle_timebox_consumed_seconds == 400.0


# ---------------------------------------------------------------------------
# DA-016: legacy resume initialization
# ---------------------------------------------------------------------------


def test_initialize_legacy_cycle_on_resume_starts_at_start_entry() -> None:
    """A legacy checkpoint at start_entry gets the timer initialized on resume."""
    from ralph.pipeline.cycle_timing import initialize_legacy_cycle_on_resume

    policy = _policy()
    # Legacy state: no cycle fields set (defaults), sitting at development.
    legacy = _state("development")
    assert legacy.cycle_timebox_active is False
    assert legacy.cycle_timebox_consumed_seconds == 0.0

    result = initialize_legacy_cycle_on_resume(legacy, policy)
    assert result.cycle_timebox_active is True
    assert result.cycle_timebox_consumed_seconds == 0.0


def test_initialize_legacy_cycle_on_resume_noop_when_already_active() -> None:
    """If cycle is already active, resume init must not reset it."""
    from ralph.pipeline.cycle_timing import initialize_legacy_cycle_on_resume

    policy = _policy()
    state = _state(
        "development", cycle_timebox_active=True, cycle_timebox_consumed_seconds=500.0
    )
    result = initialize_legacy_cycle_on_resume(state, policy)
    assert result.cycle_timebox_active is True
    assert result.cycle_timebox_consumed_seconds == 500.0


def test_initialize_legacy_cycle_on_resume_noop_outside_cycle_phases() -> None:
    """A legacy checkpoint at a non-cycle phase is left untouched."""
    from ralph.pipeline.cycle_timing import initialize_legacy_cycle_on_resume

    policy = _policy()
    legacy = _state("planning")
    result = initialize_legacy_cycle_on_resume(legacy, policy)
    assert result.cycle_timebox_active is False
    assert result.cycle_timebox_consumed_seconds == 0.0
