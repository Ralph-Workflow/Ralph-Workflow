"""Commit cleanup phase handler.

This phase runs before the commit message phase to clean up any files that
should not be committed (binaries, build artifacts, temporary files, etc.).
"""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, cast

from git import InvalidGitRepositoryError, Repo
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
    from collections.abc import Callable

    from ralph.phases import PhaseContext

COMMIT_CLEANUP_ARTIFACT_PATH = ".agent/artifacts/commit_cleanup.json"

_UNSAFE_EXTENSIONS: frozenset[str] = frozenset({
    ".py", ".js", ".ts", ".go", ".rs", ".rb", ".java", ".c", ".cpp", ".h",
    ".md", ".rst", ".txt",
    ".toml", ".yaml", ".yml", ".json", ".ini", ".cfg",
    # Additional source code extensions
    ".swift", ".kt", ".kts", ".scala", ".php",
    ".sh", ".bash", ".zsh", ".fish", ".ps1",
    ".pl", ".pm", ".lua", ".r", ".m", ".mm",
    ".cs", ".fs", ".fsx", ".vb", ".dart",
    ".groovy", ".clj", ".cljs", ".hs", ".lhs",
    ".elm", ".erl", ".ex", ".exs", ".ml", ".mli",
    ".nim", ".cr", ".pas", ".pp", ".sql",
    ".graphql", ".gql", ".prisma", ".proto",
    ".asm", ".s", ".inc", ".def",
    ".cmake", ".mak", ".ninja",
    ".dockerfile", ".jenkinsfile",
    # Config/data extensions
    ".xml", ".csv", ".tsv",
})

_UNSAFE_PATH_SEGMENTS: tuple[str, ...] = ("tests/", "test_", "_test.", "docs/", "doc/")

_GENERATED_TEXT_MARKERS: frozenset[str] = frozenset({
    "agent",
    "ai",
    "analysis",
    "artifact",
    "brainstorm",
    "capture",
    "chat",
    "checkpoint",
    "completion",
    "conversation",
    "debug",
    "dump",
    "generated",
    "generation",
    "inference",
    "interaction",
    "llm",
    "log",
    "message",
    "model",
    "note",
    "output",
    "pipeline",
    "plan",
    "prompt",
    "report",
    "response",
    "review",
    "session",
    "summary",
    "temp",
    "tmp",
    "trace",
    "transcript",
    "verify",
    "worker",
})

# Narrow allowlist of clearly-temporary source-file name tokens. Excludes
# common programming terms (log, model, worker, session, message, plan, chat,
# output, report, capture, completion, note, pipeline, response, review,
# summary, debug, trace, transcript) to prevent false positives on legitimate
# source files like log.py, debug.py, or worker.py. Only tokens that almost
# always denote a disposable artifact are allowed.
_SOURCE_FILE_GENERATED_MARKERS: frozenset[str] = frozenset({
    "temp",
    "tmp",
    "scratch",
    "generated",
    "throwaway",
    "dump",
})

_GENERATED_TEXT_DIRECTORIES: frozenset[str] = frozenset({
    ".agent",
    ".cache",
    ".gradle",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "artifacts",
    "build",
    "cache",
    "caches",
    "coverage",
    "dist",
    "htmlcov",
    "logs",
    "node_modules",
    "out",
    "output",
    "outputs",
    "reports",
    "sessions",
    "temp",
    "tmp",
    "traces",
    "transcripts",
    "vendor",
})


def _close_repo(repo: Repo | None) -> None:
    close = cast("Callable[[], object] | None", getattr(repo, "close", None))
    if callable(close):
        close()


def _path_exists_in_head(repo_root: Path, relative_path: str) -> bool:
    """Return True when ``relative_path`` already exists in HEAD."""
    repo: Repo | None = None
    try:
        repo = Repo(repo_root, search_parent_directories=False)
        with suppress(Exception):
            repo.git.cat_file("-e", f"HEAD:{relative_path}")
            return True
        return False
    except InvalidGitRepositoryError:
        return False
    finally:
        _close_repo(repo)


def _is_generated_text_artifact(
    repo_root: Path,
    path: str,
    markers: frozenset[str] = _GENERATED_TEXT_MARKERS,
) -> bool:
    """Return True when ``path`` looks like a generated artifact, not authored content.

    The ``markers`` parameter selects which name tokens count as a generated
    signal. The default broad set is used for ``.txt`` and ``.json`` files;
    the narrow source-file allowlist is used for any other extension.

    Tracked files (those already present in HEAD) are never treated as
    generated, regardless of name.
    """
    candidate = Path(path)
    name_tokens = {
        token
        for token in candidate.stem.lower().replace(".", "-").replace("_", "-").split("-")
        if token
    }
    parent_parts = {part.lower() for part in candidate.parts[:-1]}
    has_generated_signal = bool(name_tokens & markers) or bool(
        parent_parts & _GENERATED_TEXT_DIRECTORIES
    )
    if not has_generated_signal:
        return False
    return not _path_exists_in_head(repo_root, path)


def _is_safe_to_delete(repo_root: Path, path: str) -> bool:
    """Return True only if path is a housekeeping artifact safe to delete.

    Rejects source code, test files, documentation, and configuration files.
    Source-code files with clearly-temporary names (e.g. ``temp_script.py``)
    are allowed to be deleted when untracked, but tracked files are always
    protected.
    """
    candidate = Path(path)
    path_lower = path.lower()

    # 1. Reject paths inside test/doc segments unconditionally
    if any(seg in path_lower for seg in _UNSAFE_PATH_SEGMENTS):
        return False

    # 2. Reject known lock files and dependency manifests
    if candidate.name in {
        "package-lock.json", "yarn.lock", "Cargo.lock", "poetry.lock",
        "uv.lock", "Pipfile.lock", "composer.lock", "Gemfile.lock", "go.sum",
    }:
        return False

    suffix = candidate.suffix.lower()

    # 3. Housekeeping extensions are safe to delete unless already tracked
    if suffix in {".bak", ".tmp", ".temp", ".old", ".orig", ".rej", ".patch", ".log"}:
        return not _path_exists_in_head(repo_root, path)

    # 4. .txt and .json use the broad generated-text marker set
    if suffix in (".txt", ".json"):
        return _is_generated_text_artifact(repo_root, path)

    # 5. Other extensions (source code, configs, etc.) may still be deleted
    #    when their name or directory carries a clearly-temporary signal AND
    #    the file is not tracked in HEAD. _is_generated_text_artifact returns
    #    False for tracked files, so a tracked source file is always safe.
    if _is_generated_text_artifact(
        repo_root, path, markers=_SOURCE_FILE_GENERATED_MARKERS
    ):
        return True

    # 6. Fall through: reject if the extension is in the unsafe list
    return suffix not in _UNSAFE_EXTENSIONS


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
            if not _is_safe_to_delete(repo_root, action.path):
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
