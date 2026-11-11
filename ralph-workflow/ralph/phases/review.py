
from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from git import InvalidGitRepositoryError
from loguru import logger

from ralph.git.errors import GitOperationError
from ralph.git.operations import get_head_sha, has_commits_since
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.phases import PhaseContext
from ralph.phases.artifact_freshness import (
    is_stale_since_invocation,
    stale_artifact_detail,
)
from ralph.phases.artifacts import (
    PhaseArtifactError,
    artifact_validation_failure_event,
    load_phase_artifact,
    unwrap_phase_artifact_content,
)
from ralph.phases.required_artifacts import (
    build_retry_hint,
    clear_validation_retry_hint,
    retry_hint_path,
)
from ralph.pipeline.effects import Effect, InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import Event, PipelineEvent

REVIEW_BASELINE_MARKER = ".agent/tmp/last_reviewed_sha.txt"
REVIEW_ISSUES_ARTIFACT_PATH = ".agent/artifacts/issues.md"


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


def _persist_review_baseline(
    marker_path: Path,
    sha: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    try:
        write_text_if_changed(
            backend,
            marker_path,
            sha,
            prepare_write=lambda: backend.mkdir(marker_path.parent, parents=True, exist_ok=True),
        )
    except OSError as exc:
        logger.debug("Failed to write review baseline marker: {}", exc)


def _write_review_baseline(ctx: PhaseContext, sha: str) -> None:
    marker = _workspace_absolute_path(ctx, REVIEW_BASELINE_MARKER)
    if marker is None:
        return
    _persist_review_baseline(Path(marker), sha)


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




def _production_read_baseline(ctx: PhaseContext) -> str | None:
    return _read_review_baseline(ctx)


def _production_has_new_commits(ctx: PhaseContext, baseline: str) -> bool:
    return _has_new_commits_since_baseline(ctx, baseline)


def _production_current_head_sha(ctx: PhaseContext) -> str | None:
    return _current_head_sha(ctx)


def _production_write_baseline(ctx: PhaseContext, sha: str) -> None:
    _write_review_baseline(ctx, sha)


@dataclass(frozen=True)
class ReviewCollaborators:

    read_baseline: Callable[[PhaseContext], str | None] = _production_read_baseline

    has_new_commits: Callable[[PhaseContext, str], bool] = _production_has_new_commits

    current_head_sha: Callable[[PhaseContext], str | None] = _production_current_head_sha

    write_baseline: Callable[[PhaseContext, str], None] = _production_write_baseline


DEFAULT_REVIEW_COLLABORATORS: Final[ReviewCollaborators] = ReviewCollaborators()


def _write_retry_hint(ctx: PhaseContext, phase: str, detail: str) -> None:
    hint_path = retry_hint_path(phase, pipeline_policy=ctx.pipeline_policy)
    hint = build_retry_hint(phase, detail)
    with suppress(Exception):
        if ctx.workspace.exists(hint_path):
            existing = ctx.workspace.read(hint_path).strip()
            if existing:
                hint = f"{existing}\n\n{hint}"
        ctx.workspace.write(hint_path, hint)


def _load_review_issues(ctx: PhaseContext, phase: str | None = None) -> dict[str, object]:
    if phase is not None and is_stale_since_invocation(
        ctx.workspace, phase, REVIEW_ISSUES_ARTIFACT_PATH
    ):
        raise PhaseArtifactError(stale_artifact_detail(REVIEW_ISSUES_ARTIFACT_PATH, "issues"))
    artifact_wrapper = load_phase_artifact(
        ctx.workspace,
        REVIEW_ISSUES_ARTIFACT_PATH,
        artifact_type="issues",
    )
    if artifact_wrapper.get("type") != "issues":
        raise PhaseArtifactError("Review issues artifact must declare type='issues'")
    return unwrap_phase_artifact_content(
        artifact_wrapper,
        expected_type="issues",
    )


def handle_review(
    effect: Effect,
    ctx: PhaseContext,
    *,
    collaborators: ReviewCollaborators = DEFAULT_REVIEW_COLLABORATORS,
) -> list[Event]:
    if isinstance(effect, PreparePromptEffect):
        logger.info(
            "Review phase: preparing prompt (pass={})",
            effect.iteration,
        )
        return [PipelineEvent.PROMPT_PREPARED]

    if isinstance(effect, InvokeAgentEffect):
        baseline = collaborators.read_baseline(ctx)
        if baseline is not None and not collaborators.has_new_commits(ctx, baseline):
            logger.info("Review phase: no new commits since {} — skipping review", baseline[:8])
            return [PipelineEvent.REVIEW_CLEAN]

        logger.info("Review phase: processing review result after agent run")
        freshness_phase: str | None = effect.phase if hasattr(effect, "phase") else None
        try:
            content = _load_review_issues(ctx, freshness_phase)
        except (PhaseArtifactError, TypeError, ValueError) as exc:
            detail = str(exc)
            logger.warning("Review phase missing fresh issues artifact: {}", detail)
            _write_retry_hint(ctx, effect.phase, detail)
            return [
                artifact_validation_failure_event(
                    phase=effect.phase,
                    reason=f"Missing/invalid issues artifact: {detail}",
                )
            ]

        head = collaborators.current_head_sha(ctx)
        if head is not None:
            collaborators.write_baseline(ctx, head)

        try:
            phase_name = effect.phase
        except AttributeError:
            phase_name = "review"
        clear_validation_retry_hint(
            ctx.workspace,
            phase_name,
            pipeline_policy=ctx.pipeline_policy,
        )

        issues = content.get("issues", [])
        if isinstance(issues, list) and issues:
            return [PipelineEvent.REVIEW_ISSUES_FOUND]
        return [PipelineEvent.AGENT_SUCCESS]

    return []
