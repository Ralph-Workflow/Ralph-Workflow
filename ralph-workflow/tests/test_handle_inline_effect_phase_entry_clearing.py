"""Tests for PreparePromptEffect boundary clearing via handle_inline_effect.

These tests verify that artifact clearing fires correctly on fresh phase entry
and is suppressed on resume, same-phase retry, and analysis loopback.
"""

from __future__ import annotations

import json
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
    json_path: str,
    md_path: str | None,
) -> None:
    """Write minimal artifact files for a given artifact type."""
    ws.mkdirs(Path(json_path).parent.as_posix())
    ws.write(json_path, json.dumps({"type": artifact_type, "content": "test"}))
    if md_path:
        ws.write(md_path, f"# {artifact_type}\n\ntest content")


class TestHandleInlineEffectPhaseEntryClearing:
    """PreparePromptEffect handler clears drains on genuine fresh entry."""

    def test_fresh_planning_entry_clears_artifacts(self, tmp_path: Path) -> None:
        """Fresh planning entry (checkpoint_saved_count=0, previous_phase=None) clears artifacts."""
        pipeline, artifacts_policy = _load_default_policy_bundle()

        root = tmp_path / ".agent"
        root.mkdir(parents=True)
        ws = FsWorkspace(root)

        # Pre-create all 6 planning, planning_analysis, and development_analysis files
        _write_artifact_files(ws, "plan", ".agent/artifacts/plan.json", ".agent/PLAN.md")
        _write_artifact_files(
            ws,
            "planning_analysis_decision",
            ".agent/artifacts/planning_analysis_decision.json",
            ".agent/PLANNING_ANALYSIS_DECISION.md",
        )
        _write_artifact_files(
            ws,
            "development_analysis_decision",
            ".agent/artifacts/development_analysis_decision.json",
            ".agent/DEVELOPMENT_ANALYSIS_DECISION.md",
        )

        effect = PreparePromptEffect(
            phase="planning", previous_phase=None, drain="planning", iteration=0
        )
        state = PipelineState(phase="planning", previous_phase=None, checkpoint_saved_count=0)

        # Monkeypatch materialize_prepared_prompt to avoid actual materialization
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

        # All 6 files cleared
        assert not ws.exists(".agent/artifacts/plan.json")
        assert not ws.exists(".agent/PLAN.md")
        assert not ws.exists(".agent/artifacts/planning_analysis_decision.json")
        assert not ws.exists(".agent/PLANNING_ANALYSIS_DECISION.md")
        assert not ws.exists(".agent/artifacts/development_analysis_decision.json")
        assert not ws.exists(".agent/DEVELOPMENT_ANALYSIS_DECISION.md")

    def test_planning_to_development_clears_analysis_and_dev(self, tmp_path: Path) -> None:
        """Fresh development entry clears planning_analysis + dev + dev_analysis artifacts."""
        pipeline, artifacts_policy = _load_default_policy_bundle()
        root = tmp_path / ".agent"
        root.mkdir(parents=True)
        ws = FsWorkspace(root)

        # Pre-create all 6 files
        _write_artifact_files(
            ws,
            "planning_analysis_decision",
            ".agent/artifacts/planning_analysis_decision.json",
            ".agent/PLANNING_ANALYSIS_DECISION.md",
        )
        _write_artifact_files(
            ws,
            "development_result",
            ".agent/artifacts/development_result.json",
            ".agent/DEVELOPMENT_RESULT.md",
        )
        _write_artifact_files(
            ws,
            "development_analysis_decision",
            ".agent/artifacts/development_analysis_decision.json",
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

        # All 6 files cleared
        assert not ws.exists(".agent/artifacts/planning_analysis_decision.json")
        assert not ws.exists(".agent/PLANNING_ANALYSIS_DECISION.md")
        assert not ws.exists(".agent/artifacts/development_result.json")
        assert not ws.exists(".agent/DEVELOPMENT_RESULT.md")
        assert not ws.exists(".agent/artifacts/development_analysis_decision.json")
        assert not ws.exists(".agent/DEVELOPMENT_ANALYSIS_DECISION.md")

    def test_development_commit_clears_dev_and_analysis(self, tmp_path: Path) -> None:
        """Fresh development_commit entry clears development + development_analysis artifacts."""
        pipeline, artifacts_policy = _load_default_policy_bundle()
        root = tmp_path / ".agent"
        root.mkdir(parents=True)
        ws = FsWorkspace(root)

        _write_artifact_files(
            ws,
            "development_result",
            ".agent/artifacts/development_result.json",
            ".agent/DEVELOPMENT_RESULT.md",
        )
        _write_artifact_files(
            ws,
            "development_analysis_decision",
            ".agent/artifacts/development_analysis_decision.json",
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

        # All 4 files cleared
        assert not ws.exists(".agent/artifacts/development_result.json")
        assert not ws.exists(".agent/DEVELOPMENT_RESULT.md")
        assert not ws.exists(".agent/artifacts/development_analysis_decision.json")
        assert not ws.exists(".agent/DEVELOPMENT_ANALYSIS_DECISION.md")

    def test_planning_resume_does_not_clear(self, tmp_path: Path) -> None:
        """Planning resume (checkpoint_saved_count>0, previous_phase=None) suppresses clearing."""
        pipeline, artifacts_policy = _load_default_policy_bundle()
        root = tmp_path / ".agent"
        root.mkdir(parents=True)
        ws = FsWorkspace(root)

        _write_artifact_files(ws, "plan", ".agent/artifacts/plan.json", ".agent/PLAN.md")

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

        # Files still exist (resume guard suppressed clearing)
        assert ws.exists(".agent/artifacts/plan.json")
        assert ws.exists(".agent/PLAN.md")

    def test_analysis_loopback_does_not_clear(self, tmp_path: Path) -> None:
        """Analysis loopback (previous_phase='planning_analysis') suppresses clearing."""
        pipeline, artifacts_policy = _load_default_policy_bundle()
        root = tmp_path / ".agent"
        root.mkdir(parents=True)
        ws = FsWorkspace(root)

        _write_artifact_files(ws, "plan", ".agent/artifacts/plan.json", ".agent/PLAN.md")

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

        # Files still exist (analysis loopback, not fresh)
        assert ws.exists(".agent/artifacts/plan.json")
        assert ws.exists(".agent/PLAN.md")

    def test_same_phase_loopback_does_not_clear(self, tmp_path: Path) -> None:
        """Same-phase loopback (previous_phase='development') suppresses clearing."""
        pipeline, artifacts_policy = _load_default_policy_bundle()
        root = tmp_path / ".agent"
        root.mkdir(parents=True)
        ws = FsWorkspace(root)

        _write_artifact_files(
            ws,
            "development_result",
            ".agent/artifacts/development_result.json",
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

        # Files still exist
        assert ws.exists(".agent/artifacts/development_result.json")
        assert ws.exists(".agent/DEVELOPMENT_RESULT.md")

    def test_development_commit_to_planning_clears_planning(self, tmp_path: Path) -> None:
        """Last-commit re-entry (previous_phase='development_commit') clears planning artifacts."""
        pipeline, artifacts_policy = _load_default_policy_bundle()
        root = tmp_path / ".agent"
        root.mkdir(parents=True)
        ws = FsWorkspace(root)

        _write_artifact_files(ws, "plan", ".agent/artifacts/plan.json", ".agent/PLAN.md")
        _write_artifact_files(
            ws,
            "planning_analysis_decision",
            ".agent/artifacts/planning_analysis_decision.json",
            ".agent/PLANNING_ANALYSIS_DECISION.md",
        )
        _write_artifact_files(
            ws,
            "development_analysis_decision",
            ".agent/artifacts/development_analysis_decision.json",
            ".agent/DEVELOPMENT_ANALYSIS_DECISION.md",
        )

        effect = PreparePromptEffect(
            phase="planning", previous_phase="development_commit", drain="planning", iteration=0
        )
        state = PipelineState(
            phase="planning",
            previous_phase="development_commit",
            checkpoint_saved_count=5,  # checkpoint exists but previous_phase != None
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

        # All 6 files cleared despite checkpoint_saved_count > 0
        assert not ws.exists(".agent/artifacts/plan.json")
        assert not ws.exists(".agent/PLAN.md")
        assert not ws.exists(".agent/artifacts/planning_analysis_decision.json")
        assert not ws.exists(".agent/PLANNING_ANALYSIS_DECISION.md")
        assert not ws.exists(".agent/artifacts/development_analysis_decision.json")
        assert not ws.exists(".agent/DEVELOPMENT_ANALYSIS_DECISION.md")

    def test_resumed_non_planning_phase_does_not_clear(self, tmp_path: Path) -> None:
        """Non-planning resume: checkpoint_saved_count>0, previous_phase=None."""
        pipeline, artifacts_policy = _load_default_policy_bundle()
        root = tmp_path / ".agent"
        root.mkdir(parents=True)
        ws = FsWorkspace(root)

        _write_artifact_files(
            ws,
            "development_result",
            ".agent/artifacts/development_result.json",
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

        # Files still exist (non-planning resume guard suppressed clearing)
        assert ws.exists(".agent/artifacts/development_result.json")
        assert ws.exists(".agent/DEVELOPMENT_RESULT.md")
