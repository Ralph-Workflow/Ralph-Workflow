"""Integration tests for persisted development/review progress invariants.

These tests drive ``runner.run()`` in-process with a mocked agent execution seam
so the reducer, checkpoint save path, and policy routing can be exercised without
real subprocesses.

These tests are subprocess_e2e: they exercise the real pipeline runner
with multi-iteration loops that cannot fit the per-test 1 s budget.
"""

from __future__ import annotations

import gc
import json
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from rich.console import Console

from ralph.config.enums import Verbosity
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline import runner
from ralph.pipeline.checkpoint import load as ckpt_load
from ralph.pipeline.checkpoint import save as ckpt_save
from ralph.pipeline.effects import CommitEffect, Effect, ExitSuccessEffect, InvokeAgentEffect
from ralph.pipeline.events import AnalysisDecisionEvent, PipelineEvent
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.policy.models import PolicyBundle
from ralph.workspace.scope import WorkspaceScope
from tests.integration._commit_cleanup_always_loopback_invoker import (
    CommitCleanupAlwaysLoopbackInvoker,
)
from tests.integration._development_analysis_always_loopback_invoker import (
    DevelopmentAnalysisAlwaysLoopbackInvoker,
)
from tests.integration._loopback_once_invoker import LoopbackOnceInvoker
from tests.integration._planning_analysis_request_changes_once_invoker import (
    PlanningAnalysisRequestChangesOnceInvoker,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from pytest import MonkeyPatch

    from ralph.policy.models import PolicyBundle
    from ralph.workspace.memory import MemoryWorkspace
    from tests.integration._mock_agent_invoker import MockAgentInvoker

DEFAULT_POLICY_DIR = Path(__file__).parent.parent.parent / "ralph" / "policy" / "defaults"
DEVELOPMENT_CYCLES_TWO = 2
DEVELOPMENT_CYCLES_THREE = 3
MAX_PLANNING_ANALYSIS_ITERATIONS = 3

# All tests in this module drive the full pipeline runner (multiple
# phases, policy routing, checkpoint save path) and frequently exceed
# the default 1-second per-test ceiling under parallel xdist load.
# A 5-second ceiling reflects the realistic wall-clock cost without
# changing the test design.
pytestmark = [pytest.mark.timeout_seconds(5), pytest.mark.subprocess_e2e]


def _install_runner_display_context(monkeypatch: MonkeyPatch) -> None:
    console = Console(record=True, force_terminal=False, width=120, color_system=None)
    ctx = make_display_context(console=console, force_width=120, force_mode="wide")
    monkeypatch.setattr(runner, "make_display_context", lambda **_kwargs: ctx)


def _stub_prompt_materialization(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "materialize_prepared_prompt", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "materialize_prompt_for_phase", lambda *args, **kwargs: "noop")
    monkeypatch.setattr(runner, "materialize_agent_prompt_if_needed", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "materialize_agent_prompt_if_needed", lambda *args, **kwargs: None)


def _write_artifact(root: Path, relative_path: str, payload: dict[str, object]) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


@lru_cache(maxsize=1)
def _default_policy_bundle() -> object:
    return load_policy(DEFAULT_POLICY_DIR)


def _config() -> UnifiedConfig:
    return UnifiedConfig()


def _policy_bundle_with_loop_counter(counter_name: str, default_max: int) -> PolicyBundle:
    bundle = load_policy(DEFAULT_POLICY_DIR)
    loop_counters = dict(bundle.pipeline.loop_counters)
    loop_counters[counter_name] = loop_counters[counter_name].model_copy(
        update={"default_max": default_max}
    )
    return bundle.model_copy(
        update={"pipeline": bundle.pipeline.model_copy(update={"loop_counters": loop_counters})}
    )


def _run_pipeline(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    mock_agent_invoker: MockAgentInvoker,
    config: UnifiedConfig,
    initial_state: PipelineState | None = None,
    counter_overrides: dict[str, int] | None = None,
    policy_bundle: PolicyBundle | None = None,
) -> tuple[int, list[PipelineState]]:
    saved_states: list[PipelineState] = []
    policy_bundle = _default_policy_bundle() if policy_bundle is None else policy_bundle

    def fake_execute_effect(
        effect: Effect,
        _config: UnifiedConfig,
        _workspace_scope: WorkspaceScope,
    ) -> PipelineEvent:
        if isinstance(effect, InvokeAgentEffect):
            mock_agent_invoker.invoke(effect.agent_name, effect.phase)
            return PipelineEvent.AGENT_SUCCESS
        if isinstance(effect, CommitEffect):
            commit_event_for = cast(
                "Callable[[str], PipelineEvent] | None",
                getattr(mock_agent_invoker, "commit_event_for", None),
            )
            last_phase = getattr(mock_agent_invoker, "last_phase", None)
            if (
                commit_event_for is not None
                and isinstance(last_phase, str)
                and (last_phase.endswith("_commit") or last_phase == "commit")
            ):
                return commit_event_for(last_phase)
            return PipelineEvent.COMMIT_SUCCESS
        msg = f"Unexpected effect type: {type(effect)!r}"
        raise AssertionError(msg)

    def fake_phase_event_after_agent_run(
        *,
        effect: InvokeAgentEffect,
        **_kwargs: object,
    ) -> AnalysisDecisionEvent | PipelineEvent:
        analysis_event_for = cast(
            "Callable[[str], AnalysisDecisionEvent] | None",
            getattr(mock_agent_invoker, "analysis_event_for", None),
        )
        analysis_phases = {"planning_analysis", "development_analysis"}
        if analysis_event_for is not None and effect.phase in analysis_phases:
            return analysis_event_for(effect.phase)
        # Handle commit phases using commit_event_for
        commit_event_for = cast(
            "Callable[[str], PipelineEvent] | None",
            getattr(mock_agent_invoker, "commit_event_for", None),
        )
        if commit_event_for is not None:
            return commit_event_for(effect.phase)
        return PipelineEvent.AGENT_SUCCESS

    def capture_saved_state(state: PipelineState, *_args: object, **_kwargs: object) -> None:
        saved_states.append(state)

    monkeypatch.setattr(runner, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_path))
    monkeypatch.setattr(runner, "load_policy_or_die", lambda _path: policy_bundle)
    monkeypatch.setattr(runner, "materialize_agent_prompt_if_needed", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "materialize_prompt_for_phase", lambda *args, **kwargs: "noop")
    monkeypatch.setattr(runner, "execute_effect", fake_execute_effect)
    monkeypatch.setattr(runner, "phase_event_after_agent_run", fake_phase_event_after_agent_run)
    monkeypatch.setattr(runner.ckpt, "save", capture_saved_state)
    _install_runner_display_context(monkeypatch)

    result = runner.run(
        config,
        initial_state=initial_state,
        verbosity=Verbosity.QUIET,
        counter_overrides=counter_overrides,
    )
    return result, saved_states


