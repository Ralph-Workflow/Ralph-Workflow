from __future__ import annotations

from pathlib import Path

from ralph.pipeline.cycle_timing import RoutingTiming
from ralph.pipeline.events import AnalysisDecisionEvent, PhaseFailureEvent, PipelineEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.policy.models import PhaseWorkflowFallback

_DEFAULTS_DIR = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"


def _policy():
    return load_policy(_DEFAULTS_DIR).pipeline


def _rt(elapsed: float) -> RoutingTiming:
    return RoutingTiming(total_elapsed_seconds=elapsed)


def _state(phase: str, **overrides: object) -> PipelineState:
    return PipelineState(phase=phase, **overrides)


def test_planning_time_does_not_start_timer() -> None:
    policy = _policy()
    state = _state("planning")
    next_state, _ = reduce(state, "agent_success", policy, routing_timing=_rt(10_000.0))
    assert next_state.phase == "planning_analysis"
    assert next_state.cycle_timebox_active is False


def test_timer_starts_on_planning_to_development_handoff() -> None:
    policy = _policy()
    state = _state("planning_analysis")
    next_state, _ = reduce(state, "analysis_success", policy, routing_timing=_rt(0.0))
    assert next_state.phase == "development"
    assert next_state.cycle_timebox_active is True
    assert next_state.cycle_timebox_consumed_seconds == 0.0


def test_no_reset_on_intermediate_activity() -> None:
    policy = _policy()
    state = _state(
        "development_commit_review",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=500.0,
    )
    next_state, _ = reduce(state, "agent_success", policy, routing_timing=_rt(500.0))
    assert next_state.cycle_timebox_active is True
    assert next_state.cycle_timebox_consumed_seconds == 500.0


def test_entering_final_commit_path_preserves_cycle_timing() -> None:
    policy = _policy()
    state = _state(
        "development_analysis",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=3000.0,
    )
    event = AnalysisDecisionEvent(phase="development_analysis", decision="completed")
    next_state, _ = reduce(state, event, policy, routing_timing=_rt(3000.0))
    assert next_state.phase == "development_final_commit_review"
    assert next_state.cycle_timebox_active is True
    assert next_state.cycle_timebox_consumed_seconds == 3000.0


def test_default_cycle_timer_ends_only_on_entry_to_final_commit() -> None:
    policy = _policy()
    cleanup = _state(
        "development_final_commit_review",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=3000.0,
    )
    restored = PipelineState.model_validate_json(cleanup.model_dump_json())
    assert restored.cycle_timebox_active is True
    assert restored.cycle_timebox_consumed_seconds == 3000.0

    final_commit, _ = reduce(
        restored,
        PipelineEvent.AGENT_SUCCESS,
        policy,
        routing_timing=_rt(3000.0),
    )
    assert final_commit.phase == "development_final_commit"
    assert final_commit.cycle_timebox_active is False
    assert final_commit.cycle_timebox_consumed_seconds == 3000.0


def _request_changes_to_development(state: PipelineState, elapsed: float) -> PipelineState:
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
        cycle_timebox_consumed_seconds=36000.0,
    )
    next_state = _request_changes_to_development(state, 36000.0)
    assert next_state.phase == "development_final_commit_review"
    assert next_state.cycle_timebox_active is True


def test_development_reentry_redirected_after_deadline() -> None:
    state = _state(
        "development_analysis",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=36001.0,
    )
    next_state = _request_changes_to_development(state, 36001.0)
    assert next_state.phase == "development_final_commit_review"
    assert next_state.cycle_timebox_active is True


def test_custom_deadline_keeps_redirect_at_new_boundary() -> None:
    policy = _policy()
    from ralph.policy.models import CycleTimeboxPolicy

    custom = policy.model_copy(
        update={
            "cycle_timebox": CycleTimeboxPolicy(
                duration_seconds=600.0,
                start_source="planning_analysis",
                start_entry="development",
                guarded_entry="development",
                end_entry="development_final_commit",
                finalization_target="development_final_commit_review",
            )
        }
    )
    state = _state(
        "development_analysis",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=600.0,
    )
    event = AnalysisDecisionEvent(phase="development_analysis", decision="request_changes")
    next_state, _ = reduce(state, event, custom, routing_timing=_rt(600.0))
    assert next_state.phase == "development_final_commit_review"
    assert custom.cycle_timebox.warning_threshold_seconds == 480.0


