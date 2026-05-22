"""Commit cleanup phase handler.

This phase runs before the commit message phase to clean up any files that
should not be committed (binaries, build artifacts, temporary files, etc.).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.commit_cleanup import (
    add_to_git_exclude,
    delete_file_from_repo,
    ensure_git_initialized,
)
from ralph.git.operations import append_to_gitignore
from ralph.mcp.artifacts._commit_cleanup import CommitCleanup
from ralph.mcp.artifacts._typed_artifact_validation_error import (
    TypedArtifactValidationError,
)
from ralph.mcp.artifacts.typed_artifacts import normalize_commit_cleanup_content
from ralph.phases.artifacts import (
    PhaseArtifactError,
    load_phase_artifact,
    unwrap_phase_artifact_content,
)
from ralph.pipeline.effects import Effect, InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import Event, PhaseFailureEvent, PipelineEvent
from ralph.recovery.classifier import FailureCategory

if TYPE_CHECKING:
    from ralph.phases import PhaseContext

COMMIT_CLEANUP_ARTIFACT_PATH = ".agent/artifacts/commit_cleanup.json"

_UNSAFE_EXTENSIONS: frozenset[str] = frozenset({
    ".py", ".js", ".ts", ".go", ".rs", ".rb", ".java", ".c", ".cpp", ".h",
    ".md", ".rst", ".txt",
    ".toml", ".yaml", ".yml", ".json", ".ini", ".cfg",
})

_UNSAFE_PATH_SEGMENTS: tuple[str, ...] = ("tests/", "test_", "_test.", "docs/", "doc/")


def _is_safe_to_delete(path: str) -> bool:
    """Return True only if path is a housekeeping artifact safe to delete.

    Rejects source code, test files, documentation, and configuration files.
    """
    if Path(path).suffix.lower() in _UNSAFE_EXTENSIONS:
        return False
    path_lower = path.lower()
    return not any(seg in path_lower for seg in _UNSAFE_PATH_SEGMENTS)


def _apply_cleanup_actions(
    repo_root: Path,
    cleanup: CommitCleanup,
) -> None:
    """Apply a list of cleanup actions to the repository.

    Raises:
        PhaseArtifactError: If any cleanup action fails.
    """
    gitignore_patterns: list[str] = []
    git_exclude_patterns: list[str] = []
    delete_files: list[str] = []

    for action in cleanup.actions:
        act_type = action.action
        if act_type == "add_to_gitignore" and action.pattern:
            gitignore_patterns.append(action.pattern)
        elif act_type == "add_to_git_exclude" and action.pattern:
            git_exclude_patterns.append(action.pattern)
        elif act_type == "delete_file" and action.path:
            if not _is_safe_to_delete(action.path):
                raise ValueError(
                    f"Refusing to delete non-housekeeping file: {action.path!r}. "
                    "Commit cleanup must only remove build artifacts, binaries, "
                    "and other files that obviously should not be in the repo."
                )
            delete_files.append(action.path)

    for pattern in gitignore_patterns:
        append_to_gitignore(repo_root, [pattern])
        logger.debug("Added pattern to .gitignore: {}", pattern)

    for pattern in git_exclude_patterns:
        add_to_git_exclude(repo_root, [pattern])
        logger.debug("Added pattern to .git/info/exclude: {}", pattern)

    for file_path in delete_files:
        delete_file_from_repo(repo_root, file_path)
        logger.debug("Deleted file: {}", file_path)


def _load_cleanup_artifact(
    ctx: PhaseContext,
    phase_name: str,
) -> CommitCleanup | None:
    """Load and validate the commit_cleanup artifact.

    Returns the validated cleanup model, or None if loading/validation failed.
    """
    if not ctx.workspace.exists(COMMIT_CLEANUP_ARTIFACT_PATH):
        logger.warning(
            "{}: missing commit_cleanup artifact at {}",
            phase_name,
            COMMIT_CLEANUP_ARTIFACT_PATH,
        )
        return None

    try:
        raw_artifact = load_phase_artifact(
            ctx.workspace, COMMIT_CLEANUP_ARTIFACT_PATH
        )
        artifact_content = unwrap_phase_artifact_content(
            raw_artifact, expected_type="commit_cleanup"
        )
        normalized = normalize_commit_cleanup_content(artifact_content)
        return CommitCleanup.model_validate(normalized)
    except (PhaseArtifactError, TypedArtifactValidationError) as exc:
        logger.warning("{}: failed to load artifact: {}", phase_name, exc)
        return None


def handle_commit_cleanup_phase(effect: Effect, ctx: PhaseContext) -> list[Event]:
    """Handle the commit cleanup phase.

    Behavior summary:

    * ``PreparePromptEffect`` returns ``PROMPT_PREPARED``.
    * non-agent effects return ``[]``.
    * ``InvokeAgentEffect`` ensures git exists, validates the commit-cleanup
      artifact, applies cleanup actions, and returns ``AGENT_SUCCESS`` when
      ``analysis_complete=True`` or ``PHASE_LOOPBACK`` otherwise.
    * missing artifacts or failed cleanup actions return ``PhaseFailureEvent``.
    """
    if isinstance(effect, PreparePromptEffect):
        return [PipelineEvent.PROMPT_PREPARED]

    if not isinstance(effect, InvokeAgentEffect):
        return []

    phase_name = effect.phase

    # Ensure git is initialized
    try:
        repo_root_str = ctx.workspace.absolute_path(".")
        ensure_git_initialized(repo_root_str)
    except Exception as exc:
        logger.warning("Failed to ensure git initialized: {}", exc)

    # Load and validate the artifact
    cleanup = _load_cleanup_artifact(ctx, phase_name)
    if cleanup is None:
        return [
            PhaseFailureEvent(
                phase=phase_name,
                reason=(
                    f"Missing or invalid commit_cleanup artifact at "
                    f"{COMMIT_CLEANUP_ARTIFACT_PATH}"
                ),
                recoverable=True,
                retry_in_session=True,
                failure_category=FailureCategory.ARTIFACT_VALIDATION,
            )
        ]

    # Apply cleanup actions
    try:
        repo_root = Path(ctx.workspace.absolute_path("."))
    except Exception:
        repo_root = Path.cwd()

    try:
        _apply_cleanup_actions(repo_root, cleanup)
    except Exception as exc:
        logger.warning("{}: cleanup action failed: {}", phase_name, exc)
        return [
            PhaseFailureEvent(
                phase=phase_name,
                reason=f"Cleanup action failed: {exc}",
                recoverable=True,
                retry_in_session=True,
                failure_category=FailureCategory.ARTIFACT_VALIDATION,
            )
        ]

    # Return appropriate event based on analysis_complete
    if cleanup.analysis_complete:
        return [PipelineEvent.AGENT_SUCCESS]
    return [PipelineEvent.PHASE_LOOPBACK]
