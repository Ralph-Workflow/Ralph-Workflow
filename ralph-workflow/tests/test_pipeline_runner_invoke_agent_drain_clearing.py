"""Runner regressions for fresh-entry drain clearing on InvokeAgentEffect paths."""

from __future__ import annotations

import json
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
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pytest import MonkeyPatch

    from ralph.policy.models import PolicyBundle


@lru_cache(maxsize=1)
def _load_default_policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _write_artifact_files(
    workspace: FsWorkspace,
    artifact_type: str,
    json_path: str,
    md_path: str | None,
) -> None:
    workspace.mkdirs(Path(json_path).parent.as_posix())
    workspace.write(json_path, json.dumps({"type": artifact_type, "content": "test"}))
    if md_path is not None:
        workspace.write(md_path, f"# {artifact_type}\n\ntest content")


def _run_pipeline_step(
    *,
    state: PipelineState,
    effect: InvokeAgentEffect,
    workspace_scope: WorkspaceScope,
    monkeypatch: MonkeyPatch,
    stub_materialize: bool,
) -> object:
    bundle = _load_default_policy_bundle()

    monkeypatch.setattr(
        runner_module,
        "call_determine_effect_from_policy",
        lambda *_args, **_kwargs: effect,
    )
    if stub_materialize:
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
    monkeypatch.setattr(runner_module.ckpt, "save", lambda _state: None)

    display_context = make_display_context()
    display = runner_module.LegacyConsoleDisplay(display_context)
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
            ".agent/artifacts/development_analysis_decision.json",
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
        assert not workspace.exists(".agent/artifacts/development_analysis_decision.json")
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
            ".agent/artifacts/development_analysis_decision.json",
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
        assert workspace.exists(".agent/artifacts/development_analysis_decision.json")
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
            ".agent/artifacts/development_analysis_decision.json",
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
        assert workspace.exists(".agent/artifacts/development_analysis_decision.json")
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
            ".agent/artifacts/development_analysis_decision.json",
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
        assert workspace.exists(".agent/artifacts/development_analysis_decision.json")
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
        _write_artifact_files(workspace, "plan", ".agent/artifacts/plan.json", ".agent/PLAN.md")
        _write_artifact_files(
            workspace,
            "planning_analysis_decision",
            ".agent/artifacts/planning_analysis_decision.json",
            ".agent/PLANNING_ANALYSIS_DECISION.md",
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
        assert not workspace.exists(".agent/artifacts/plan.json")
        assert not workspace.exists(".agent/PLAN.md")
        assert not workspace.exists(".agent/artifacts/planning_analysis_decision.json")
        assert not workspace.exists(".agent/PLANNING_ANALYSIS_DECISION.md")

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
            ".agent/artifacts/development_result.json",
            ".agent/DEVELOPMENT_RESULT.md",
        )
        _write_artifact_files(
            workspace,
            "development_analysis_decision",
            ".agent/artifacts/development_analysis_decision.json",
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
        assert not workspace.exists(".agent/artifacts/development_result.json")
        assert not workspace.exists(".agent/DEVELOPMENT_RESULT.md")
        assert not workspace.exists(".agent/artifacts/development_analysis_decision.json")
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
            ".agent/artifacts/development_result.json",
            ".agent/DEVELOPMENT_RESULT.md",
        )
        _write_artifact_files(
            workspace,
            "development_analysis_decision",
            ".agent/artifacts/development_analysis_decision.json",
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
        assert not workspace.exists(".agent/artifacts/development_result.json")
        assert not workspace.exists(".agent/DEVELOPMENT_RESULT.md")
        assert not workspace.exists(".agent/artifacts/development_analysis_decision.json")
        assert not workspace.exists(".agent/DEVELOPMENT_ANALYSIS_DECISION.md")