def test_first_development_entry_always_permitted_even_if_clock_advanced() -> None:
    state = _state("planning_analysis")
    policy = _policy()
    next_state, _ = reduce(state, "analysis_success", policy, routing_timing=_rt(99_999.0))
    assert next_state.phase == "development"
    assert next_state.cycle_timebox_active is True


def _policy_with_development_fallback():
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
    policy = _policy_with_development_fallback()
    state = _state(
        "development_analysis",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=36000.0,
    )
    event = PhaseFailureEvent(
        phase="development_analysis",
        reason="non-recoverable handler failure",
        recoverable=False,
    )
    next_state, _ = reduce(state, event, policy, routing_timing=_rt(36000.0))
    assert next_state.phase == "development_final_commit_review"
    assert next_state.cycle_timebox_active is True


def test_phase_failure_workflow_fallback_permitted_before_deadline() -> None:
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
    policy = _policy_with_development_fallback()
    state = _state(
        "development_analysis",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=36000.0,
        phase_chains={
            "development_analysis": AgentChainState(agents=["claude"], current_index=0, retries=3)
        },
    )
    next_state, _ = reduce(state, PipelineEvent.AGENT_FAILURE, policy, routing_timing=_rt(36000.0))
    assert next_state.phase == "development_final_commit_review"
    assert next_state.cycle_timebox_active is True


def test_timer_does_not_start_on_unrelated_route_into_guarded_phase() -> None:
    from ralph.pipeline.cycle_timing import apply_cycle_timebox

    policy = _policy()
    state = _state("development_commit")
    decision = apply_cycle_timebox(state, "development", policy=policy, routing_timing=_rt(0.0))
    assert decision.timing_started is False
    assert decision.state.cycle_timebox_active is False
    assert decision.redirected is False
    assert decision.target_phase == "development"


def test_timer_starts_only_from_declared_start_source() -> None:
    from ralph.pipeline.cycle_timing import apply_cycle_timebox

    policy = _policy()
    state = _state("planning_analysis")
    decision = apply_cycle_timebox(state, "development", policy=policy, routing_timing=_rt(0.0))
    assert decision.timing_started is True
    assert decision.state.cycle_timebox_active is True


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
    legacy = _state("development", cycle_timebox_active=False)
    import json

    data = json.loads(legacy.model_dump_json())
    data.pop("cycle_timebox_active", None)
    data.pop("cycle_timebox_consumed_seconds", None)
    restored = PipelineState.model_validate(data)
    assert restored.cycle_timebox_active is False
    assert restored.cycle_timebox_consumed_seconds == 0.0


def _runner_sim_step(
    state: PipelineState,
    event: str | object,
    policy: object,
    box: list[float | None],
    fake_clock: list[float],
) -> PipelineState:
    from dataclasses import dataclass

    from ralph.pipeline.runner import fold_cycle_elapsed, sample_cycle_timing

    @dataclass
    class _ClockDeps:
        monotonic: object
        wall_time: object

    deps = _ClockDeps(
        monotonic=lambda: fake_clock[0],
        wall_time=lambda: fake_clock[0],
    )
    routing_timing, now, delta, ct_policy = sample_cycle_timing(state, policy, deps, box)
    next_state, _ = reduce(state, event, policy, routing_timing=routing_timing)
    next_state = fold_cycle_elapsed(
        state,
        next_state,
        delta_seconds=delta,
        timing_enabled=box is not None and ct_policy is not None,
    )
    if now is not None:
        box[0] = now
    return next_state


def _custom_policy(duration: float = 100.0):
    from ralph.policy.models import CycleTimeboxPolicy

    policy = _policy()
    return policy.model_copy(
        update={
            "cycle_timebox": CycleTimeboxPolicy(
                duration_seconds=duration,
                start_source="planning_analysis",
                start_entry="development",
                guarded_entry="development",
                end_entry="development_final_commit",
                finalization_target="development_final_commit_review",
            )
        }
    )


