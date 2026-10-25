"""Black-box test for real-resume restart via run_loop.py without injected controller."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

from ralph.config.enums import Verbosity
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.phases.execution import record_workspace_snapshot
from ralph.phases.required_artifacts import resolve_phase_required_artifact
from ralph.pipeline import checkpoint, run_loop
from ralph.pipeline import runner as runner_module
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps
from tests.recovery._scenario_harness import (
    AgentAttemptResult,
    _apply_attempt_side_effects,
    _build_policy_bundle,
    _ScenarioRegistry,
    _seed_valid_plan,
    run_scenario,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from ralph.config.agent_config import AgentConfig


def test_run_loop_real_resume_restores_ledger_without_injected_controller(
    tmp_path: Path,
) -> None:
    """Test resume via run_loop.py without injected or manually restored controller.

    Mid-streak failure state persisted in checkpoint ledger_snapshot is restored
    directly by run_loop's _resolve_recovery_controller, and the demotion limit
    is honored on the next failure without inspecting private counters.
    """

    # Session 1: Run mid-streak with consecutive limit 3, stop after 2 failures of agent_a
    def script_run_1(
        _agent: str, _phase: str, _attempt: int, _workspace: object
    ) -> AgentAttemptResult:
        return AgentAttemptResult(produce_artifact=False)

    outcome_run_1 = run_scenario(
        tmp_path,
        script=script_run_1,
        agents=("agent_a", "agent_b"),
        stop_after_invocations=2,
    )
    assert outcome_run_1.invocations == ["agent_a", "agent_a"]

    # Persist checkpoint to disk and reload state
    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint.save(outcome_run_1.final_state, checkpoint_path)
    loaded_state = checkpoint.load(checkpoint_path)
    assert loaded_state is not None
    assert loaded_state.ledger_snapshot is not None

    # Session 2: Resume via run_loop.run without injecting recovery_controller_factory
    # or manually creating RecoveryController.from_snapshot
    workspace = FsWorkspace(tmp_path)
    _seed_valid_plan(workspace)
    bundle = _build_policy_bundle(("agent_a", "agent_b"))
    runner_module.register_role_handlers(bundle.pipeline)

    deps = make_test_pipeline_deps(
        make_display_context(),
        policy_bundle=bundle,
        registry_factory=lambda _cfg: _ScenarioRegistry(),
        artifact_requirements_resolver=resolve_phase_required_artifact,
        recovery_controller_factory=None,  # run_loop must resolve and build its own controller
    )

    invocations_run_2: list[str] = []

    def fake_invoke_agent(
        agent_config: AgentConfig,
        prompt_file: str,
        *,
        options: object = None,
        _clock: object = None,
    ) -> Iterator[str]:
        del prompt_file, options, _clock
        agent = agent_config.cmd
        invocations_run_2.append(agent)
        result = AgentAttemptResult(produce_artifact=(agent == "agent_b"))
        record_workspace_snapshot(workspace, "development")
        _apply_attempt_side_effects(result, agent, len(invocations_run_2), workspace)
        return iter([f"Output from {agent}\n"])

    config = UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": False,
                "max_same_agent_retries": 0,
                "agent_max_consecutive_unproductive": 3,
            }
        }
    )

    with (
        patch(
            "ralph.pipeline.runner.resolve_workspace_scope", return_value=WorkspaceScope(tmp_path)
        ),
        patch("ralph.pipeline.runner.write_start_commit_if_absent"),
        patch("ralph.pipeline.runner.validate_custom_mcp_servers", return_value=0),
        patch("ralph.pipeline.effect_executor.invoke_agent", side_effect=fake_invoke_agent),
        patch("ralph.agents.invoke.invoke_agent", side_effect=fake_invoke_agent),
    ):
        exit_code = run_loop.run(
            config,
            initial_state=loaded_state,
            pipeline_deps=deps,
            verbosity=Verbosity.QUIET,
        )

    assert exit_code == 0
    # In Session 2, agent_a fails 1 more time (completing consecutive streak of 3)
    # and is demoted; agent_b is then selected and completes the phase.
    assert invocations_run_2 == ["agent_a", "agent_b"]
