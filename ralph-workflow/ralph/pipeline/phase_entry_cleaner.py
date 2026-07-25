"""Phase-entry drain clearing for fresh phase entries.

This module clears canonical Markdown artifacts, Markdown handoffs, and stale
legacy JSON artifacts when a pipeline phase is genuinely entered fresh — on
program start, cross-phase transition, or last-commit re-entry — as opposed to
same-phase retry or analysis loopback where the existing context is preserved.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.artifacts.md_draft_io import md_draft_workspace_path
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
    """Return True when entering a phase via genuine fresh entry.

    Fresh entry means program start, cross-phase transition, or last-commit re-entry.
    Clearing is suppressed for same-phase retry, analysis loopback, and checkpoint
    restore (resume). The resume case is handled by the caller via state-field
    check, not by this function.

    Args:
        entering_phase: The phase being entered.
        previous_phase: The phase that preceded this entry (from PreparePromptEffect).
        pipeline_policy: The active pipeline policy.

    Returns:
        True for genuine fresh entry; False for same-phase or analysis loopback.
    """
    # Same-phase retry or on_loopback self-reference: preserve context
    if previous_phase == entering_phase:
        return False

    # Analysis loopback back into the execution phase: preserve context
    if previous_phase is not None:
        prev_def = pipeline_policy.phases.get(previous_phase)
        if prev_def is not None and prev_def.role == "analysis":
            loopback_target = prev_def.transitions.on_loopback
            if loopback_target == entering_phase:
                return False

    # All other cases are fresh entry:
    # - previous_phase is None (program start or last-commit→planning)
    # - previous_phase is an unrelated normal phase (cross-phase transition)
    # - previous_phase not in pipeline_policy.phases (unknown previous treated as fresh)
    return True


def _clear_drain(
    workspace: Workspace,
    drain_name: str,
    artifacts_policy: ArtifactsPolicy,
) -> None:
    """Clear canonical, handoff, and stale legacy files for a single drain.

    Uses the same path resolution as phase_output_artifact_paths in commit_executor.py.

    Args:
        workspace: The workspace to operate on.
        drain_name: The drain name to clear artifacts for.
        artifacts_policy: The active artifacts policy for path resolution.
    """
    required_artifacts = build_required_artifacts(artifacts_policy)
    required_artifact = required_artifacts.get(drain_name)
    if required_artifact is None:
        return

    # Remove the canonical Markdown artifact before the drain starts.
    if workspace.exists(required_artifact.artifact_path):
        workspace.remove(required_artifact.artifact_path)

    # Explicit legacy cleanup prevents pre-migration state surviving fresh entry.
    legacy_path = f".agent/artifacts/{required_artifact.artifact_type}.json"
    if workspace.exists(legacy_path):
        workspace.remove(legacy_path)

    # Clear the Markdown handoff if one exists.
    md_path = required_artifact.markdown_path
    if md_path is not None and workspace.exists(md_path):
        workspace.remove(md_path)


def _clear_all_drafts(workspace: Workspace, artifacts_policy: ArtifactsPolicy) -> None:
    """Remove every retained markdown draft.

    Submission retains the submitted document as that artifact's draft so it
    can be repaired in place with ``ralph_edit_md_artifact``. A draft is scoped
    to one phase attempt: same-phase retry and analysis loopback resume from
    it, but a genuine fresh entry must not.

    Clearing is deliberately independent of ``clear_drains_on_fresh_entry``.
    Canonical artifacts legitimately outlive their producing phase — entering
    ``development`` must not delete ``plan.md`` — but the *draft* behind them
    must not, or an agent would repair the previous phase's text.
    """
    for required_artifact in build_required_artifacts(artifacts_policy).values():
        draft_path = md_draft_workspace_path(required_artifact.artifact_type)
        if workspace.exists(draft_path):
            workspace.remove(draft_path)


def _seed_drafts_from_canonical(workspace: Workspace, artifacts_policy: ArtifactsPolicy) -> None:
    """Refill missing drafts from the canonical artifact on a resumed attempt.

    A retry or analysis loopback re-enters to revise work that was already
    submitted — most commonly a plan that planning-analysis sent back for
    changes. The agent should find that rejected document already loaded as
    the draft, ready to repair with ``ralph_edit_md_artifact``, rather than
    having to re-transcribe it.

    Normally submission already left the draft in place. This seeding covers
    the paths that did not: a run resumed from a checkpoint, a document
    promoted from the ``.agent/tmp`` markdown fallback, or an artifact
    submitted before drafts were retained.

    An existing draft always wins — it may hold in-progress repairs that are
    deliberately ahead of the canonical artifact.
    """
    for required_artifact in build_required_artifacts(artifacts_policy).values():
        draft_path = md_draft_workspace_path(required_artifact.artifact_type)
        if workspace.exists(draft_path):
            continue
        artifact_path = required_artifact.artifact_path
        if not workspace.exists(artifact_path):
            continue
        workspace.write(draft_path, workspace.read(artifact_path))


def clear_phase_entry_drains(
    workspace: Workspace,
    phase_name: str,
    previous_phase: str | None,
    pipeline_policy: PipelinePolicy,
    artifacts_policy: ArtifactsPolicy,
) -> None:
    """Clear declared drain artifacts on genuine fresh phase entry.

    Clears the canonical Markdown artifact and handoff for each drain listed in
    the phase's clear_drains_on_fresh_entry field, but only when
    is_fresh_phase_entry returns True. Retained markdown drafts are cleared on
    the same signal but across every artifact type, not just declared drains —
    see :func:`_clear_all_drafts`.

    Args:
        workspace: The workspace to operate on.
        phase_name: The phase being entered.
        previous_phase: The previous phase from PreparePromptEffect.
        pipeline_policy: The active pipeline policy.
        artifacts_policy: The active artifacts policy.
    """
    phase_def = pipeline_policy.phases.get(phase_name)
    if phase_def is None:
        return

    if not is_fresh_phase_entry(phase_name, previous_phase, pipeline_policy):
        # Retry / loopback: the attempt continues, so nothing is cleared and
        # the rejected document is loaded back as the editable draft.
        _seed_drafts_from_canonical(workspace, artifacts_policy)
        return

    for drain in phase_def.clear_drains_on_fresh_entry:
        _clear_drain(workspace, drain, artifacts_policy)

    _clear_all_drafts(workspace, artifacts_policy)
