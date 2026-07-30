"""Runner regressions for fresh-entry drain clearing on InvokeAgentEffect paths."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.config.enums import Verbosity
from ralph.display.context import make_display_context
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.prompts._missing_plan_handoff_error import MissingPlanHandoffError
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pytest import MonkeyPatch

    from ralph.policy.models import PolicyBundle


@lru_cache(maxsize=1)
def _load_default_policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


# Warm the policy TOML parse at module-import time so the first test to call
# _load_default_policy_bundle does not bear the full cold-start parse cost
# under 12-worker xdist contention (which can exceed the 1.0s per-test timeout).
# This shifts the parse to collection time, before any per-test SIGALRM timer.
_ = _load_default_policy_bundle()


def _write_artifact_files(
    workspace: FsWorkspace,
    artifact_type: str,
    artifact_path: str,
    md_path: str | None,
) -> None:
    workspace.mkdirs(Path(artifact_path).parent.as_posix())
    workspace.write(artifact_path, f"---\ntype: {artifact_type}\n---\n\n# Test\n")
    if md_path is not None:
        workspace.write(md_path, f"# {artifact_type}\n\ntest content")


def _run_pipeline_step(
    *,
    state: PipelineState,
    effect: InvokeAgentEffect,
    workspace_scope: WorkspaceScope,
    monkeypatch: MonkeyPatch,
    stub_materialize: bool,
    raise_missing_plan_handoff: bool = False,
) -> object:
    bundle = _load_default_policy_bundle()

    monkeypatch.setattr(
        runner_module,
        "call_determine_effect_from_policy",
        lambda *_args, **_kwargs: effect,
    )
    if raise_missing_plan_handoff:
        # `raise` takes precedence: the new flag deliberately bypasses the
        # stub-with-None short-circuit so the recovery helper's try/except
        # branch can be exercised through the real seam.
        def _raise_missing_plan_handoff(*_args: object, **_kwargs: object) -> None:
            raise MissingPlanHandoffError(
                "Template 'developer_iteration.jinja' requires an existing "
                "plan handoff at .agent/PLAN.md"
            )

        monkeypatch.setattr(
            runner_module,
            "materialize_agent_prompt_if_needed",
            _raise_missing_plan_handoff,
        )
    elif stub_materialize:
        monkeypatch.setattr(
            runner_module,
            "materialize_agent_prompt_if_needed",
            lambda *_args, **_kwargs: None,
        )
    monkeypatch.setattr(
        runner_module,
        "invoke_execute_effect_with_optional_display",
        lambda *_args, **_kwargs: PipelineEvent.AGENT_SUCCESS,
    )
    monkeypatch.setattr(
        runner_module,
        "phase_event_after_agent_run",
        lambda **_kwargs: PipelineEvent.AGENT_SUCCESS,
    )
    monkeypatch.setattr(
        runner_module,
        "reducer_reduce",
        lambda current_state, _event, _policy, recovery=None: (current_state, []),
    )
    monkeypatch.setattr(runner_module.ckpt, "save", lambda *_args, **_kwargs: None)

    display_context = make_display_context()
    display = runner_module.ParallelDisplay(display_context)
    registry = MagicMock()
    registry.get.return_value = None

    return runner_module.run_pipeline_step(
        state=state,
        policy_bundle=bundle,
        workspace_scope=workspace_scope,
        config=MagicMock(),
        display=display,
        display_context=display_context,
        verbosity=Verbosity.QUIET,
        registry=registry,
        pipeline_subscriber=None,
    )


class TestPipelineRunnerInvokeAgentDrainClearing:
    def test_fresh_development_entry_clears_dev_analysis_drains(
        self,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        workspace = FsWorkspace(tmp_path)
        workspace_scope = WorkspaceScope(tmp_path)
        _write_artifact_files(
            workspace,
            "development_analysis_decision",
            ".agent/artifacts/development_analysis_decision.md",
            ".agent/DEVELOPMENT_ANALYSIS_DECISION.md",
        )
        effect = InvokeAgentEffect(
            agent_name="claude",
            phase="development",
            prompt_file=".agent/tmp/development_prompt.md",
            drain="development",
        )
        state = PipelineState(phase="development", previous_phase="planning_analysis")

        result = _run_pipeline_step(
            state=state,
            effect=effect,
            workspace_scope=workspace_scope,
            monkeypatch=monkeypatch,
            stub_materialize=True,
        )

        assert isinstance(result, PipelineState)
        assert not workspace.exists(".agent/artifacts/development_analysis_decision.md")
        assert not workspace.exists(".agent/DEVELOPMENT_ANALYSIS_DECISION.md")

    def test_analysis_loopback_does_not_clear_drains(
        self,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        workspace = FsWorkspace(tmp_path)
        workspace_scope = WorkspaceScope(tmp_path)
        _write_artifact_files(
            workspace,
            "development_analysis_decision",
            ".agent/artifacts/development_analysis_decision.md",
            ".agent/DEVELOPMENT_ANALYSIS_DECISION.md",
        )
        effect = InvokeAgentEffect(
            agent_name="claude",
            phase="development",
            prompt_file=".agent/tmp/development_prompt.md",
            drain="development",
        )
        state = PipelineState(phase="development", previous_phase="development_analysis")

        result = _run_pipeline_step(
            state=state,
            effect=effect,
            workspace_scope=workspace_scope,
            monkeypatch=monkeypatch,
            stub_materialize=True,
        )

        assert isinstance(result, PipelineState)
        assert workspace.exists(".agent/artifacts/development_analysis_decision.md")
        assert workspace.exists(".agent/DEVELOPMENT_ANALYSIS_DECISION.md")

    def test_same_phase_retry_does_not_clear_drains(
        self,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        workspace = FsWorkspace(tmp_path)
        workspace_scope = WorkspaceScope(tmp_path)
        _write_artifact_files(
            workspace,
            "development_analysis_decision",
            ".agent/artifacts/development_analysis_decision.md",
            ".agent/DEVELOPMENT_ANALYSIS_DECISION.md",
        )
        effect = InvokeAgentEffect(
            agent_name="claude",
            phase="development",
            prompt_file=".agent/tmp/development_prompt.md",
            drain="development",
        )
        state = PipelineState(phase="development", previous_phase="development")

        result = _run_pipeline_step(
            state=state,
            effect=effect,
            workspace_scope=workspace_scope,
            monkeypatch=monkeypatch,
            stub_materialize=True,
        )

        assert isinstance(result, PipelineState)
        assert workspace.exists(".agent/artifacts/development_analysis_decision.md")
        assert workspace.exists(".agent/DEVELOPMENT_ANALYSIS_DECISION.md")

    def test_resume_guard_does_not_clear_drains(
        self,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        workspace = FsWorkspace(tmp_path)
        workspace_scope = WorkspaceScope(tmp_path)
        _write_artifact_files(
            workspace,
            "development_analysis_decision",
            ".agent/artifacts/development_analysis_decision.md",
            ".agent/DEVELOPMENT_ANALYSIS_DECISION.md",
        )
        effect = InvokeAgentEffect(
            agent_name="claude",
            phase="development",
            prompt_file=".agent/tmp/development_prompt.md",
            drain="development",
        )
        state = PipelineState(
            phase="development",
            previous_phase=None,
            checkpoint_saved_count=1,
        )

        result = _run_pipeline_step(
            state=state,
            effect=effect,
            workspace_scope=workspace_scope,
            monkeypatch=monkeypatch,
            stub_materialize=True,
        )

        assert isinstance(result, PipelineState)
        assert workspace.exists(".agent/artifacts/development_analysis_decision.md")
        assert workspace.exists(".agent/DEVELOPMENT_ANALYSIS_DECISION.md")

    def test_fresh_planning_entry_via_invoke_agent_clears_drains_safely(
        self,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        workspace = FsWorkspace(tmp_path)
        workspace_scope = WorkspaceScope(tmp_path)
        workspace.write("PROMPT.md", "Create a fresh plan")
        workspace.write(
            ".agent/tmp/planning_prompt.md",
            "You are in PLANNING MODE. Create a detailed, structured execution plan.",
        )
        _write_artifact_files(workspace, "plan", ".agent/artifacts/plan.md", ".agent/PLAN.md")
        _write_artifact_files(
            workspace,
            "planning_analysis_decision",
            ".agent/artifacts/planning_analysis_decision.md",
            ".agent/PLANNING_ANALYSIS_DECISION.md",
        )
        _write_artifact_files(
            workspace,
            "development_analysis_decision",
            ".agent/artifacts/development_analysis_decision.md",
            ".agent/DEVELOPMENT_ANALYSIS_DECISION.md",
        )
        effect = InvokeAgentEffect(
            agent_name="claude",
            phase="planning",
            prompt_file="PROMPT.md",
            drain="planning",
        )
        state = PipelineState(phase="planning", previous_phase=None)

        result = _run_pipeline_step(
            state=state,
            effect=effect,
            workspace_scope=workspace_scope,
            monkeypatch=monkeypatch,
            stub_materialize=False,
        )

        assert isinstance(result, PipelineState)
        assert not workspace.exists(".agent/artifacts/plan.md")
        assert not workspace.exists(".agent/PLAN.md")
        assert not workspace.exists(".agent/artifacts/planning_analysis_decision.md")
        assert not workspace.exists(".agent/PLANNING_ANALYSIS_DECISION.md")
        assert not workspace.exists(".agent/artifacts/development_analysis_decision.md")
        assert not workspace.exists(".agent/DEVELOPMENT_ANALYSIS_DECISION.md")

    def test_fresh_planning_entry_from_dev_final_commit_clears_dev_analysis(
        self,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        workspace = FsWorkspace(tmp_path)
        workspace_scope = WorkspaceScope(tmp_path)
        workspace.write("PROMPT.md", "Create a fresh plan")
        workspace.write(
            ".agent/tmp/planning_prompt.md",
            "You are in PLANNING MODE. Create a detailed, structured execution plan.",
        )
        _write_artifact_files(workspace, "plan", ".agent/artifacts/plan.md", ".agent/PLAN.md")
        _write_artifact_files(
            workspace,
            "planning_analysis_decision",
            ".agent/artifacts/planning_analysis_decision.md",
            ".agent/PLANNING_ANALYSIS_DECISION.md",
        )
        _write_artifact_files(
            workspace,
            "development_analysis_decision",
            ".agent/artifacts/development_analysis_decision.md",
            ".agent/DEVELOPMENT_ANALYSIS_DECISION.md",
        )
        effect = InvokeAgentEffect(
            agent_name="claude",
            phase="planning",
            prompt_file="PROMPT.md",
            drain="planning",
        )
        state = PipelineState(phase="planning", previous_phase="development_final_commit")

        result = _run_pipeline_step(
            state=state,
            effect=effect,
            workspace_scope=workspace_scope,
            monkeypatch=monkeypatch,
            stub_materialize=True,
        )

        assert isinstance(result, PipelineState)
        assert not workspace.exists(".agent/artifacts/plan.md")
        assert not workspace.exists(".agent/PLAN.md")
        assert not workspace.exists(".agent/artifacts/planning_analysis_decision.md")
        assert not workspace.exists(".agent/PLANNING_ANALYSIS_DECISION.md")
        assert not workspace.exists(".agent/artifacts/development_analysis_decision.md")
        assert not workspace.exists(".agent/DEVELOPMENT_ANALYSIS_DECISION.md")

    def test_fresh_development_commit_entry_clears_development_drains(
        self,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        workspace = FsWorkspace(tmp_path)
        workspace_scope = WorkspaceScope(tmp_path)
        _write_artifact_files(
            workspace,
            "development_result",
            ".agent/artifacts/development_result.md",
            ".agent/DEVELOPMENT_RESULT.md",
        )
        _write_artifact_files(
            workspace,
            "development_analysis_decision",
            ".agent/artifacts/development_analysis_decision.md",
            ".agent/DEVELOPMENT_ANALYSIS_DECISION.md",
        )
        effect = InvokeAgentEffect(
            agent_name="claude",
            phase="development_commit",
            prompt_file=".agent/tmp/development_commit_prompt.md",
            drain="development_commit",
        )
        state = PipelineState(
            phase="development_commit",
            previous_phase="development_commit_cleanup",
        )

        result = _run_pipeline_step(
            state=state,
            effect=effect,
            workspace_scope=workspace_scope,
            monkeypatch=monkeypatch,
            stub_materialize=True,
        )

        assert isinstance(result, PipelineState)
        assert not workspace.exists(".agent/artifacts/development_result.md")
        assert not workspace.exists(".agent/DEVELOPMENT_RESULT.md")
        assert not workspace.exists(".agent/artifacts/development_analysis_decision.md")
        assert not workspace.exists(".agent/DEVELOPMENT_ANALYSIS_DECISION.md")

    def test_fresh_development_final_commit_entry_clears_development_drains(
        self,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        workspace = FsWorkspace(tmp_path)
        workspace_scope = WorkspaceScope(tmp_path)
        _write_artifact_files(
            workspace,
            "development_result",
            ".agent/artifacts/development_result.md",
            ".agent/DEVELOPMENT_RESULT.md",
        )
        _write_artifact_files(
            workspace,
            "development_analysis_decision",
            ".agent/artifacts/development_analysis_decision.md",
            ".agent/DEVELOPMENT_ANALYSIS_DECISION.md",
        )
        effect = InvokeAgentEffect(
            agent_name="claude",
            phase="development_final_commit",
            prompt_file=".agent/tmp/development_final_commit_prompt.md",
            drain="development_final_commit",
        )
        state = PipelineState(
            phase="development_final_commit",
            previous_phase="development_final_commit_cleanup",
        )

        result = _run_pipeline_step(
            state=state,
            effect=effect,
            workspace_scope=workspace_scope,
            monkeypatch=monkeypatch,
            stub_materialize=True,
        )

        assert isinstance(result, PipelineState)
        assert not workspace.exists(".agent/artifacts/development_result.md")
        assert not workspace.exists(".agent/DEVELOPMENT_RESULT.md")
        assert not workspace.exists(".agent/artifacts/development_analysis_decision.md")
        assert not workspace.exists(".agent/DEVELOPMENT_ANALYSIS_DECISION.md")

    def test_invoke_agent_effect_recovers_missing_plan_handoff(
        self,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """An InvokeAgentEffect path that raises MissingPlanHandoffError recovers
        to the entry phase instead of leaking to the failure classifier.

        Anchors the real seam in ``_run_pipeline_step`` (the
        ``materialize_agent_prompt_if_needed`` call). Before the recovery
        helper is added, this test fails because the missing-handoff
        exception escapes through the outer ``except BaseException`` arm
        in ``_run_pipeline_step`` and routes to the recovery controller
        instead of landing on the plan-handoff recovery path. After the
        helper lands, the test passes: the recovered state advances to
        planning (entry_phase), recovery_epoch increments to 1, and the
        plan-handoff error text is recorded in last_error.
        """
        workspace_scope = WorkspaceScope(tmp_path)
        effect = InvokeAgentEffect(
            agent_name="claude",
            phase="development",
            prompt_file=".agent/tmp/development_prompt.md",
            drain="development",
        )
        state = PipelineState(
            phase="development",
            previous_phase=None,
            recovery_epoch=0,
        )

        result = _run_pipeline_step(
            state=state,
            effect=effect,
            workspace_scope=workspace_scope,
            monkeypatch=monkeypatch,
            stub_materialize=False,
            raise_missing_plan_handoff=True,
        )

        assert isinstance(result, PipelineState)
        assert result.phase == "planning", (
            f"Recovered phase must be planning (entry_phase), got {result.phase}"
        )
        assert result.recovery_epoch == 1, (
            f"recovery_epoch must be 1 after one recovery, got {result.recovery_epoch}"
        )
        assert "plan handoff" in (result.last_error or ""), (
            f"last_error must describe the plan handoff failure, got {result.last_error!r}"
        )