def test_per_step_fold_accumulates_consumed_time_across_steps() -> None:
    custom = _custom_policy(duration=100.0)
    box: list[float | None] = [None]
    clock = [0.0]

    state = _runner_sim_step(_state("planning_analysis"), "analysis_success", custom, box, clock)
    assert state.phase == "development"
    assert state.cycle_timebox_active is True
    assert state.cycle_timebox_consumed_seconds == 0.0

    clock[0] = 40.0
    state = _runner_sim_step(state, "agent_success", custom, box, clock)
    assert state.cycle_timebox_active is True
    assert state.cycle_timebox_consumed_seconds == 40.0

    clock[0] = 70.0
    state = _runner_sim_step(state, "agent_success", custom, box, clock)
    assert state.cycle_timebox_active is True
    assert state.cycle_timebox_consumed_seconds == 70.0

    clock[0] = 105.0
    state = _runner_sim_step(
        state,
        AnalysisDecisionEvent(phase="development_analysis", decision="request_changes"),
        custom,
        box,
        clock,
    )
    assert state.phase == "development_final_commit_review"
    assert state.cycle_timebox_active is True


def test_consumed_time_survives_checkpoint_roundtrip_and_continues() -> None:
    custom = _custom_policy(duration=200.0)
    box: list[float | None] = [None]
    clock = [0.0]

    state = _runner_sim_step(_state("planning_analysis"), "analysis_success", custom, box, clock)
    clock[0] = 80.0
    state = _runner_sim_step(state, "agent_success", custom, box, clock)
    assert state.cycle_timebox_consumed_seconds == 80.0

    restored = PipelineState.model_validate_json(state.model_dump_json())
    assert restored.cycle_timebox_active is True
    assert restored.cycle_timebox_consumed_seconds == 80.0

    box2: list[float | None] = [None]
    clock2 = [90.0]

    next_state = _runner_sim_step(restored, "agent_success", custom, box2, clock2)
    assert next_state.cycle_timebox_consumed_seconds == 90.0

    clock2[0] = 110.0
    next_state = _runner_sim_step(next_state, "agent_success", custom, box2, clock2)
    assert next_state.cycle_timebox_consumed_seconds == 110.0

    clock2[0] = 210.0
    next_state = _runner_sim_step(
        next_state,
        AnalysisDecisionEvent(phase="development_analysis", decision="request_changes"),
        custom,
        box2,
        clock2,
    )
    assert next_state.phase == "development_final_commit_review"
    assert next_state.cycle_timebox_active is True


def test_new_cycle_starts_after_final_commit() -> None:
    custom = _custom_policy(duration=500.0)
    box: list[float | None] = [None]
    clock = [0.0]

    state = _runner_sim_step(_state("planning_analysis"), "analysis_success", custom, box, clock)
    clock[0] = 100.0
    state = _runner_sim_step(state, "agent_success", custom, box, clock)
    assert state.cycle_timebox_consumed_seconds == 100.0

    clock[0] = 110.0
    state = _runner_sim_step(
        state,
        AnalysisDecisionEvent(phase="development_analysis", decision="completed"),
        custom,
        box,
        clock,
    )
    assert state.phase == "development_final_commit_review"
    assert state.cycle_timebox_active is True
    assert state.cycle_timebox_consumed_seconds == 110.0

    state = _runner_sim_step(state, "agent_success", custom, box, clock)
    assert state.phase == "development_final_commit"
    assert state.cycle_timebox_active is False

    post_state = _state("planning_analysis")
    clock[0] = 5000.0
    new_dev = _runner_sim_step(post_state, "analysis_success", custom, box, clock)
    assert new_dev.phase == "development"
    assert new_dev.cycle_timebox_active is True
    assert new_dev.cycle_timebox_consumed_seconds == 0.0


def test_distinct_start_entry_starts_timer_separately_from_guarded_entry() -> None:
    from ralph.policy.models import CycleTimeboxPolicy

    policy = _policy().model_copy(
        update={
            "cycle_timebox": CycleTimeboxPolicy(
                duration_seconds=500.0,
                start_source="planning_analysis",
                start_entry="development",
                guarded_entry="development_analysis",
                end_entry="development_final_commit_review",
                finalization_target="development_final_commit_review",
            )
        }
    )
    state = _state("planning_analysis")
    next_state, _ = reduce(state, "analysis_success", policy, routing_timing=_rt(0.0))
    assert next_state.phase == "development"
    assert next_state.cycle_timebox_active is True
    assert next_state.cycle_timebox_consumed_seconds == 0.0

    concluded = _state(
        "development_analysis",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=400.0,
    )
    end_state, _ = reduce(concluded, "analysis_success", policy, routing_timing=_rt(400.0))
    assert end_state.phase == "development_final_commit_review"
    assert end_state.cycle_timebox_active is False
    assert end_state.cycle_timebox_consumed_seconds == 400.0


