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

The phase's side-effecting collaborators (baseline reader, commit checker,
head-SHA reader, baseline writer) are reachable through the injectable
:class:`ReviewCollaborators` bundle so callers can drive
:func:`handle_review` without a real Git repository. The default bundle
resolves the production implementations, so omitting the seam leaves the
handler's behavior unchanged.
"""

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
        # filesystem-read-ok: review baseline reads its one canonical marker for this explicit phase
        if not marker_path.exists():
            return None
        # filesystem-read-ok: review baseline reads its one canonical marker for this explicit phase
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
    """Persist the review baseline marker without rewriting identical content.

    Wraps the mkdir + write pair in the existing try/except so an
    ``OSError`` from the workspace still degrades to a debug log line
    instead of crashing the handler. The marker byte format is bare
    ``sha`` with no trailing newline, matching the prior behavior.

    The ``backend`` seam defaults to :data:`DEFAULT_FILE_BACKEND` so
    an in-memory backend can exercise the idempotent guard without
    real filesystem I/O. A byte-identical rewrite of an existing
    marker short-circuits the physical write so per-cycle clean
    review passes do not advance the file's mtime or generate an
    additional fseventsd notification. The post-condition "marker
    file contains ``sha``" still holds because the fail-open
    ``write_text_if_changed`` guard falls through to a real write
    on any read uncertainty or content mismatch.
        # filesystem-write-ok: review phase output under .agent/tmp, runtime phase output not tracked
    """
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


# --- Injectable collaborator seam -----------------------------------------
#
# The four callbacks below are the review phase's only side-effecting
# collaborators. They are resolved through the module globals at CALL
# TIME (not bound at import time) so the production default stays the
# single source of truth and remains monkeypatch-compatible for any
# caller that reaches for the module attributes directly.


def _production_read_baseline(ctx: PhaseContext) -> str | None:
    """Resolve the production review-baseline reader."""
    return _read_review_baseline(ctx)


def _production_has_new_commits(ctx: PhaseContext, baseline: str) -> bool:
    """Resolve the production commit checker."""
    return _has_new_commits_since_baseline(ctx, baseline)


def _production_current_head_sha(ctx: PhaseContext) -> str | None:
    """Resolve the production head-SHA reader."""
    return _current_head_sha(ctx)


def _production_write_baseline(ctx: PhaseContext, sha: str) -> None:
    """Resolve the production review-baseline writer."""
    _write_review_baseline(ctx, sha)


@dataclass(frozen=True)
class ReviewCollaborators:
    """Injectable collaborators for the review phase.

    Every field defaults to the production implementation, so
    ``ReviewCollaborators()`` reproduces the handler's default behavior
    exactly. Callers (and tests) may override any subset of the
    callbacks to drive :func:`handle_review` without touching the
    filesystem or a real Git repository.
    """

    read_baseline: Callable[[PhaseContext], str | None] = _production_read_baseline
    """Returns the recorded review baseline SHA, or ``None`` when no baseline has been recorded yet."""

    has_new_commits: Callable[[PhaseContext, str], bool] = _production_has_new_commits
    """Returns ``True`` when commits landed after the given baseline SHA."""

    current_head_sha: Callable[[PhaseContext], str | None] = _production_current_head_sha
    """Returns the current workspace head SHA, or ``None`` when it cannot be determined."""

    write_baseline: Callable[[PhaseContext, str], None] = _production_write_baseline
    """Records the given SHA as the new review baseline."""


#: The production collaborator bundle used when no seam is supplied.
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


def _load_review_issues(ctx: PhaseContext) -> dict[str, object]:
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
    """Handle the review phase.

    Args:
        effect: The effect that triggered this phase.
        ctx: Phase context with workspace and policy.
        collaborators: Injectable baseline/commit/head collaborators. Defaults
            to :data:`DEFAULT_REVIEW_COLLABORATORS`, which resolves the
            production implementations, so omitting the seam keeps the
            handler's behavior unchanged.

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
        baseline = collaborators.read_baseline(ctx)
        if baseline is not None and not collaborators.has_new_commits(ctx, baseline):
            logger.info("Review phase: no new commits since {} — skipping review", baseline[:8])
            return [PipelineEvent.REVIEW_CLEAN]

        logger.info("Review phase: processing review result after agent run")
        try:
            content = _load_review_issues(ctx)
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
