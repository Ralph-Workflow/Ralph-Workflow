
from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.artifacts.md_draft_io import md_draft_workspace_path, seeded_draft_workspace_path
from ralph.phases.required_artifacts import build_required_artifacts

if TYPE_CHECKING:
    from ralph.policy.models import ArtifactsPolicy, PipelinePolicy
    from ralph.workspace.protocol import Workspace


__all__ = [
    "clear_phase_entry_drains",
    "is_fresh_phase_entry",
]


def is_fresh_phase_entry(
    entering_phase: str,
    previous_phase: str | None,
    pipeline_policy: PipelinePolicy,
) -> bool:
    if previous_phase == entering_phase:
        return False

    if previous_phase is not None:
        prev_def = pipeline_policy.phases.get(previous_phase)
        if prev_def is not None and prev_def.role == "analysis":
            loopback_target = prev_def.transitions.on_loopback
            if loopback_target == entering_phase:
                return False

    return True


def _clear_drain(
    workspace: Workspace,
    drain_name: str,
    artifacts_policy: ArtifactsPolicy,
) -> None:
    required_artifacts = build_required_artifacts(artifacts_policy)
    required_artifact = required_artifacts.get(drain_name)
    if required_artifact is None:
        return

    if workspace.exists(required_artifact.artifact_path):
        workspace.remove(required_artifact.artifact_path)

    legacy_path = f".agent/artifacts/{required_artifact.artifact_type}.json"
    if workspace.exists(legacy_path):
        workspace.remove(legacy_path)

    md_path = required_artifact.markdown_path
    if md_path is not None and workspace.exists(md_path):
        workspace.remove(md_path)


def _clear_all_drafts(workspace: Workspace, artifacts_policy: ArtifactsPolicy) -> None:
    for required_artifact in build_required_artifacts(artifacts_policy).values():
        artifact_type = required_artifact.artifact_type
        draft_path = md_draft_workspace_path(artifact_type)
        if workspace.exists(draft_path):
            workspace.remove(draft_path)
        seeded_path = seeded_draft_workspace_path(artifact_type)
        if workspace.exists(seeded_path):
            workspace.remove(seeded_path)


def _seed_drafts_from_canonical(workspace: Workspace, artifacts_policy: ArtifactsPolicy) -> None:
    for required_artifact in build_required_artifacts(artifacts_policy).values():
        draft_path = md_draft_workspace_path(required_artifact.artifact_type)
        if workspace.exists(draft_path):
            continue
        artifact_path = required_artifact.artifact_path
        if not workspace.exists(artifact_path):
            continue
        workspace.write(draft_path, workspace.read(artifact_path))
        workspace.write(seeded_draft_workspace_path(required_artifact.artifact_type), "")


def clear_phase_entry_drains(
    workspace: Workspace,
    phase_name: str,
    previous_phase: str | None,
    pipeline_policy: PipelinePolicy,
    artifacts_policy: ArtifactsPolicy,
) -> None:
    phase_def = pipeline_policy.phases.get(phase_name)
    if phase_def is None:
        return

    if not is_fresh_phase_entry(phase_name, previous_phase, pipeline_policy):
        _seed_drafts_from_canonical(workspace, artifacts_policy)
        return

    for drain in phase_def.clear_drains_on_fresh_entry:
        _clear_drain(workspace, drain, artifacts_policy)

    _clear_all_drafts(workspace, artifacts_policy)