def test_initialize_legacy_cycle_on_resume_starts_at_start_entry() -> None:
    from ralph.pipeline.cycle_timing import initialize_legacy_cycle_on_resume

    policy = _policy()
    legacy = _state("development")
    assert legacy.cycle_timebox_active is False
    assert legacy.cycle_timebox_consumed_seconds == 0.0

    result = initialize_legacy_cycle_on_resume(legacy, policy)
    assert result.cycle_timebox_active is True
    assert result.cycle_timebox_consumed_seconds == 0.0


def test_initialize_legacy_cycle_on_resume_noop_when_already_active() -> None:
    from ralph.pipeline.cycle_timing import initialize_legacy_cycle_on_resume

    policy = _policy()
    state = _state(
        "development",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=500.0,
        cycle_timebox_started_at_epoch=1000.0,
    )
    result = initialize_legacy_cycle_on_resume(state, policy)
    assert result.cycle_timebox_active is True
    assert result.cycle_timebox_consumed_seconds == 500.0


def test_initialize_legacy_cycle_on_resume_noop_outside_cycle_phases() -> None:
    from ralph.pipeline.cycle_timing import initialize_legacy_cycle_on_resume

    policy = _policy()
    legacy = _state("planning")
    result = initialize_legacy_cycle_on_resume(legacy, policy)
    assert result.cycle_timebox_active is False
    assert result.cycle_timebox_consumed_seconds == 0.0


def test_legacy_resume_times_a_cycle_resumed_mid_loop() -> None:
    from ralph.pipeline.cycle_timing import initialize_legacy_cycle_on_resume

    policy = _policy()

    for phase in ("development_analysis", "development_commit", "development_commit_review"):
        result = initialize_legacy_cycle_on_resume(_state(phase), policy)
        assert result.cycle_timebox_active is True, phase
        assert result.cycle_timebox_consumed_seconds == 0.0, phase


def test_legacy_resume_leaves_pre_cycle_phases_untimed() -> None:
    from ralph.pipeline.cycle_timing import initialize_legacy_cycle_on_resume

    policy = _policy()

    for phase in ("planning", "planning_analysis", "complete", "failed_terminal"):
        result = initialize_legacy_cycle_on_resume(_state(phase), policy)
        assert result.cycle_timebox_active is False, phase


def test_legacy_resume_preserves_cleanup_but_not_final_commit_timing() -> None:
    from ralph.pipeline.cycle_timing import initialize_legacy_cycle_on_resume

    policy = _policy()

    cleanup = initialize_legacy_cycle_on_resume(_state("development_final_commit_review"), policy)
    final_commit = initialize_legacy_cycle_on_resume(_state("development_final_commit"), policy)
    assert cleanup.cycle_timebox_active is True
    assert final_commit.cycle_timebox_active is False


def test_pre_cycle_phase_reached_by_a_decision_is_outside_the_cycle() -> None:
    from ralph.pipeline.cycle_timing import initialize_legacy_cycle_on_resume
    from ralph.policy.models import PhaseDecisionRoute

    base = _policy()
    planning_analysis = base.phases["planning_analysis"]
    policy = base.model_copy(
        update={
            "phases": {
                **base.phases,
                "planning_refine": base.phases["planning"].model_copy(
                    update={"transitions": base.phases["planning"].transitions}
                ),
                "planning_analysis": planning_analysis.model_copy(
                    update={
                        "decisions": {
                            **planning_analysis.decisions,
                            "request_changes": PhaseDecisionRoute(
                                target="planning_refine", reset_loop=False
                            ),
                        }
                    }
                ),
            }
        }
    )

    resumed = initialize_legacy_cycle_on_resume(_state("planning_refine"), policy)

    assert resumed.cycle_timebox_active is False
