from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import PreparePromptEffect
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.policy.models import ArtifactsPolicy, PipelinePolicy


def _load_default_policy_bundle() -> tuple[PipelinePolicy, ArtifactsPolicy]:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    bundle = load_policy(defaults_dir)
    return bundle.pipeline, bundle.artifacts


def _write_artifact_files(
    ws: FsWorkspace,
    artifact_type: str,
    artifact_path: str,
    md_path: str | None,
) -> None:
    ws.mkdirs(Path(artifact_path).parent.as_posix())
    document = f"---\ntype: {artifact_type}\n---\n## Summary\n- [S1] test\n"
    ws.write(artifact_path, document)
    if md_path:
        ws.write(md_path, document)


class TestHandleInlineEffectPhaseEntryClearing:
    def test_fresh_planning_entry_clears_artifacts(self, tmp_path: Path) -> None:
        pipeline, artifacts_policy = _load_default_policy_bundle()

        root = tmp_path / ".agent"
        root.mkdir(parents=True)
        ws = FsWorkspace(root)

        _write_artifact_files(ws, "plan", ".agent/artifacts/plan.md", ".agent/PLAN.md")
        _write_artifact_files(
            ws,
            "planning_analysis_decision",
            ".agent/artifacts/planning_analysis_decision.md",
            ".agent/PLANNING_ANALYSIS_DECISION.md",
        )
        _write_artifact_files(
            ws,
            "development_analysis_decision",
            ".agent/artifacts/development_analysis_decision.md",
            ".agent/DEVELOPMENT_ANALYSIS_DECISION.md",
        )

        effect = PreparePromptEffect(
            phase="planning", previous_phase=None, drain="planning", iteration=0
        )
        state = PipelineState(phase="planning", previous_phase=None, checkpoint_saved_count=0)

        original = runner_module.materialize_prepared_prompt
        runner_module.materialize_prepared_prompt = lambda *a, **k: None

        try:
            runner_module.handle_inline_effect(
                effect=effect,
                state=state,
                pipeline_policy=pipeline,
                artifacts_policy=artifacts_policy,
                workspace_scope=WorkspaceScope(root=root, allowed_roots=frozenset([root])),
            )
        finally:
            runner_module.materialize_prepared_prompt = original

        assert not ws.exists(".agent/artifacts/plan.md")
        assert not ws.exists(".agent/PLAN.md")
        assert not ws.exists(".agent/artifacts/planning_analysis_decision.md")
        assert not ws.exists(".agent/PLANNING_ANALYSIS_DECISION.md")
        assert not ws.exists(".agent/artifacts/development_analysis_decision.md")
        assert not ws.exists(".agent/DEVELOPMENT_ANALYSIS_DECISION.md")

    def test_planning_to_development_clears_analysis_and_dev(self, tmp_path: Path) -> None:
        pipeline, artifacts_policy = _load_default_policy_bundle()
        root = tmp_path / ".agent"
        root.mkdir(parents=True)
        ws = FsWorkspace(root)

        _write_artifact_files(
            ws,
            "planning_analysis_decision",
            ".agent/artifacts/planning_analysis_decision.md",
            ".agent/PLANNING_ANALYSIS_DECISION.md",
        )
        _write_artifact_files(
            ws,
            "development_result",
            ".agent/artifacts/development_result.md",
            ".agent/DEVELOPMENT_RESULT.md",
        )
        _write_artifact_files(
            ws,
            "development_analysis_decision",
            ".agent/artifacts/development_analysis_decision.md",
            ".agent/DEVELOPMENT_ANALYSIS_DECISION.md",
        )

        effect = PreparePromptEffect(
            phase="development",
            previous_phase="planning_analysis",
            drain="development",
            iteration=0,
        )
        state = PipelineState(
            phase="development", previous_phase="planning_analysis", checkpoint_saved_count=0
        )

        original = runner_module.materialize_prepared_prompt
        runner_module.materialize_prepared_prompt = lambda *a, **k: None

        try:
            runner_module.handle_inline_effect(
                effect=effect,
                state=state,
                pipeline_policy=pipeline,
                artifacts_policy=artifacts_policy,
                workspace_scope=WorkspaceScope(root=root, allowed_roots=frozenset([root])),
            )
        finally:
            runner_module.materialize_prepared_prompt = original

        assert not ws.exists(".agent/artifacts/planning_analysis_decision.md")
        assert not ws.exists(".agent/PLANNING_ANALYSIS_DECISION.md")
        assert not ws.exists(".agent/artifacts/development_result.md")
        assert not ws.exists(".agent/DEVELOPMENT_RESULT.md")
        assert not ws.exists(".agent/artifacts/development_analysis_decision.md")
        assert not ws.exists(".agent/DEVELOPMENT_ANALYSIS_DECISION.md")

    def test_development_commit_clears_dev_and_analysis(self, tmp_path: Path) -> None:
        pipeline, artifacts_policy = _load_default_policy_bundle()
        root = tmp_path / ".agent"
        root.mkdir(parents=True)
        ws = FsWorkspace(root)

        _write_artifact_files(
            ws,
            "development_result",
            ".agent/artifacts/development_result.md",
            ".agent/DEVELOPMENT_RESULT.md",
        )
        _write_artifact_files(
            ws,
            "development_analysis_decision",
            ".agent/artifacts/development_analysis_decision.md",
            ".agent/DEVELOPMENT_ANALYSIS_DECISION.md",
        )

        effect = PreparePromptEffect(
            phase="development_commit",
            previous_phase="development_analysis",
            drain="development_commit",
            iteration=0,
        )
        state = PipelineState(
            phase="development_commit",
            previous_phase="development_analysis",
            checkpoint_saved_count=0,
        )

        original = runner_module.materialize_prepared_prompt
        runner_module.materialize_prepared_prompt = lambda *a, **k: None

        try:
            runner_module.handle_inline_effect(
                effect=effect,
                state=state,
                pipeline_policy=pipeline,
                artifacts_policy=artifacts_policy,
                workspace_scope=WorkspaceScope(root=root, allowed_roots=frozenset([root])),
            )
        finally:
            runner_module.materialize_prepared_prompt = original

        assert not ws.exists(".agent/artifacts/development_result.md")
        assert not ws.exists(".agent/DEVELOPMENT_RESULT.md")
        assert not ws.exists(".agent/artifacts/development_analysis_decision.md")
        assert not ws.exists(".agent/DEVELOPMENT_ANALYSIS_DECISION.md")

    def test_planning_resume_does_not_clear(self, tmp_path: Path) -> None:
        pipeline, artifacts_policy = _load_default_policy_bundle()
        root = tmp_path / ".agent"
        root.mkdir(parents=True)
        ws = FsWorkspace(root)

        _write_artifact_files(ws, "plan", ".agent/artifacts/plan.md", ".agent/PLAN.md")

        effect = PreparePromptEffect(
            phase="planning", previous_phase=None, drain="planning", iteration=0
        )
        state = PipelineState(phase="planning", previous_phase=None, checkpoint_saved_count=5)

        original = runner_module.materialize_prepared_prompt
        runner_module.materialize_prepared_prompt = lambda *a, **k: None

        try:
            runner_module.handle_inline_effect(
                effect=effect,
                state=state,
                pipeline_policy=pipeline,
                artifacts_policy=artifacts_policy,
                workspace_scope=WorkspaceScope(root=root, allowed_roots=frozenset([root])),
            )
        finally:
            runner_module.materialize_prepared_prompt = original

        assert ws.exists(".agent/artifacts/plan.md")
        assert ws.exists(".agent/PLAN.md")

    def test_analysis_loopback_does_not_clear(self, tmp_path: Path) -> None:
        pipeline, artifacts_policy = _load_default_policy_bundle()
        root = tmp_path / ".agent"
        root.mkdir(parents=True)
        ws = FsWorkspace(root)

        _write_artifact_files(ws, "plan", ".agent/artifacts/plan.md", ".agent/PLAN.md")

        effect = PreparePromptEffect(
            phase="planning", previous_phase="planning_analysis", drain="planning", iteration=3
        )
        state = PipelineState(
            phase="planning", previous_phase="planning_analysis", checkpoint_saved_count=0
        )

        original = runner_module.materialize_prepared_prompt
        runner_module.materialize_prepared_prompt = lambda *a, **k: None

        try:
            runner_module.handle_inline_effect(
                effect=effect,
                state=state,
                pipeline_policy=pipeline,
                artifacts_policy=artifacts_policy,
                workspace_scope=WorkspaceScope(root=root, allowed_roots=frozenset([root])),
            )
        finally:
            runner_module.materialize_prepared_prompt = original

        assert ws.exists(".agent/artifacts/plan.md")
        assert ws.exists(".agent/PLAN.md")

    def test_same_phase_loopback_does_not_clear(self, tmp_path: Path) -> None:
        pipeline, artifacts_policy = _load_default_policy_bundle()
        root = tmp_path / ".agent"
        root.mkdir(parents=True)
        ws = FsWorkspace(root)

        _write_artifact_files(
            ws,
            "development_result",
            ".agent/artifacts/development_result.md",
            ".agent/DEVELOPMENT_RESULT.md",
        )

        effect = PreparePromptEffect(
            phase="development", previous_phase="development", drain="development", iteration=2
        )
        state = PipelineState(
            phase="development", previous_phase="development", checkpoint_saved_count=0
        )

        original = runner_module.materialize_prepared_prompt
        runner_module.materialize_prepared_prompt = lambda *a, **k: None

        try:
            runner_module.handle_inline_effect(
                effect=effect,
                state=state,
                pipeline_policy=pipeline,
                artifacts_policy=artifacts_policy,
                workspace_scope=WorkspaceScope(root=root, allowed_roots=frozenset([root])),
            )
        finally:
            runner_module.materialize_prepared_prompt = original

        assert ws.exists(".agent/artifacts/development_result.md")
        assert ws.exists(".agent/DEVELOPMENT_RESULT.md")

    def test_development_commit_to_planning_clears_planning(self, tmp_path: Path) -> None:
        pipeline, artifacts_policy = _load_default_policy_bundle()
        root = tmp_path / ".agent"
        root.mkdir(parents=True)
        ws = FsWorkspace(root)

        _write_artifact_files(ws, "plan", ".agent/artifacts/plan.md", ".agent/PLAN.md")
        _write_artifact_files(
            ws,
            "planning_analysis_decision",
            ".agent/artifacts/planning_analysis_decision.md",
            ".agent/PLANNING_ANALYSIS_DECISION.md",
        )
        _write_artifact_files(
            ws,
            "development_analysis_decision",
            ".agent/artifacts/development_analysis_decision.md",
            ".agent/DEVELOPMENT_ANALYSIS_DECISION.md",
        )

        effect = PreparePromptEffect(
            phase="planning", previous_phase="development_commit", drain="planning", iteration=0
        )
        state = PipelineState(
            phase="planning",
            previous_phase="development_commit",
            checkpoint_saved_count=5,
        )

        original = runner_module.materialize_prepared_prompt
        runner_module.materialize_prepared_prompt = lambda *a, **k: None

        try:
            runner_module.handle_inline_effect(
                effect=effect,
                state=state,
                pipeline_policy=pipeline,
                artifacts_policy=artifacts_policy,
                workspace_scope=WorkspaceScope(root=root, allowed_roots=frozenset([root])),
            )
        finally:
            runner_module.materialize_prepared_prompt = original

        assert not ws.exists(".agent/artifacts/plan.md")
        assert not ws.exists(".agent/PLAN.md")
        assert not ws.exists(".agent/artifacts/planning_analysis_decision.md")
        assert not ws.exists(".agent/PLANNING_ANALYSIS_DECISION.md")
        assert not ws.exists(".agent/artifacts/development_analysis_decision.md")
        assert not ws.exists(".agent/DEVELOPMENT_ANALYSIS_DECISION.md")

    def test_resumed_non_planning_phase_does_not_clear(self, tmp_path: Path) -> None:
        pipeline, artifacts_policy = _load_default_policy_bundle()
        root = tmp_path / ".agent"
        root.mkdir(parents=True)
        ws = FsWorkspace(root)

        _write_artifact_files(
            ws,
            "development_result",
            ".agent/artifacts/development_result.md",
            ".agent/DEVELOPMENT_RESULT.md",
        )

        effect = PreparePromptEffect(
            phase="development", previous_phase=None, drain="development", iteration=0
        )
        state = PipelineState(phase="development", previous_phase=None, checkpoint_saved_count=5)

        original = runner_module.materialize_prepared_prompt
        runner_module.materialize_prepared_prompt = lambda *a, **k: None

        try:
            runner_module.handle_inline_effect(
                effect=effect,
                state=state,
                pipeline_policy=pipeline,
                artifacts_policy=artifacts_policy,
                workspace_scope=WorkspaceScope(root=root, allowed_roots=frozenset([root])),
            )
        finally:
            runner_module.materialize_prepared_prompt = original

        assert ws.exists(".agent/artifacts/development_result.md")
        assert ws.exists(".agent/DEVELOPMENT_RESULT.md")
