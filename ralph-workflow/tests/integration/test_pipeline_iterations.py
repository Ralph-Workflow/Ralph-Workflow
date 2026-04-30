"""Integration tests for persisted development/review progress invariants.

These tests drive ``runner.run()`` in-process with a mocked agent execution seam
so the reducer, checkpoint save path, and policy routing can be exercised without
real subprocesses.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console

from ralph.config.enums import Verbosity
from ralph.config.models import GeneralConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline import runner
from ralph.pipeline.checkpoint import load as ckpt_load
from ralph.pipeline.checkpoint import save as ckpt_save
from ralph.pipeline.effects import CommitEffect, InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
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


def _config(developer_iters: int, reviewer_reviews: int) -> UnifiedConfig:
    return UnifiedConfig(
        general=GeneralConfig(
            developer_iters=developer_iters,
            reviewer_reviews=reviewer_reviews,
        )
    )


def _run_pipeline(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    mock_agent_invoker: MockAgentInvoker,
    config: UnifiedConfig,
    initial_state: PipelineState | None = None,
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
        analysis_phases = {"development_analysis", "review_analysis"}
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

    result = runner.run(config, initial_state=initial_state, verbosity=Verbosity.QUIET)
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
        _config(developer_iters=DEVELOPMENT_CYCLES_TWO, reviewer_reviews=0),
    )

    assert result == 0
    assert mock_agent_invoker.count_for("development") == DEVELOPMENT_CYCLES_TWO
    assert mock_agent_invoker.count_for("development_analysis") == DEVELOPMENT_CYCLES_TWO
    assert mock_agent_invoker.count_for("development_commit") == DEVELOPMENT_CYCLES_TWO
    final_state = saved_states[-1]
    assert final_state.phase == "complete"
    assert final_state.iteration == DEVELOPMENT_CYCLES_TWO
    assert final_state.reviewer_pass == 0
    assert final_state.get_loop_iteration("development_analysis_iteration") == 0
    assert final_state.get_loop_iteration("review_analysis_iteration") == 0
    assert final_state.development_budget_remaining == 0
    assert final_state.review_budget_remaining == 0


def test_dev_runs_exactly_3_cycles_with_d3(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    mock_agent_invoker: MockAgentInvoker,
) -> None:
    result, saved_states = _run_pipeline(
        monkeypatch,
        tmp_path,
        mock_agent_invoker,
        _config(developer_iters=DEVELOPMENT_CYCLES_THREE, reviewer_reviews=0),
    )

    assert result == 0
    assert mock_agent_invoker.count_for("development") == DEVELOPMENT_CYCLES_THREE
    assert mock_agent_invoker.count_for("development_analysis") == DEVELOPMENT_CYCLES_THREE
    assert mock_agent_invoker.count_for("development_commit") == DEVELOPMENT_CYCLES_THREE
    final_state = saved_states[-1]
    assert final_state.phase == "complete"
    assert final_state.iteration == DEVELOPMENT_CYCLES_THREE
    assert final_state.get_loop_iteration("development_analysis_iteration") == 0
    assert final_state.development_budget_remaining == 0


def test_review_runs_exactly_2_cycles_with_r2(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    mock_agent_invoker: MockAgentInvoker,
) -> None:
    result, saved_states = _run_pipeline(
        monkeypatch,
        tmp_path,
        mock_agent_invoker,
        _config(developer_iters=1, reviewer_reviews=REVIEW_CYCLES_TWO),
    )

    assert result == 0
    assert mock_agent_invoker.count_for("development") == 1
    assert mock_agent_invoker.count_for("review") == REVIEW_CYCLES_TWO
    final_state = saved_states[-1]
    assert final_state.phase == "complete"
    assert final_state.iteration == 1
    assert final_state.reviewer_pass == REVIEW_CYCLES_TWO
    assert final_state.get_loop_iteration("development_analysis_iteration") == 0
    assert final_state.get_loop_iteration("review_analysis_iteration") == 0
    assert final_state.review_budget_remaining == 0


def test_no_review_when_reviewer_reviews_zero(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    mock_agent_invoker: MockAgentInvoker,
) -> None:
    result, saved_states = _run_pipeline(
        monkeypatch,
        tmp_path,
        mock_agent_invoker,
        _config(developer_iters=DEVELOPMENT_CYCLES_TWO, reviewer_reviews=0),
    )

    assert result == 0
    assert mock_agent_invoker.count_for("review") == 0
    assert mock_agent_invoker.count_for("review_analysis") == 0
    assert mock_agent_invoker.count_for("review_commit") == 0
    assert mock_agent_invoker.count_for("development") == DEVELOPMENT_CYCLES_TWO
    final_state = saved_states[-1]
    assert final_state.phase == "complete"
    assert final_state.iteration == DEVELOPMENT_CYCLES_TWO
    assert final_state.reviewer_pass == 0
    assert final_state.review_budget_remaining == 0


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
        _config(developer_iters=DEVELOPMENT_CYCLES_TWO, reviewer_reviews=0),
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
    assert loopback_state.development_budget_remaining == starting_budget
    final_state = saved_states[-1]
    assert final_state.iteration == DEVELOPMENT_CYCLES_TWO
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
        _config(developer_iters=1, reviewer_reviews=1),
    )

    assert result == 0
    fix_state = _state_with_phase(saved_states, "fix")
    assert fix_state.reviewer_pass == 0
    assert fix_state.get_loop_iteration("review_analysis_iteration") == 1
    assert fix_state.review_issues_found is True
    final_state = saved_states[-1]
    assert final_state.phase == "complete"
    assert final_state.reviewer_pass == 1
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
        _config(developer_iters=1, reviewer_reviews=1),
    )

    assert result == 0
    capped_fix_state = next(
        state
        for state in saved_states
        if state.phase == "fix"
        and state.previous_phase == "review_analysis"
        and state.get_loop_iteration("review_analysis_iteration") == MAX_REVIEW_ANALYSIS_ITERATIONS
    )
    assert capped_fix_state.reviewer_pass == 0
    assert capped_fix_state.review_issues_found is True
    assert invoker.count_for("fix") == MAX_REVIEW_ANALYSIS_ITERATIONS
    final_state = saved_states[-1]
    assert final_state.phase == "complete"
    assert final_state.reviewer_pass == 1
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
        _config(developer_iters=1, reviewer_reviews=1),
    )

    assert result == 0
    final_state = saved_states[-1]
    assert final_state.phase == "complete"
    assert final_state.reviewer_pass == 0
    assert final_state.get_loop_iteration("review_analysis_iteration") == 0


def test_checkpoint_resume_preserves_budget(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    mock_agent_invoker: MockAgentInvoker,
) -> None:
    checkpoint_path = tmp_path / "checkpoint.json"
    state = PipelineState(
        phase="planning",
        total_iterations=1,
        total_reviewer_passes=0,
        development_budget_remaining=1,
        review_budget_remaining=0,
    )
    ckpt_save(state, checkpoint_path)

    loaded = ckpt_load(checkpoint_path)

    assert loaded is not None
    assert loaded.development_budget_remaining == 1

    result, saved_states = _run_pipeline(
        monkeypatch,
        tmp_path,
        mock_agent_invoker,
        _config(developer_iters=1, reviewer_reviews=0),
        initial_state=loaded,
    )

    assert result == 0
    assert mock_agent_invoker.count_for("development") == 1
    final_state = saved_states[-1]
    assert final_state.phase == "complete"
    assert final_state.iteration == 1
    assert final_state.development_budget_remaining == 0
