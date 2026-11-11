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
    state, _effects = reducer_reduce(
        _in_cycle("development_commit"),
        PipelineEvent.COMMIT_FAILURE,
        _pipeline(),
        routing_timing=RoutingTiming(total_elapsed_seconds=_CONSUMED),
    )

    assert state.cycle_timebox_active is True


def test_the_deadline_still_redirects_after_a_commit_failure() -> None:
    failed, _effects = reducer_reduce(
        _in_cycle("development_commit"),
        PipelineEvent.COMMIT_FAILURE,
        _pipeline(),
        routing_timing=RoutingTiming(total_elapsed_seconds=_CONSUMED),
    )
    resumed = failed.copy_with(phase="development_analysis")

    redirected, _effects = reducer_reduce(
        resumed,
        PipelineEvent.ANALYSIS_LOOPBACK,
        _pipeline(),
        routing_timing=RoutingTiming(total_elapsed_seconds=36_000.0),
    )

    assert redirected.phase == "development_final_commit_review"
    assert redirected.cycle_timebox_redirect_reason is not None


def test_recovering_a_missing_plan_handoff_preserves_the_cycle(tmp_path: Path) -> None:
    recovered = recover_missing_plan_handoff(
        state=_in_cycle("development", consumed=7300.0),
        pipeline_policy=_pipeline(),
        checkpoint_path=tmp_path / "checkpoint.json",
        subscriber=None,
        exc=MissingPlanHandoffError("no plan handoff at .agent/PLAN.md"),
    )

    assert recovered.phase == _pipeline().entry_phase
    assert recovered.cycle_timebox_active is True


def test_the_cycle_after_a_missing_plan_handoff_keeps_its_budget(
    tmp_path: Path,
) -> None:
    recovered = recover_missing_plan_handoff(
        state=_in_cycle("development", consumed=7300.0),
        pipeline_policy=_pipeline(),
        checkpoint_path=tmp_path / "checkpoint.json",
        subscriber=None,
        exc=MissingPlanHandoffError("no plan handoff at .agent/PLAN.md"),
    )
    spent = recovered.with_loop_iteration("planning_analysis_iteration", 3)

    advanced, _effects = reducer_reduce(
        spent,
        PipelineEvent.AGENT_SUCCESS,
        _pipeline(),
        routing_timing=RoutingTiming(total_elapsed_seconds=0.0),
    )

    assert advanced.phase == "development"
    assert advanced.cycle_timebox_consumed_seconds == pytest.approx(7300.0)


def test_a_recovery_re_entry_is_still_bound_by_the_deadline() -> None:
    from ralph.pipeline.cycle_timing import apply_cycle_timebox

    expired = _in_cycle("failed_terminal", consumed=36_000.0)

    decision = apply_cycle_timebox(
        expired,
        "development",
        policy=_pipeline(),
        routing_timing=RoutingTiming(total_elapsed_seconds=36_000.0),
    )

    assert decision.target_phase == "development_final_commit_review"
    assert decision.redirected is True


def test_the_runtime_re_entry_applies_that_decision(tmp_path: Path) -> None:
    from ralph.pipeline import runner as runner_module
    from ralph.pipeline.effects import PreparePromptEffect
    from ralph.workspace.scope import WorkspaceScope

    (tmp_path / ".agent").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".agent" / "PLAN.md").write_text("# Plan\n", encoding="utf-8")
    (tmp_path / "PROMPT.md").write_text("Do the work\n", encoding="utf-8")
    expired = _in_cycle("failed_terminal", consumed=36_000.0).copy_with(
        previous_phase="development"
    )

    updated = runner_module._handle_inline_effect(
        effect=PreparePromptEffect(phase="development", drain="development"),
        state=expired,
        pipeline_policy=_pipeline(),
        artifacts_policy=load_policy(_DEFAULTS_DIR).artifacts,
        workspace_scope=WorkspaceScope(tmp_path),
    )

    assert updated.phase == "development_final_commit_review"
    assert updated.cycle_timebox_active is True


@pytest.mark.parametrize(
    ("target_phase", "dev_active", "cycle_active"),
    [
        ("development_commit", False, True),
        ("development_final_commit", True, False),
    ],
)
def test_recovery_reentry_applies_each_independent_timer_boundary(
    tmp_path: Path,
    target_phase: str,
    dev_active: bool,
    cycle_active: bool,
) -> None:
    from ralph.pipeline import runner as runner_module
    from ralph.pipeline.effects import PreparePromptEffect
    from ralph.workspace.scope import WorkspaceScope

    (tmp_path / ".agent").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".agent" / "PLAN.md").write_text("# Plan\n", encoding="utf-8")
    (tmp_path / "PROMPT.md").write_text("Do the work\n", encoding="utf-8")
    state = _in_cycle("failed_terminal").copy_with(
        previous_phase="development",
        dev_timebox_active=True,
        dev_timebox_consumed_seconds=4200.0,
    )

    updated = runner_module._handle_inline_effect(
        effect=PreparePromptEffect(phase=target_phase, drain="development_commit"),
        state=state,
        pipeline_policy=_pipeline(),
        artifacts_policy=load_policy(_DEFAULTS_DIR).artifacts,
        workspace_scope=WorkspaceScope(tmp_path),
    )

    assert updated.phase == target_phase
    assert updated.dev_timebox_active is dev_active
    assert updated.cycle_timebox_active is cycle_active