def _state_with_phase(saved_states: list[PipelineState], phase: str) -> PipelineState:
    for state in saved_states:
        if state.phase == phase:
            return state
    raise AssertionError(f"expected a saved state for phase {phase!r}")


def test_default_policy_saved_states_mark_block_policy_format(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    mock_agent_invoker: MockAgentInvoker,
) -> None:
    result, saved_states = _run_pipeline(
        monkeypatch,
        tmp_path,
        mock_agent_invoker,
        _config(),
        counter_overrides={"iteration": 1},
    )

    assert result == 0
    assert saved_states
    assert saved_states[-1].policy_format_version == 2
    assert any(state.policy_format_version == 2 for state in saved_states)


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
        counter_overrides={"iteration": DEVELOPMENT_CYCLES_TWO},
    )

    assert result == 0
    assert mock_agent_invoker.count_for("development") == DEVELOPMENT_CYCLES_TWO
    assert mock_agent_invoker.count_for("development_analysis") == DEVELOPMENT_CYCLES_TWO
    assert mock_agent_invoker.count_for("development_commit") == DEVELOPMENT_CYCLES_TWO
    final_state = saved_states[-1]
    assert final_state.phase == "complete"
    assert final_state.get_outer_progress("iteration") == DEVELOPMENT_CYCLES_TWO
    assert final_state.get_loop_iteration("development_analysis_iteration") == 0
    assert final_state.get_budget_remaining("iteration") == 0


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
        counter_overrides={"iteration": DEVELOPMENT_CYCLES_THREE},
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
        counter_overrides={"iteration": DEVELOPMENT_CYCLES_TWO},
    )

    assert result == 0
    assert invoker.count_for("development") == DEVELOPMENT_CYCLES_THREE
    assert invoker.count_for("development_commit") == DEVELOPMENT_CYCLES_THREE
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


