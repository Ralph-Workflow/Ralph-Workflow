"""Generic review-role phase handler.

This handler may be registered for any phase declared with role='review'.
It does not assume the phase is named 'review': all emitted events derive
the phase name from the incoming effect's phase attribute.

When no new commits have landed since the last successful review pass, the
handler emits ``REVIEW_CLEAN`` so the reducer routes straight to
``review_commit`` without invoking the reviewer agent. We intentionally treat
any commit since the baseline as a trigger to re-review — even documentation
churn — because the reviewer, not this handler, is the correct judge of which
changes are substantive.
"""

from __future__ import annotations

import json
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

from git import InvalidGitRepositoryError
from loguru import logger

from ralph.git.operations import GitOperationError, get_head_sha, has_commits_since

if TYPE_CHECKING:
    from ralph.phases import PhaseContext
from ralph.phases.artifacts import (
    PhaseArtifactError,
    load_phase_artifact,
    unwrap_phase_artifact_content,
)
from ralph.phases.required_artifacts import build_retry_hint, retry_hint_path
from ralph.pipeline.effects import Effect, InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import (
    Event,
    PhaseFailureEvent,
    PipelineEvent,
)

REVIEW_BASELINE_MARKER = ".agent/tmp/last_reviewed_sha.txt"
REVIEW_ISSUES_ARTIFACT_PATH = ".agent/artifacts/issues.json"


def _workspace_absolute_path(ctx: PhaseContext, rel: str) -> str | None:
    try:
        return ctx.workspace.absolute_path(rel)
    except (AttributeError, TypeError, ValueError):
        return None


def _read_review_baseline(ctx: PhaseContext) -> str | None:
    marker = _workspace_absolute_path(ctx, REVIEW_BASELINE_MARKER)
    if marker is None:
        return None
    marker_path = Path(marker)
    try:
        if not marker_path.exists():
            return None
        sha = marker_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return sha or None


def _write_review_baseline(ctx: PhaseContext, sha: str) -> None:
    marker = _workspace_absolute_path(ctx, REVIEW_BASELINE_MARKER)
    if marker is None:
        return
    marker_path = Path(marker)
    try:
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(sha, encoding="utf-8")
    except OSError as exc:
        logger.debug("Failed to write review baseline marker: {}", exc)


def _current_head_sha(ctx: PhaseContext) -> str | None:
    root = _workspace_absolute_path(ctx, ".")
    if root is None:
        return None
    try:
        return get_head_sha(root)
    except (InvalidGitRepositoryError, GitOperationError, OSError, ValueError):
        return None


def _has_new_commits_since_baseline(ctx: PhaseContext, baseline: str) -> bool:
    root = _workspace_absolute_path(ctx, ".")
    if root is None:
        return True
    try:
        return has_commits_since(root, baseline)
    except (InvalidGitRepositoryError, GitOperationError, OSError, ValueError):
        return True


def _write_retry_hint(ctx: PhaseContext, phase: str, detail: str) -> None:
    hint_path = retry_hint_path(phase)
    hint = build_retry_hint(phase, detail)
    with suppress(Exception):
        ctx.workspace.write(hint_path, hint)


def handle_review(effect: Effect, ctx: PhaseContext) -> list[Event]:
    """Handle the review phase.

    Args:
        effect: The effect that triggered this phase.
        ctx: Phase context with workspace and policy.

    Returns:
        List of events to emit.
    """
    if isinstance(effect, PreparePromptEffect):
        logger.info(
            "Review phase: preparing prompt (pass={})",
            effect.iteration,
        )
        return [PipelineEvent.PROMPT_PREPARED]

    if isinstance(effect, InvokeAgentEffect):
        baseline = _read_review_baseline(ctx)
        if baseline is not None and not _has_new_commits_since_baseline(ctx, baseline):
            logger.info("Review phase: no new commits since {} — skipping review", baseline[:8])
            return [PipelineEvent.REVIEW_CLEAN]

        logger.info("Review phase: processing review result after agent run")
        try:
            artifact_wrapper = load_phase_artifact(ctx.workspace, REVIEW_ISSUES_ARTIFACT_PATH)
            if artifact_wrapper.get("type") != "issues":
                raise PhaseArtifactError("Review issues artifact must declare type='issues'")
            unwrap_phase_artifact_content(
                artifact_wrapper,
                expected_type="issues",
            )
        except (json.JSONDecodeError, PhaseArtifactError, TypeError, ValueError) as exc:
            detail = str(exc)
            logger.warning("Review phase missing fresh issues artifact: {}", detail)
            _write_retry_hint(ctx, effect.phase, detail)
            return [
                PhaseFailureEvent(
                    phase=effect.phase,
                    reason=f"Missing/invalid issues artifact: {detail}",
                    recoverable=True,
                    retry_in_session=True,
                )
            ]

        head = _current_head_sha(ctx)
        if head is not None:
            _write_review_baseline(ctx, head)

        # Check if issues were found and emit REVIEW_ISSUES_FOUND if so
        try:
            artifact_wrapper = load_phase_artifact(ctx.workspace, REVIEW_ISSUES_ARTIFACT_PATH)
            content: object = artifact_wrapper.get("content", {})
            issues: object = content.get("issues", []) if isinstance(content, dict) else []
            if isinstance(issues, list) and issues:
                return [PipelineEvent.REVIEW_ISSUES_FOUND]
        except Exception:
            pass

        return [PipelineEvent.AGENT_SUCCESS]

    return []



