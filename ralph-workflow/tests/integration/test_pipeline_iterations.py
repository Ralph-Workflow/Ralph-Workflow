"""Integration tests for persisted development/review progress invariants.

These tests drive ``runner.run()`` in-process with a mocked agent execution seam
so the reducer, checkpoint save path, and policy routing can be exercised without
real subprocesses.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

from ralph.config.enums import Verbosity
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline import runner
from ralph.pipeline.checkpoint import load as ckpt_load
from ralph.pipeline.checkpoint import save as ckpt_save
from ralph.pipeline.effects import CommitEffect, ExitSuccessEffect, InvokeAgentEffect
from ralph.pipeline.events import AnalysisDecisionEvent, PipelineEvent
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.workspace.scope import WorkspaceScope
from tests.integration.test_pipeline_happy_path import MockAgentInvoker

if TYPE_CHECKING:
    from pytest import MonkeyPatch

    from ralph.workspace.memory import MemoryWorkspace

DEFAULT_POLICY_DIR = Path(__file__).parent.parent.parent / "ralph" / "policy" / "defaults"
DEVELOPMENT_CYCLES_TWO = 2
DEVELOPMENT_CYCLES_THREE = 3
REVIEW_CYCLES_TWO = 2
MAX_REVIEW_ANALYSIS_ITERATIONS = 2
MAX_PLANNING_ANALYSIS_ITERATIONS = 3


def _install_runner_display_context(monkeypatch: MonkeyPatch) -> None:
    console = Console(record=True, force_terminal=False, width=120, color_system=None)
    ctx = make_display_context(console=console, force_width=120, force_mode="wide")
    monkeypatch.setattr(runner, "make_display_context", lambda **_kwargs: ctx)


class LoopbackOnceInvoker(MockAgentInvoker):
    """Return a single development-analysis loopback before succeeding."""

    def __init__(self, workspace: MemoryWorkspace) -> None:
        super().__init__(workspace)
        self._development_analysis_calls = 0
        self.last_phase: str | None = None

    def invoke(self, agent_name: str, phase: str) -> PipelineEvent:
        self.last_phase = phase
        return super().invoke(agent_name, phase)

    def analysis_event_for(self, phase: str) -> PipelineEvent:
        if phase == "development_analysis":
            self._development_analysis_calls += 1
            if self._development_analysis_calls == 1:
                return PipelineEvent.ANALYSIS_LOOPBACK
        return PipelineEvent.ANALYSIS_SUCCESS


class ReviewLoopbackOnceInvoker(MockAgentInvoker):
    """Return a single review-analysis loopback before approval."""

    def __init__(self, workspace: MemoryWorkspace) -> None:
        super().__init__(workspace)
        self._review_analysis_calls = 0
        self.last_phase: str | None = None

    def invoke(self, agent_name: str, phase: str) -> PipelineEvent:
        self.last_phase = phase
        return super().invoke(agent_name, phase)

    def analysis_event_for(self, phase: str) -> PipelineEvent:
        if phase == "review_analysis":
            self._review_analysis_calls += 1
            if self._review_analysis_calls == 1:
                return PipelineEvent.ANALYSIS_LOOPBACK
        return PipelineEvent.ANALYSIS_SUCCESS


class ReviewLoopbackToCapThenApproveInvoker(MockAgentInvoker):
    """Request review loopback through the cap, then approve after the final fix pass."""

    def __init__(self, workspace: MemoryWorkspace) -> None:
        super().__init__(workspace)
        self.last_phase: str | None = None
        self._review_analysis_calls = 0

    def invoke(self, agent_name: str, phase: str) -> PipelineEvent:
        self.last_phase = phase
        return super().invoke(agent_name, phase)

    def analysis_event_for(self, phase: str) -> PipelineEvent:
        if phase == "review_analysis":
            self._review_analysis_calls += 1
            if self._review_analysis_calls <= MAX_REVIEW_ANALYSIS_ITERATIONS:
                return PipelineEvent.ANALYSIS_LOOPBACK
        return PipelineEvent.ANALYSIS_SUCCESS


class ReviewCommitSkippedInvoker(MockAgentInvoker):
    """Skip the review commit while succeeding everywhere else."""

    def __init__(self, workspace: MemoryWorkspace) -> None:
        super().__init__(workspace)
        self.last_phase: str | None = None

    def invoke(self, agent_name: str, phase: str) -> PipelineEvent:
        self.last_phase = phase
        return super().invoke(agent_name, phase)

    def commit_event_for(self, phase: str | None) -> PipelineEvent:
        if phase == "review_commit":
            return PipelineEvent.COMMIT_SKIPPED
        return PipelineEvent.COMMIT_SUCCESS


class PlanningAnalysisRequestChangesOnceInvoker(MockAgentInvoker):
    """Request planning changes once, then approve if planning_analysis is re-entered."""

    def __init__(self, workspace: MemoryWorkspace) -> None:
        super().__init__(workspace)
        self.last_phase: str | None = None
        self._planning_analysis_calls = 0

    def invoke(self, agent_name: str, phase: str) -> PipelineEvent:
        self.last_phase = phase
        return super().invoke(agent_name, phase)

    def analysis_event_for(self, phase: str) -> AnalysisDecisionEvent | PipelineEvent:
        if phase == "planning_analysis":
            self._planning_analysis_calls += 1
            if self._planning_analysis_calls == 1:
                return AnalysisDecisionEvent(
                    phase="planning_analysis", decision="request_changes"
                )
            return AnalysisDecisionEvent(phase="planning_analysis", decision="completed")
        return PipelineEvent.ANALYSIS_SUCCESS


class DevelopmentAnalysisAlwaysLoopbackInvoker(MockAgentInvoker):
    """Force every development analysis run to request changes."""

    def __init__(self, workspace: MemoryWorkspace) -> None:
        super().__init__(workspace)
        self.last_phase: str | None = None

    def invoke(self, agent_name: str, phase: str) -> PipelineEvent:
        self.last_phase = phase
        return super().invoke(agent_name, phase)

    def analysis_event_for(self, phase: str) -> PipelineEvent:
        if phase == "development_analysis":
            return PipelineEvent.ANALYSIS_LOOPBACK
        return PipelineEvent.ANALYSIS_SUCCESS


def _config() -> UnifiedConfig:
    return UnifiedConfig()


def _run_pipeline(  # noqa: PLR0913
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    mock_agent_invoker: MockAgentInvoker,
    config: UnifiedConfig,
    initial_state: PipelineState | None = None,
    counter_overrides: dict[str, int] | None = None,
) -> tuple[int, list[PipelineState]]:
    saved_states: list[PipelineState] = []
    policy_bundle = load_policy(DEFAULT_POLICY_DIR)

    def fake_execute_effect(effect, _config, _workspace_scope):
        if isinstance(effect, InvokeAgentEffect):
            mock_agent_invoker.invoke(effect.agent_name, effect.phase)
            return PipelineEvent.AGENT_SUCCESS
        if isinstance(effect, CommitEffect):
            commit_event_for = getattr(mock_agent_invoker, "commit_event_for", None)
            if callable(commit_event_for):
                return commit_event_for(getattr(mock_agent_invoker, "last_phase", None))
            return PipelineEvent.COMMIT_SUCCESS
        msg = f"Unexpected effect type: {type(effect)!r}"
        raise AssertionError(msg)

    def fake_phase_event_after_agent_run(*, effect, **_kwargs):
        analysis_event_for = getattr(mock_agent_invoker, "analysis_event_for", None)
        analysis_phases = {"planning_analysis", "development_analysis", "review_analysis"}
        if callable(analysis_event_for) and effect.phase in analysis_phases:
            return analysis_event_for(effect.phase)
        return PipelineEvent.AGENT_SUCCESS

    def capture_saved_state(state: PipelineState) -> None:
        saved_states.append(state)

    monkeypatch.setattr(runner, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_path))
    monkeypatch.setattr(runner, "load_policy_or_die", lambda _path: policy_bundle)
    monkeypatch.setattr(runner, "_materialize_agent_prompt_if_needed", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "_execute_effect", fake_execute_effect)
    monkeypatch.setattr(runner, "_phase_event_after_agent_run", fake_phase_event_after_agent_run)
    monkeypatch.setattr(runner.ckpt, "save", capture_saved_state)
    _install_runner_display_context(monkeypatch)

    result = runner.run(
        config, initial_state=initial_state, verbosity=Verbosity.QUIET,
        counter_overrides=counter_overrides,
    )
    return result, saved_states


def _state_with_phase(saved_states: list[PipelineState], phase: str) -> PipelineState:
    for state in saved_states:
        if state.phase == phase:
            return state
    raise AssertionError(f"expected a saved state for phase {phase!r}")


def test_dev_runs_exactly_2_cycles_with_d2(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    mock_agent_invoker: MockAgentInvoker,
) -> None:
    result, saved_states = _run_pipeline(
        monkeypatch,
        tmp_path,
        mock_agent_invoker,
        _config(),
        counter_overrides={"iteration": DEVELOPMENT_CYCLES_TWO, "reviewer_pass": 0},
    )

    assert result == 0
    assert mock_agent_invoker.count_for("development") == DEVELOPMENT_CYCLES_TWO
    assert mock_agent_invoker.count_for("development_analysis") == DEVELOPMENT_CYCLES_TWO
    assert mock_agent_invoker.count_for("development_commit") == DEVELOPMENT_CYCLES_TWO
    final_state = saved_states[-1]
    assert final_state.phase == "complete"
    assert final_state.get_outer_progress("iteration") == DEVELOPMENT_CYCLES_TWO
    assert final_state.get_outer_progress("reviewer_pass") == 0
    assert final_state.get_loop_iteration("development_analysis_iteration") == 0
    assert final_state.get_loop_iteration("review_analysis_iteration") == 0
    assert final_state.get_budget_remaining("iteration") == 0
    assert final_state.get_budget_remaining("reviewer_pass") == 0


def test_dev_runs_exactly_3_cycles_with_d3(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    mock_agent_invoker: MockAgentInvoker,
) -> None:
    result, saved_states = _run_pipeline(
        monkeypatch,
        tmp_path,
        mock_agent_invoker,
        _config(),
        counter_overrides={"iteration": DEVELOPMENT_CYCLES_THREE, "reviewer_pass": 0},
    )

    assert result == 0
    assert mock_agent_invoker.count_for("development") == DEVELOPMENT_CYCLES_THREE
    assert mock_agent_invoker.count_for("development_analysis") == DEVELOPMENT_CYCLES_THREE
    assert mock_agent_invoker.count_for("development_commit") == DEVELOPMENT_CYCLES_THREE
    final_state = saved_states[-1]
    assert final_state.phase == "complete"
    assert final_state.get_outer_progress("iteration") == DEVELOPMENT_CYCLES_THREE
    assert final_state.get_loop_iteration("development_analysis_iteration") == 0
    assert final_state.get_budget_remaining("iteration") == 0


def test_review_runs_exactly_2_cycles_with_r2(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    mock_agent_invoker: MockAgentInvoker,
) -> None:
    result, saved_states = _run_pipeline(
        monkeypatch,
        tmp_path,
        mock_agent_invoker,
        _config(),
        counter_overrides={"iteration": 1, "reviewer_pass": REVIEW_CYCLES_TWO},
    )

    assert result == 0
    assert mock_agent_invoker.count_for("development") == 1
    assert mock_agent_invoker.count_for("review") == REVIEW_CYCLES_TWO
    final_state = saved_states[-1]
    assert final_state.phase == "complete"
    assert final_state.get_outer_progress("iteration") == 1
    assert final_state.get_outer_progress("reviewer_pass") == REVIEW_CYCLES_TWO
    assert final_state.get_loop_iteration("development_analysis_iteration") == 0
    assert final_state.get_loop_iteration("review_analysis_iteration") == 0
    assert final_state.get_budget_remaining("reviewer_pass") == 0


def test_no_review_when_reviewer_pass_cap_zero(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    mock_agent_invoker: MockAgentInvoker,
) -> None:
    result, saved_states = _run_pipeline(
        monkeypatch,
        tmp_path,
        mock_agent_invoker,
        _config(),
        counter_overrides={"iteration": DEVELOPMENT_CYCLES_TWO, "reviewer_pass": 0},
    )

    assert result == 0
    assert mock_agent_invoker.count_for("review") == 0
    assert mock_agent_invoker.count_for("review_analysis") == 0
    assert mock_agent_invoker.count_for("review_commit") == 0
    assert mock_agent_invoker.count_for("development") == DEVELOPMENT_CYCLES_TWO
    final_state = saved_states[-1]
    assert final_state.phase == "complete"
    assert final_state.get_outer_progress("iteration") == DEVELOPMENT_CYCLES_TWO
    assert final_state.get_outer_progress("reviewer_pass") == 0
    assert final_state.get_budget_remaining("reviewer_pass") == 0


def test_analysis_loopback_preserves_budget(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    memory_workspace: MemoryWorkspace,
) -> None:
    invoker = LoopbackOnceInvoker(memory_workspace)

    result, saved_states = _run_pipeline(
        monkeypatch,
        tmp_path,
        invoker,
        _config(),
        counter_overrides={"iteration": DEVELOPMENT_CYCLES_TWO, "reviewer_pass": 0},
    )

    assert result == 0
    assert invoker.count_for("development") == DEVELOPMENT_CYCLES_THREE
    assert invoker.count_for("development_commit") == DEVELOPMENT_CYCLES_TWO
    starting_budget = DEVELOPMENT_CYCLES_TWO
    loopback_state = next(
        state
        for state in saved_states
        if state.phase == "development"
        and state.previous_phase == "development_analysis"
        and state.get_loop_iteration("development_analysis_iteration") == 1
    )
    assert loopback_state.get_budget_remaining("iteration") == starting_budget
    final_state = saved_states[-1]
    assert final_state.get_outer_progress("iteration") == DEVELOPMENT_CYCLES_TWO
    assert final_state.get_loop_iteration("development_analysis_iteration") == 0


def test_review_analysis_loopback_is_persisted_as_inner_progress_only(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    memory_workspace: MemoryWorkspace,
) -> None:
    invoker = ReviewLoopbackOnceInvoker(memory_workspace)

    result, saved_states = _run_pipeline(
        monkeypatch,
        tmp_path,
        invoker,
        _config(),
        counter_overrides={"iteration": 1, "reviewer_pass": 1},
    )

    assert result == 0
    fix_state = _state_with_phase(saved_states, "fix")
    assert fix_state.get_outer_progress("reviewer_pass") == 0
    assert fix_state.get_loop_iteration("review_analysis_iteration") == 1
    assert fix_state.review_outcome is not None
    final_state = saved_states[-1]
    assert final_state.phase == "complete"
    assert final_state.get_outer_progress("reviewer_pass") == 1
    assert final_state.get_loop_iteration("review_analysis_iteration") == 0


def test_review_analysis_cap_routes_through_final_fix_with_persisted_max_counter(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    memory_workspace: MemoryWorkspace,
) -> None:
    invoker = ReviewLoopbackToCapThenApproveInvoker(memory_workspace)

    result, saved_states = _run_pipeline(
        monkeypatch,
        tmp_path,
        invoker,
        _config(),
        counter_overrides={"iteration": 1, "reviewer_pass": 1},
    )

    assert result == 0
    capped_fix_state = next(
        state
        for state in saved_states
        if state.phase == "fix"
        and state.previous_phase == "review_analysis"
        and state.get_loop_iteration("review_analysis_iteration")
        == MAX_REVIEW_ANALYSIS_ITERATIONS
    )
    assert capped_fix_state.get_outer_progress("reviewer_pass") == 0
    assert capped_fix_state.review_outcome is not None
    assert invoker.count_for("fix") == MAX_REVIEW_ANALYSIS_ITERATIONS
    final_state = saved_states[-1]
    assert final_state.phase == "complete"
    assert final_state.get_outer_progress("reviewer_pass") == 1
    assert final_state.get_loop_iteration("review_analysis_iteration") == 0


def test_skipped_review_commit_preserves_outer_progress_in_persisted_state(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    memory_workspace: MemoryWorkspace,
) -> None:
    invoker = ReviewCommitSkippedInvoker(memory_workspace)

    result, saved_states = _run_pipeline(
        monkeypatch,
        tmp_path,
        invoker,
        _config(),
        counter_overrides={"iteration": 1, "reviewer_pass": 1},
    )

    assert result == 0
    final_state = saved_states[-1]
    assert final_state.phase == "complete"
    assert final_state.get_outer_progress("reviewer_pass") == 0
    assert final_state.get_loop_iteration("review_analysis_iteration") == 0


def test_planning_analysis_cap_skips_reentry_and_enters_development(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    memory_workspace: MemoryWorkspace,
) -> None:
    invoker = PlanningAnalysisRequestChangesOnceInvoker(memory_workspace)
    policy_bundle = load_policy(DEFAULT_POLICY_DIR)
    initial_state = PipelineState.from_policy(
        policy_bundle.pipeline,
        budget_caps={"iteration": 1, "reviewer_pass": 0},
        budget_remaining={"iteration": 1, "reviewer_pass": 0},
        loop_iterations={"planning_analysis_iteration": 1},
        loop_caps={
            "planning_analysis_iteration": 1,
            "development_analysis_iteration": DEVELOPMENT_CYCLES_THREE,
            "review_analysis_iteration": MAX_REVIEW_ANALYSIS_ITERATIONS,
        },
    )

    result, saved_states = _run_pipeline(
        monkeypatch,
        tmp_path,
        invoker,
        _config(),
        initial_state=initial_state,
        counter_overrides={"iteration": 1, "reviewer_pass": 0},
    )

    assert result == 0
    assert invoker.count_for("planning_analysis") == 0
    planning_analysis_states = [
        state for state in saved_states if state.phase == "planning_analysis"
    ]
    assert planning_analysis_states == []
    development_state = _state_with_phase(saved_states, "development")
    assert development_state.previous_phase == "planning"
    assert development_state.get_loop_iteration("planning_analysis_iteration") == 0


def test_runner_uses_real_planning_analysis_decision_and_skips_reentry_at_cap(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    saved_states: list[PipelineState] = []
    policy_bundle = load_policy(DEFAULT_POLICY_DIR)
    initial_loop_caps = {
        name: cfg.default_max for name, cfg in policy_bundle.pipeline.loop_counters.items()
    }
    initial_loop_caps["planning_analysis_iteration"] = 3
    initial_state = PipelineState.from_policy(
        policy_bundle.pipeline,
        budget_caps={"iteration": 1, "reviewer_pass": 0},
        budget_remaining={"iteration": 1, "reviewer_pass": 0},
        loop_caps=initial_loop_caps,
    )
    (tmp_path / "PROMPT.md").write_text("# Prompt\n\nReproduce exhausted planning analysis.")

    planning_analysis_calls = 0
    original_determine = runner._call_determine_effect_from_policy

    def stop_at_development(state, bundle, workspace_scope, config):
        if state.phase == "development":
            return ExitSuccessEffect()
        return original_determine(state, bundle, workspace_scope, config)

    def write_artifact(relative_path: str, payload: dict[str, object]) -> None:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))

    def fake_execute_effect(effect, _config, _workspace_scope, **_kwargs):
        nonlocal planning_analysis_calls
        if isinstance(effect, InvokeAgentEffect):
            if effect.phase == "planning":
                write_artifact(
                    ".agent/artifacts/plan.json",
                    {
                        "type": "plan",
                        "content": {
                            "summary": {
                                "context": "Minimal planning artifact for integration coverage.",
                                "scope_items": [
                                    {"text": "one"},
                                    {"text": "two"},
                                    {"text": "three"},
                                ],
                            },
                            "steps": [
                                {
                                    "number": 1,
                                    "title": "Touch file",
                                    "content": "Modify a tracked file.",
                                    "step_type": "file_change",
                                    "targets": [{"path": "foo.py", "action": "modify"}],
                                }
                            ],
                            "critical_files": {
                                "primary_files": [{"path": "foo.py", "action": "modify"}]
                            },
                            "risks_mitigations": [
                                {"risk": "minimal risk", "mitigation": "covered by test"}
                            ],
                            "verification_strategy": [
                                {"method": "pytest", "expected_outcome": "passes"}
                            ],
                            "work_units": [],
                        },
                    },
                )
                return PipelineEvent.AGENT_SUCCESS
            if effect.phase == "planning_analysis":
                planning_analysis_calls += 1
                decision = (
                    "request_changes"
                    if planning_analysis_calls <= MAX_PLANNING_ANALYSIS_ITERATIONS
                    else "completed"
                )
                write_artifact(
                    ".agent/artifacts/planning_analysis_decision.json",
                    {"type": "planning_analysis_decision", "content": {"status": decision}},
                )
                return PipelineEvent.AGENT_SUCCESS
            raise AssertionError(f"Unexpected invoke phase before development exit: {effect.phase}")
        if isinstance(effect, CommitEffect):
            raise AssertionError("Should not reach commit before stopping at development")
        raise AssertionError(f"Unexpected effect type: {type(effect)!r}")

    def capture_saved_state(state: PipelineState) -> None:
        saved_states.append(state)

    monkeypatch.setattr(runner, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_path))
    monkeypatch.setattr(runner, "load_policy_or_die", lambda _path: policy_bundle)
    monkeypatch.setattr(runner, "_materialize_prepared_prompt", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "_materialize_agent_prompt_if_needed", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "_execute_effect", fake_execute_effect)
    monkeypatch.setattr(runner, "_call_determine_effect_from_policy", stop_at_development)
    monkeypatch.setattr(runner.ckpt, "save", capture_saved_state)
    _install_runner_display_context(monkeypatch)

    result = runner.run(
        _config(),
        initial_state=initial_state,
        verbosity=Verbosity.QUIET,
        counter_overrides={"iteration": 1, "reviewer_pass": 0},
    )

    assert result == 0
    assert planning_analysis_calls == MAX_PLANNING_ANALYSIS_ITERATIONS
    loopback_planning_state = next(
        state
        for state in saved_states
        if state.phase == "planning"
        and state.previous_phase == "planning_analysis"
        and state.get_loop_iteration("planning_analysis_iteration")
        == MAX_PLANNING_ANALYSIS_ITERATIONS
    )
    assert (
        loopback_planning_state.get_loop_iteration("planning_analysis_iteration")
        == MAX_PLANNING_ANALYSIS_ITERATIONS
    )
    development_state = next(state for state in saved_states if state.phase == "development")
    assert development_state.previous_phase == "planning"
    assert development_state.get_loop_iteration("planning_analysis_iteration") == 0


@pytest.mark.parametrize("analysis_cap", [1, 2])
def test_development_analysis_runs_exactly_up_to_cap_then_skips_reentry(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    memory_workspace: MemoryWorkspace,
    analysis_cap: int,
) -> None:
    invoker = DevelopmentAnalysisAlwaysLoopbackInvoker(memory_workspace)
    policy_bundle = load_policy(DEFAULT_POLICY_DIR)
    initial_loop_caps = {
        name: cfg.default_max for name, cfg in policy_bundle.pipeline.loop_counters.items()
    }
    initial_loop_caps["development_analysis_iteration"] = analysis_cap
    initial_state = PipelineState(
        phase="development",
        policy_entry_phase=policy_bundle.pipeline.entry_phase,
        current_drain="development",
        budget_caps={"iteration": 1, "reviewer_pass": 0},
        budget_remaining={"iteration": 1, "reviewer_pass": 0},
        loop_caps=initial_loop_caps,
    )

    result, saved_states = _run_pipeline(
        monkeypatch,
        tmp_path,
        invoker,
        _config(),
        initial_state=initial_state,
        counter_overrides={"iteration": 1, "reviewer_pass": 0},
    )

    assert result == 0
    assert invoker.count_for("development_analysis") == analysis_cap
    development_analysis_states = [
        state for state in saved_states if state.phase == "development_analysis"
    ]
    assert len(development_analysis_states) == analysis_cap
    observed_counters = [
        state.get_loop_iteration("development_analysis_iteration")
        for state in development_analysis_states
    ]
    assert observed_counters == list(range(analysis_cap))
    expected_loopback_counters = list(range(1, analysis_cap + 1))
    loopback_states = [
        state
        for state in saved_states
        if state.phase == "development" and state.previous_phase == "development_analysis"
    ]
    assert [
        state.get_loop_iteration("development_analysis_iteration")
        for state in loopback_states
    ] == expected_loopback_counters
    development_commit_state = next(
        state for state in saved_states if state.phase == "development_commit"
    )
    assert development_commit_state.previous_phase == "development"
    assert development_commit_state.get_loop_iteration("development_analysis_iteration") == 0
    final_state = saved_states[-1]
    assert final_state.phase == "complete"
    assert final_state.get_outer_progress("iteration") == 1


def test_runner_uses_real_development_analysis_decision_and_skips_reentry_at_cap(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    saved_states: list[PipelineState] = []
    policy_bundle = load_policy(DEFAULT_POLICY_DIR)
    initial_loop_caps = {
        name: cfg.default_max for name, cfg in policy_bundle.pipeline.loop_counters.items()
    }
    initial_loop_caps["development_analysis_iteration"] = 3
    initial_state = PipelineState(
        phase="development",
        policy_entry_phase=policy_bundle.pipeline.entry_phase,
        current_drain="development",
        budget_caps={"iteration": 1, "reviewer_pass": 0},
        budget_remaining={"iteration": 1, "reviewer_pass": 0},
        loop_caps=initial_loop_caps,
    )
    (tmp_path / "PROMPT.md").write_text("# Prompt\n\nReproduce exhausted development analysis.")
    (tmp_path / ".agent" / "artifacts").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".agent" / "artifacts" / "plan.json").write_text(
        json.dumps(
            {
                "type": "plan",
                "content": {
                    "summary": {
                        "context": "Minimal planning artifact for development analysis coverage.",
                        "scope_items": [
                            {"text": "one"},
                            {"text": "two"},
                            {"text": "three"},
                        ],
                    },
                    "steps": [
                        {
                            "number": 1,
                            "title": "Touch file",
                            "content": "Modify a tracked file.",
                            "step_type": "file_change",
                            "targets": [{"path": "foo.py", "action": "modify"}],
                        }
                    ],
                    "critical_files": {
                        "primary_files": [{"path": "foo.py", "action": "modify"}]
                    },
                    "risks_mitigations": [
                        {"risk": "minimal risk", "mitigation": "covered by test"}
                    ],
                    "verification_strategy": [
                        {"method": "pytest", "expected_outcome": "passes"}
                    ],
                    "work_units": [],
                },
            }
        )
    )

    development_analysis_calls = 0
    original_determine = runner._call_determine_effect_from_policy

    def stop_at_development_commit(state, bundle, workspace_scope, config):
        if state.phase == "development_commit":
            return ExitSuccessEffect()
        return original_determine(state, bundle, workspace_scope, config)

    def write_artifact(relative_path: str, payload: dict[str, object]) -> None:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))

    def fake_execute_effect(effect, _config, _workspace_scope, **_kwargs):
        nonlocal development_analysis_calls
        if isinstance(effect, InvokeAgentEffect):
            if effect.phase == "development":
                write_artifact(
                    ".agent/artifacts/development_result.json",
                    {
                        "type": "development_result",
                        "content": {
                            "status": "completed",
                            "summary": "Development artifact present.",
                            "files_changed": "foo.py",
                        },
                    },
                )
                return PipelineEvent.AGENT_SUCCESS
            if effect.phase == "development_analysis":
                development_analysis_calls += 1
                decision = (
                    "request_changes"
                    if development_analysis_calls <= DEVELOPMENT_CYCLES_THREE
                    else "completed"
                )
                write_artifact(
                    ".agent/artifacts/development_analysis_decision.json",
                    {"type": "development_analysis_decision", "content": {"status": decision}},
                )
                return PipelineEvent.AGENT_SUCCESS
            raise AssertionError(
                "Unexpected invoke phase before development_commit exit: "
                f"{effect.phase}"
            )
        if isinstance(effect, CommitEffect):
            raise AssertionError("Should not reach commit before stopping at development_commit")
        raise AssertionError(f"Unexpected effect type: {type(effect)!r}")

    def capture_saved_state(state: PipelineState) -> None:
        saved_states.append(state)

    monkeypatch.setattr(runner, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_path))
    monkeypatch.setattr(runner, "load_policy_or_die", lambda _path: policy_bundle)
    monkeypatch.setattr(runner, "_materialize_prepared_prompt", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "_materialize_agent_prompt_if_needed", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "_execute_effect", fake_execute_effect)
    monkeypatch.setattr(runner, "_call_determine_effect_from_policy", stop_at_development_commit)
    monkeypatch.setattr(runner.ckpt, "save", capture_saved_state)
    _install_runner_display_context(monkeypatch)

    result = runner.run(
        _config(),
        initial_state=initial_state,
        verbosity=Verbosity.QUIET,
        counter_overrides={"iteration": 1, "reviewer_pass": 0},
    )

    assert result == 0
    assert development_analysis_calls == DEVELOPMENT_CYCLES_THREE
    development_analysis_states = [
        state for state in saved_states if state.phase == "development_analysis"
    ]
    assert len(development_analysis_states) == DEVELOPMENT_CYCLES_THREE
    loopback_development_state = next(
        state
        for state in saved_states
        if state.phase == "development"
        and state.previous_phase == "development_analysis"
        and state.get_loop_iteration("development_analysis_iteration")
        == DEVELOPMENT_CYCLES_THREE
    )
    assert loopback_development_state.get_budget_remaining("iteration") == 1
    development_commit_state = next(
        state for state in saved_states if state.phase == "development_commit"
    )
    assert development_commit_state.previous_phase == "development"
    assert development_commit_state.get_loop_iteration("development_analysis_iteration") == 0


def test_checkpoint_resume_preserves_budget(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    mock_agent_invoker: MockAgentInvoker,
) -> None:
    checkpoint_path = tmp_path / "checkpoint.json"
    state = PipelineState(
        phase="planning",
        budget_caps={"iteration": 1, "reviewer_pass": 0},
        budget_remaining={"iteration": 1, "reviewer_pass": 0},
    )
    ckpt_save(state, checkpoint_path)

    loaded = ckpt_load(checkpoint_path)

    assert loaded is not None
    assert loaded.get_budget_remaining("iteration") == 1

    result, saved_states = _run_pipeline(
        monkeypatch,
        tmp_path,
        mock_agent_invoker,
        _config(),
        initial_state=loaded,
        counter_overrides={"iteration": 1, "reviewer_pass": 0},
    )

    assert result == 0
    assert mock_agent_invoker.count_for("development") == 1
    final_state = saved_states[-1]
    assert final_state.phase == "complete"
    assert final_state.get_outer_progress("iteration") == 1
    assert final_state.get_budget_remaining("iteration") == 0