def test_planning_analysis_cap_skips_reentry_and_enters_development(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    memory_workspace: MemoryWorkspace,
) -> None:
    invoker = PlanningAnalysisRequestChangesOnceInvoker(memory_workspace)
    policy_bundle = _policy_bundle_with_loop_counter("planning_analysis_iteration", 1)
    initial_state = PipelineState.from_policy(
        policy_bundle.pipeline,
        budget_caps={"iteration": 1},
        loop_iterations={"planning_analysis_iteration": 1},
    )

    result, saved_states = _run_pipeline(
        monkeypatch,
        tmp_path,
        invoker,
        _config(),
        initial_state=initial_state,
        counter_overrides={"iteration": 1},
        policy_bundle=policy_bundle,
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
    policy_bundle = _policy_bundle_with_loop_counter("planning_analysis_iteration", 3)
    initial_state = PipelineState.from_policy(
        policy_bundle.pipeline,
        budget_caps={"iteration": 1},
    )
    (tmp_path / "PROMPT.md").write_text("# Prompt\n\nReproduce exhausted planning analysis.")

    planning_analysis_calls = 0
    original_determine = runner.call_determine_effect_from_policy

    def stop_at_development(
        state: PipelineState,
        bundle: PolicyBundle,
        workspace_scope: WorkspaceScope,
        config: UnifiedConfig,
    ) -> Effect:
        if state.phase == "development":
            return ExitSuccessEffect()
        return original_determine(state, bundle, workspace_scope, config)

    def fake_execute_effect(
        effect: object,
        _config: object,
        _workspace_scope: object,
        **_kwargs: object,
    ) -> PipelineEvent:
        nonlocal planning_analysis_calls
        if isinstance(effect, InvokeAgentEffect):
            if effect.phase == "planning":
                _write_artifact(
                    tmp_path,
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
                _write_artifact(
                    tmp_path,
                    ".agent/artifacts/planning_analysis_decision.json",
                    {"type": "planning_analysis_decision", "content": {"status": decision}},
                )
                return PipelineEvent.AGENT_SUCCESS
            raise AssertionError(f"Unexpected invoke phase before development exit: {effect.phase}")
        if isinstance(effect, CommitEffect):
            raise AssertionError("Should not reach commit before stopping at development")
        raise AssertionError(f"Unexpected effect type: {type(effect)!r}")

    def capture_saved_state(state: PipelineState, *_args: object, **_kwargs: object) -> None:
        saved_states.append(state)

    def fake_phase_event_after_agent_run(
        *,
        effect: object,
        **_kwargs: object,
    ) -> PipelineEvent:
        if isinstance(effect, InvokeAgentEffect) and effect.phase == "planning_analysis":
            try:
                data = json.loads(
                    (tmp_path / ".agent/artifacts/planning_analysis_decision.json").read_text()
                )
                status = data["content"]["status"]
                if status == "completed":
                    return PipelineEvent.ANALYSIS_SUCCESS
                return PipelineEvent.ANALYSIS_LOOPBACK
            except Exception:
                return PipelineEvent.ANALYSIS_LOOPBACK
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(runner, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_path))
    monkeypatch.setattr(runner, "load_policy_or_die", lambda _path: policy_bundle)
    _stub_prompt_materialization(monkeypatch)
    monkeypatch.setattr(runner, "execute_effect", fake_execute_effect)
    monkeypatch.setattr(runner, "call_determine_effect_from_policy", stop_at_development)
    monkeypatch.setattr(runner, "phase_event_after_agent_run", fake_phase_event_after_agent_run)
    monkeypatch.setattr(runner.ckpt, "save", capture_saved_state)
    _install_runner_display_context(monkeypatch)

    result = runner.run(
        _config(),
        initial_state=initial_state,
        verbosity=Verbosity.QUIET,
        counter_overrides={"iteration": 1},
    )
    gc.collect()

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
    policy_bundle = _policy_bundle_with_loop_counter(
        "development_analysis_iteration",
        analysis_cap,
    )
    initial_state = PipelineState(
        phase="development",
        policy_entry_phase=policy_bundle.pipeline.entry_phase,
        current_drain="development",
        budget_caps={"iteration": 1},
    )

    result, saved_states = _run_pipeline(
        monkeypatch,
        tmp_path,
        invoker,
        _config(),
        initial_state=initial_state,
        counter_overrides={"iteration": 1},
        policy_bundle=policy_bundle,
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
        state.get_loop_iteration("development_analysis_iteration") for state in loopback_states
    ] == expected_loopback_counters
    development_final_commit_state = next(
        state for state in saved_states if state.phase == "development_final_commit"
    )
    # development_analysis -> development_final_commit_cleanup -> development_final_commit
    assert development_final_commit_state.previous_phase == "development_final_commit_cleanup"
    assert development_final_commit_state.get_loop_iteration("development_analysis_iteration") == 0
    final_state = saved_states[-1]
    assert final_state.phase == "complete"
    assert final_state.get_outer_progress("iteration") == 1


def test_runner_uses_real_development_analysis_decision_and_skips_reentry_at_cap(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    saved_states: list[PipelineState] = []
    policy_bundle = _policy_bundle_with_loop_counter("development_analysis_iteration", 3)
    initial_state = PipelineState(
        phase="development",
        policy_entry_phase=policy_bundle.pipeline.entry_phase,
        current_drain="development",
        budget_caps={"iteration": 1},
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
                    "skills_mcp": {
                        "skills": [
                            "test-driven-development",
                            "verification-before-completion",
                        ],
                        "mcps": [],
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
                    "critical_files": {"primary_files": [{"path": "foo.py", "action": "modify"}]},
                    "risks_mitigations": [
                        {"risk": "minimal risk", "mitigation": "covered by test"}
                    ],
                    "verification_strategy": [{"method": "pytest", "expected_outcome": "passes"}],
                    "work_units": [],
                },
            }
        )
    )

    development_analysis_calls = 0
    original_determine = runner.call_determine_effect_from_policy

    def stop_at_development_final_commit(
        state: PipelineState,
        bundle: PolicyBundle,
        workspace_scope: WorkspaceScope,
        config: UnifiedConfig,
    ) -> Effect:
        if state.phase == "development_final_commit":
            return ExitSuccessEffect()
        return original_determine(state, bundle, workspace_scope, config)

    def fake_execute_effect(
        effect: object,
        _config: object,
        _workspace_scope: object,
        **_kwargs: object,
    ) -> PipelineEvent:
        nonlocal development_analysis_calls
        if isinstance(effect, InvokeAgentEffect):
            if effect.phase == "development":
                _write_artifact(
                    tmp_path,
                    ".agent/artifacts/development_result.json",
                    {
                        "type": "development_result",
                        "content": {
                            "status": "completed",
                            "summary": "Development artifact present.",
                            "files_changed": "foo.py",
                            "plan_items_proven": [
                                {
                                    "plan_item": "Step 1: Touch file",
                                    "proof": "Updated foo.py as planned.",
                                }
                            ],
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
                _write_artifact(
                    tmp_path,
                    ".agent/artifacts/development_analysis_decision.json",
                    {"type": "development_analysis_decision", "content": {"status": decision}},
                )
                return PipelineEvent.AGENT_SUCCESS
            if effect.phase in {
                "development_commit_cleanup",
                "development_final_commit_cleanup",
            }:
                return (
                    _write_artifact(
                        tmp_path,
                        ".agent/artifacts/commit_cleanup.json",
                        {"analysis_complete": True, "actions": []},
                    )
                    or PipelineEvent.AGENT_SUCCESS
                )
            if effect.phase == "development_commit":
                return (
                    _write_artifact(
                        tmp_path,
                        ".agent/tmp/commit_message.json",
                        {
                            "name": "commit_message",
                            "type": "commit_message",
                            "content": {
                                "type": "commit",
                                "subject": "fix: continue development analysis loop",
                            },
                            "created_at": "STATIC",
                            "updated_at": "STATIC",
                            "metadata": {},
                        },
                    )
                    or PipelineEvent.AGENT_SUCCESS
                )
            raise AssertionError(
                f"Unexpected invoke phase before development_final_commit exit: {effect.phase}"
            )
        if isinstance(effect, CommitEffect):
            return PipelineEvent.COMMIT_SUCCESS
        raise AssertionError(f"Unexpected effect type: {type(effect)!r}")

    def capture_saved_state(state: PipelineState, *_args: object, **_kwargs: object) -> None:
        saved_states.append(state)

    monkeypatch.setattr(runner, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_path))
    monkeypatch.setattr(runner, "load_policy_or_die", lambda _path: policy_bundle)
    _stub_prompt_materialization(monkeypatch)
    monkeypatch.setattr(runner, "execute_effect", fake_execute_effect)
    monkeypatch.setattr(
        runner,
        "call_determine_effect_from_policy",
        stop_at_development_final_commit,
    )
    monkeypatch.setattr(runner.ckpt, "save", capture_saved_state)
    _install_runner_display_context(monkeypatch)

    result = runner.run(
        _config(),
        initial_state=initial_state,
        verbosity=Verbosity.QUIET,
        counter_overrides={"iteration": 1},
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
        and state.get_loop_iteration("development_analysis_iteration") == DEVELOPMENT_CYCLES_THREE
    )
    assert loopback_development_state.get_budget_remaining("iteration") == 1
    development_commit_state = next(
        state for state in saved_states if state.phase == "development_commit"
    )
    # development_analysis -> development_commit_cleanup -> development_commit
    assert development_commit_state.previous_phase == "development_commit_cleanup"
    assert development_commit_state.get_loop_iteration("development_analysis_iteration") == 0


def test_checkpoint_resume_preserves_budget(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    mock_agent_invoker: MockAgentInvoker,
) -> None:
    checkpoint_path = tmp_path / "checkpoint.json"
    state = PipelineState(
        phase="planning",
        budget_caps={"iteration": 1},
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
        counter_overrides={"iteration": 1},
    )

    assert result == 0
    assert mock_agent_invoker.count_for("development") == 1
    final_state = saved_states[-1]
    assert final_state.phase == "complete"
    assert final_state.get_outer_progress("iteration") == 1
    assert final_state.get_budget_remaining("iteration") == 0


def test_dev_cycle_routing_layer_completes_with_mocked_execution(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    mock_agent_invoker: MockAgentInvoker,
) -> None:
    """Routing-layer regression: the pipeline can complete development cycles even when
    the mock execution handler never writes development_result.

    _execute_effect is mocked to return AGENT_SUCCESS, completely bypassing
    handle_execution_phase. This test exercises the runner/checkpoint/state-machine
    routing layer only, not artifact validation. development_result is required by
    default policy, but this test confirms the routing layer is not affected by
    mocked execution.
    """
    result, saved_states = _run_pipeline(
        monkeypatch,
        tmp_path,
        mock_agent_invoker,
        _config(),
        counter_overrides={"iteration": DEVELOPMENT_CYCLES_TWO},
    )

    assert result == 0
    assert mock_agent_invoker.count_for("development") == DEVELOPMENT_CYCLES_TWO
    assert mock_agent_invoker.count_for("development_analysis") == DEVELOPMENT_CYCLES_TWO
    assert mock_agent_invoker.count_for("development_commit") == DEVELOPMENT_CYCLES_TWO
    final_state = saved_states[-1]
    assert final_state.phase == "complete"
    assert final_state.get_outer_progress("iteration") == DEVELOPMENT_CYCLES_TWO
    assert final_state.get_budget_remaining("iteration") == 0


def test_commit_cleanup_loop_exhaustion_advances_to_development_commit(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    memory_workspace: MemoryWorkspace,
) -> None:
    """Commit cleanup loop exhaustion bypasses to development_commit with counter reset.

    When CommitCleanupAlwaysLoopbackInvoker forces every cleanup run to loopback,
    the loop counter increments until it hits the cap (3), at which point the
    exhaustion bypass routes to development_commit. The commit_cleanup_iteration
    counter should be reset to 0 when development_commit runs (via loop_resets).
    """
    invoker = CommitCleanupAlwaysLoopbackInvoker(memory_workspace)

    result, saved_states = _run_pipeline(
        monkeypatch,
        tmp_path,
        invoker,
        _config(),
        counter_overrides={"iteration": 1},
    )

    assert result == 0
    assert invoker.count_for("development_commit_cleanup") == 3
    dev_commit_state = _state_with_phase(saved_states, "development_commit")
    assert dev_commit_state.get_loop_iteration("commit_cleanup_iteration") == 0
    assert saved_states[-1].phase == "complete"
