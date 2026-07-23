"""Materialized prompts preserve markdown-artifact workflow context."""

from __future__ import annotations

from pathlib import Path

from ralph.policy.loader import load_policy
from ralph.prompts.materialize import (
    PromptPhaseContext,
    PromptPhaseOptions,
    materialize_prompt_for_phase,
)
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace


def _materialize(
    tmp_path: Path,
    *,
    phase: str,
    previous_phase: str | None = None,
) -> tuple[MemoryWorkspace, str]:
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Migrate artifact prompts")
    policy = load_policy(tmp_path / ".agent")
    drain = SessionDrain.PLANNING if phase.startswith("planning") else SessionDrain.DEVELOPMENT
    path = materialize_prompt_for_phase(
        PromptPhaseContext(
            phase=phase,
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(drain),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(
            artifacts_policy=policy.artifacts,
            previous_phase=previous_phase,
        ),
    )
    return workspace, workspace.read(path)


def test_materialized_planning_prompt_teaches_native_markdown_submission(
    tmp_path: Path,
) -> None:
    _workspace, rendered = _materialize(tmp_path, phase="planning")

    assert "PLANNING MODE" in rendered
    assert "ralph_verify_md_artifact" in rendered
    assert "ralph_submit_md_artifact" in rendered
    assert ".agent/artifact-formats/plan.md" in rendered
    assert "### [S-" in rendered
    assert "ralph_submit_plan_section" not in rendered
    assert "plan.json" not in rendered


def test_fresh_planning_removes_stale_markdown_artifacts(tmp_path: Path) -> None:
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Create a fresh plan")
    workspace.write(".agent/artifacts/plan.md", "---\ntype: plan\n---\n# stale")
    workspace.write(".agent/tmp/plan.md", "---\ntype: plan\n---\n# partial")
    workspace.write(
        ".agent/artifacts/planning_analysis_decision.md",
        "---\ntype: planning_analysis_decision\n---\n# stale",
    )
    policy = load_policy(tmp_path / ".agent")

    materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="planning",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(artifacts_policy=policy.artifacts, previous_phase=None),
    )

    assert not workspace.exists(".agent/artifacts/plan.md")
    assert not workspace.exists(".agent/artifacts/planning_analysis_decision.md")


def test_materialized_development_prompt_reads_markdown_plan(tmp_path: Path) -> None:
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Implement the plan")
    workspace.write(
        ".agent/artifacts/plan.md",
        "---\ntype: plan\n---\n## Summary\nUse canonical markdown.\n",
    )
    workspace.write(".agent/PLAN.md", "## Summary\nUse canonical markdown.\n")
    policy = load_policy(tmp_path / ".agent")

    path = materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="development",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(artifacts_policy=policy.artifacts, previous_phase="planning"),
    )
    rendered = workspace.read(path)

    assert ".agent/PLAN.md" in rendered
    assert "development_result.md" in rendered
    assert "ralph_submit_md_artifact" in rendered


def test_planning_edit_preserves_current_markdown_plan(tmp_path: Path) -> None:
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Revise the plan")
    workspace.write(".agent/artifacts/plan.md", "---\ntype: plan\n---\n# current")
    workspace.write(".agent/PLAN.md", "# current")
    workspace.write(
        ".agent/PLANNING_ANALYSIS_DECISION.md",
        "The verification command is too broad.",
    )
    policy = load_policy(tmp_path / ".agent")

    path = materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="planning",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(
            artifacts_policy=policy.artifacts,
            previous_phase="planning_analysis",
        ),
    )
    rendered = workspace.read(path)

    assert workspace.exists(".agent/artifacts/plan.md")
    assert "ralph_edit_md_plan_step" in rendered
    assert "ralph_verify_md_artifact" in rendered