def test_a_deadline_redirect_is_announced_on_the_routing_log() -> None:
    from loguru import logger

    records: list[str] = []
    sink_id = logger.add(lambda message: records.append(str(message)), level="WARNING")
    try:
        reducer_reduce(
            _in_cycle("development_analysis", consumed=36_000.0),
            PipelineEvent.ANALYSIS_LOOPBACK,
            _pipeline(),
            routing_timing=RoutingTiming(total_elapsed_seconds=36_000.0),
        )
    finally:
        logger.remove(sink_id)

    assert any("cycle timebox reached" in record for record in records)


def test_routing_from_final_commit_to_terminal_does_not_reset_a_stale_timer() -> None:
    spent = PipelineState(
        phase="development_final_commit",
        budget_caps={"iteration": 1},
        outer_progress={"iteration": 1},
        pending_cycle_outcome="completed",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=_CONSUMED,
    )

    ended, _effects = reducer_reduce(
        spent,
        PipelineEvent.COMMIT_SUCCESS,
        _pipeline(),
        routing_timing=RoutingTiming(total_elapsed_seconds=_CONSUMED),
    )

    assert ended.phase in _pipeline().terminal_states()
    assert ended.cycle_timebox_active is True


def test_a_redirected_recovery_hop_prepares_the_phase_it_actually_enters(
    tmp_path: Path,
) -> None:
    from loguru import logger

    from ralph.pipeline import runner as runner_module
    from ralph.pipeline.effects import PreparePromptEffect
    from ralph.workspace.scope import WorkspaceScope

    (tmp_path / ".agent").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".agent" / "PLAN.md").write_text("# Plan\n", encoding="utf-8")
    (tmp_path / "PROMPT.md").write_text("Do the work\n", encoding="utf-8")
    bundle = load_policy(_DEFAULTS_DIR)
    expired = _in_cycle("failed_terminal", consumed=36_000.0).copy_with(
        previous_phase="development"
    )

    records: list[str] = []
    sink_id = logger.add(lambda message: records.append(str(message)), level="WARNING")
    try:
        updated = runner_module._handle_inline_effect(
            effect=PreparePromptEffect(phase="development", drain="development"),
            state=expired,
            pipeline_policy=bundle.pipeline,
            artifacts_policy=bundle.artifacts,
            workspace_scope=WorkspaceScope(tmp_path),
        )
    finally:
        logger.remove(sink_id)

    target = "development_final_commit_review"
    assert updated.phase == target
    assert updated.current_drain == bundle.pipeline.phases[target].drain
    assert any("cycle timebox reached" in record for record in records)


def test_the_recovery_hop_judges_the_deadline_on_the_sampled_clock(
    tmp_path: Path,
) -> None:
    from ralph.pipeline import runner as runner_module
    from ralph.pipeline.effects import PreparePromptEffect
    from ralph.workspace.scope import WorkspaceScope

    (tmp_path / ".agent").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".agent" / "PLAN.md").write_text("# Plan\n", encoding="utf-8")
    (tmp_path / "PROMPT.md").write_text("Do the work\n", encoding="utf-8")
    bundle = load_policy(_DEFAULTS_DIR)
    nearly_spent = _in_cycle("failed_terminal", consumed=35900.0).copy_with(
        previous_phase="development"
    )

    updated = runner_module._handle_inline_effect(
        effect=PreparePromptEffect(phase="development", drain="development"),
        state=nearly_spent,
        pipeline_policy=bundle.pipeline,
        artifacts_policy=bundle.artifacts,
        workspace_scope=WorkspaceScope(tmp_path),
        routing_timing=RoutingTiming(total_elapsed_seconds=36100.0),
    )

    assert updated.phase == "development_final_commit_review"


def test_a_resumed_concluded_cycle_is_not_re_armed(tmp_path: Path) -> None:
    from ralph.pipeline.cycle_timing import initialize_legacy_cycle_on_resume

    concluded = PipelineState(
        phase="development",
        cycle_timebox_active=False,
        cycle_timebox_consumed_seconds=36000.0,
    )

    resumed = initialize_legacy_cycle_on_resume(concluded, _pipeline())

    assert resumed.cycle_timebox_active is False
    assert resumed.cycle_timebox_consumed_seconds == 36000.0


def test_the_banner_never_shows_negative_time_remaining() -> None:
    from ralph.pipeline.cycle_timing import cycle_timebox_status_item

    item = cycle_timebox_status_item(
        _in_cycle("development", consumed=9000.0),
        policy=_pipeline(),
    )

    assert item is not None
    assert "-" not in str(item)
