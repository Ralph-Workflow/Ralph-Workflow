"""Commit cleanup phase handler.

This phase runs before the commit message phase to clean up any files that
should not be committed (binaries, build artifacts, temporary files, etc.).

The phase is hardened to be ROCK SOLID: cleanup actions are applied
best-effort -- a single unsafe ``delete_file`` does not abort the whole
phase. Safe actions (matching files, gitignore patterns, git exclude
patterns) are still applied even when one or more delete actions are
skipped. The phase only returns ``PhaseFailureEvent`` when EVERY delete
action was rejected and no safe work was done; in that case the event
carries a structured retry hint naming the rejected paths.

The phase PRE-EMPTIVELY UNTRACKS tracked engine-internal files (via
``untrack_engine_internal_files`` from ``ralph.git.commit_cleanup``)
BEFORE loading the artifact. This is the safety net for the prior
failure mode where tracked ``.agent/raw/opencode.log``,
``.agent/tmp/mcp-server.log``, or root ``checkpoint.json`` would
trigger a hard reject from the safety classifier when the agent
submitted ``delete_file`` actions -- the rejection came because the
file was tracked in HEAD and the safety check ran before the
engine-internal fast-path exemption. The pre-emptive untrack removes
those paths from the INDEX (not the working tree) so the agent's
diff never includes them and the rejection cannot occur.

The phase also auto-seeds the canonical ``.gitignore`` and
``.git/info/exclude`` patterns on every entry (via
``auto_seed_default_gitignore`` and ``auto_seed_default_git_exclude``
from ``ralph.config.bootstrap``) so the engine-internal allowlist stays
in effect on non-bootstrap runs. Both seeds are wrapped in try/except
so a seeding failure cannot fail the phase.
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
    untrack_engine_internal_files,
)
from ralph.git.operations import append_to_gitignore
from ralph.mcp.artifacts._commit_cleanup import CommitCleanup
from ralph.mcp.artifacts._typed_artifact_validation_error import (
    TypedArtifactValidationError,
)
from ralph.mcp.artifacts.typed_artifacts import normalize_commit_cleanup_content
from ralph.phases._agent_internal_paths import is_agent_internal_path
from ralph.phases._commit_cleanup_actions import apply_cleanup_actions
from ralph.phases._commit_cleanup_outcome import (
    build_cleanup_retry_hint as _build_cleanup_retry_hint,
)
from ralph.phases._commit_cleanup_outcome import (
    decide_cleanup_outcome,
)
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

COMMIT_CLEANUP_ARTIFACT_PATH = ".agent/artifacts/commit_cleanup.md"
_LEGACY_COMMIT_CLEANUP_ARTIFACT_PATH = ".agent/artifacts/commit_cleanup.json"

_UNSAFE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py",
        ".js",
        ".ts",
        ".go",
        ".rs",
        ".rb",
        ".java",
        ".c",
        ".cpp",
        ".h",
        ".md",
        ".rst",
        ".txt",
        ".toml",
        ".yaml",
        ".yml",
        ".json",
        ".ini",
        ".cfg",
        # Additional source code extensions
        ".swift",
        ".kt",
        ".kts",
        ".scala",
        ".php",
        ".sh",
        ".bash",
        ".zsh",
        ".fish",
        ".ps1",
        ".pl",
        ".pm",
        ".lua",
        ".r",
        ".m",
        ".mm",
        ".cs",
        ".fs",
        ".fsx",
        ".vb",
        ".dart",
        ".groovy",
        ".clj",
        ".cljs",
        ".hs",
        ".lhs",
        ".elm",
        ".erl",
        ".ex",
        ".exs",
        ".ml",
        ".mli",
        ".nim",
        ".cr",
        ".pas",
        ".pp",
        ".sql",
        ".graphql",
        ".gql",
        ".prisma",
        ".proto",
        ".asm",
        ".s",
        ".inc",
        ".def",
        ".cmake",
        ".mak",
        ".ninja",
        ".dockerfile",
        ".jenkinsfile",
        # Config/data extensions
        ".xml",
        ".csv",
        ".tsv",
    }
)

_UNSAFE_PATH_SEGMENTS: tuple[str, ...] = ("tests/", "test_", "_test.", "docs/", "doc/")

# Housekeeping filenames that are always safe to delete when untracked but
# must NEVER be deleted when committed (e.g. a checked-in ``.coverage`` is
# project content, not a stray test artifact). The basename check runs before
# the suffix fall-through so ``coverage.xml`` can be deleted even though
# ``.xml`` is in ``_UNSAFE_EXTENSIONS``.
_HOUSEKEEPING_BASENAMES: frozenset[str] = frozenset({".coverage", "coverage.xml"})

# Extensionless files that are protected from deletion regardless of any
# suffix-based rule below. The check is case-insensitive so ``Dockerfile``,
# ``MAKEFILE``, ``License`` and similar are all covered. These names win over
# every suffix-based check, including the ``.txt`` / ``.json`` generated-text
# marker check (e.g. ``LICENSE.txt`` is protected).
_PROTECTED_BASENAMES: frozenset[str] = frozenset(
    {
        "dockerfile",
        "makefile",
        "license",
        "readme",
    }
)

_GENERATED_TEXT_MARKERS: frozenset[str] = frozenset(
    {
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
    }
)

# Narrow allowlist of clearly-temporary source-file name tokens. Excludes
# common programming terms (log, model, worker, session, message, plan, chat,
# output, report, capture, completion, note, pipeline, response, review,
# summary, debug, trace, transcript) to prevent false positives on legitimate
# source files like log.py, debug.py, or worker.py. Only tokens that almost
# always denote a disposable artifact are allowed.
_SOURCE_FILE_GENERATED_MARKERS: frozenset[str] = frozenset(
    {
        "temp",
        "tmp",
        "scratch",
        "generated",
        "throwaway",
        "dump",
    }
)

_GENERATED_TEXT_DIRECTORIES: frozenset[str] = frozenset(
    {
        ".agent",
        ".cache",
        ".gradle",
        ".mypy_cache",
        ".next",
        ".nuxt",
        ".output",
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
    }
)


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

    The check order matters:
    1. The agent-internal fast path (FIRST statement in the function body)
       -- Ralph runtime artifacts under ``.agent/`` plus root-level
       ``checkpoint.json`` are unconditionally deletable, even when
       tracked in HEAD. The fast path must execute BEFORE any other
       work (``Path(path)``, ``path.lower()``, ``suffix``) so that the
       engine-owned allowlist cannot be silently bypassed by a future
       refactor that adds a new check above it. This is also the
       guarantee that ``audit_agent_internal_paths`` pins via AST
       placement inspection (see
       ``ralph/testing/audit_agent_internal_paths.py``).
    2. Protected basenames win over suffix-based rules (so ``LICENSE.txt``
       is protected even though ``.txt`` is a generated-text suffix).
    3. Housekeeping basenames win over the unsafe-extension fall-through
       (so ``coverage.xml`` is deletable even though ``.xml`` is in
       ``_UNSAFE_EXTENSIONS``).
    4. Paths with parent-traversal segments (``..``) or absolute paths
       are always rejected -- they would escape the repository root and
       target files outside the engine's control surface.
    """
    if is_agent_internal_path(path):
        return True
    candidate = Path(path)
    path_lower = path.lower()
    suffix = candidate.suffix.lower()
    if _is_protected_path(repo_root, candidate, path_lower):
        return False
    # Security: never accept paths that escape the repo root via parent
    # traversal or absolute paths. ``delete_file_from_repo`` would raise
    # ``ValueError`` for these, but rejecting here means the action is
    # counted as a skipped delete and surfaces via the structured retry
    # hint instead of being silently swallowed.
    if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
        return False
    return _is_deletable_housekeeping(repo_root, candidate, suffix)


def _is_protected_path(
    repo_root: Path,
    candidate: Path,
    path_lower: str,
) -> bool:
    """Return True for paths that must never be deleted, regardless of suffix."""
    if any(seg in path_lower for seg in _UNSAFE_PATH_SEGMENTS):
        return True
    if candidate.name in {
        "package-lock.json",
        "yarn.lock",
        "Cargo.lock",
        "poetry.lock",
        "uv.lock",
        "Pipfile.lock",
        "composer.lock",
        "Gemfile.lock",
        "go.sum",
    }:
        return True
    if candidate.name.lower() in _PROTECTED_BASENAMES:
        return True
    return candidate.name in _HOUSEKEEPING_BASENAMES and _path_exists_in_head(
        repo_root, str(candidate)
    )


def _is_deletable_housekeeping(
    repo_root: Path,
    candidate: Path,
    suffix: str,
) -> bool:
    """Return True for files that are safe housekeeping artifacts to delete."""
    if candidate.name in _HOUSEKEEPING_BASENAMES:
        return not _path_exists_in_head(repo_root, str(candidate))
    if suffix in {".bak", ".tmp", ".temp", ".old", ".orig", ".rej", ".patch", ".log"}:
        return not _path_exists_in_head(repo_root, str(candidate))
    if suffix in (".txt", ".json"):
        return _is_generated_text_artifact(repo_root, str(candidate))
    if _is_generated_text_artifact(
        repo_root, str(candidate), markers=_SOURCE_FILE_GENERATED_MARKERS
    ):
        return True
    return suffix not in _UNSAFE_EXTENSIONS


def build_cleanup_retry_hint(skipped_paths: list[str], safe_applied_count: int) -> str:
    """Build the phase's structured retry hint."""
    return _build_cleanup_retry_hint(skipped_paths, safe_applied_count)


def _apply_cleanup_actions(
    repo_root: Path,
    cleanup: CommitCleanup,
) -> tuple[list[str], list[str]]:
    """Apply actions through the isolated action engine."""
    return apply_cleanup_actions(
        repo_root,
        cleanup,
        is_safe_to_delete=_is_safe_to_delete,
        append_to_gitignore=append_to_gitignore,
        add_to_git_exclude=add_to_git_exclude,
        delete_file_from_repo=delete_file_from_repo,
    )


def _load_cleanup_artifact(
    ctx: PhaseContext,
    phase_name: str,
) -> CommitCleanup | None:
    """Load and validate the commit_cleanup artifact.

    Returns the validated cleanup model, or None if loading/validation failed.
    """
    artifact_path = COMMIT_CLEANUP_ARTIFACT_PATH
    if not ctx.workspace.exists(artifact_path) and ctx.workspace.exists(
        _LEGACY_COMMIT_CLEANUP_ARTIFACT_PATH
    ):
        artifact_path = _LEGACY_COMMIT_CLEANUP_ARTIFACT_PATH

    if not ctx.workspace.exists(artifact_path):
        logger.warning(
            "{}: missing commit_cleanup artifact at {}",
            phase_name,
            COMMIT_CLEANUP_ARTIFACT_PATH,
        )
        return None

    try:
        raw_artifact = load_phase_artifact(
            ctx.workspace,
            artifact_path,
            artifact_type="commit_cleanup",
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
    * ``InvokeAgentEffect`` ensures git exists, auto-seeds canonical
      ``.gitignore`` and ``.git/info/exclude`` patterns on every entry,
      validates the commit-cleanup artifact, applies cleanup actions
      best-effort, and returns ``AGENT_SUCCESS`` when ``analysis_complete=True``
      or ``PHASE_LOOPBACK`` otherwise.
    * Cleanup is best-effort: a single unsafe ``delete_file`` does not
      abort the phase. The phase only fails when EVERY delete action was
      unsafe AND no safe action was applied.
    * Missing artifacts return ``PhaseFailureEvent`` with
      ``recoverable=True``.
    """
    if isinstance(effect, PreparePromptEffect):
        return [PipelineEvent.PROMPT_PREPARED]

    if not isinstance(effect, InvokeAgentEffect):
        return []

    phase_name = effect.phase
    workspace_resolution_error: BaseException | None = None
    try:
        repo_root_str = ctx.workspace.absolute_path(".")
        repo_root = Path(repo_root_str)
        # Direct call so audit_agent_internal_paths._check_auto_seed_placement
        # can locate the prior anchor via ast.Call inspection.
        ensure_git_initialized(repo_root_str)
    except Exception as exc:
        workspace_resolution_error = exc

    if workspace_resolution_error is not None:
        return [
            PhaseFailureEvent(
                phase=phase_name,
                reason=f"Failed to resolve workspace root: {workspace_resolution_error}",
                recoverable=True,
                retry_in_session=True,
                failure_category=FailureCategory.ARTIFACT_VALIDATION,
            )
        ]  # reason: defensive return -- audit pins structural placement

    # Pre-emptive untrack of tracked engine-internal files. Runs BEFORE
    # the artifact load so the agent's view of the diff no longer
    # contains engine-internal paths -- even when the agent's
    # ``delete_file`` action would otherwise hit a hard safety reject
    # for a tracked engine file. The call is a plain
    # ``untrack_engine_internal_files(...)`` (an ``ast.Name`` call) so
    # ``audit_agent_internal_paths._check_pre_emptive_untrack_placement``
    # can locate it via ``ast.Call`` inspection. Wrapped in
    # ``with suppress(Exception):`` so a helper failure (broken git
    # state, read-only filesystem, symlink escape) cannot fail the
    # phase -- the helper already returns ``[]`` fail-closed for these
    # cases.
    with suppress(Exception):
        untracked = untrack_engine_internal_files(repo_root, is_agent_internal_path)
        logger.info(
            "Pre-emptively untracked {} engine-internal file(s)", len(untracked)
        )

    # Direct calls so audit_agent_internal_paths._check_auto_seed_placement
    # can locate the seed helpers via ast.Call inspection. Both helpers
    # are imported lazily to avoid a circular import through
    # ``ralph.config -> ralph.policy -> ralph.phases``; the calls are
    # wrapped in try/except so a seeding failure cannot fail the phase.
    try:
        from ralph.config.bootstrap import auto_seed_default_gitignore

        _gitignore_appended = auto_seed_default_gitignore(repo_root)
        logger.debug(
            "Auto-seeded {} canonical gitignore pattern(s) on cleanup entry",
            len(_gitignore_appended),
        )
    except Exception as exc:
        logger.warning("auto_seed_default_gitignore failed (continuing): {}", exc)
    try:
        from ralph.config.bootstrap import auto_seed_default_git_exclude

        _gitexclude_appended = auto_seed_default_git_exclude(repo_root)
        logger.debug(
            "Auto-seeded {} canonical git-exclude pattern(s) on cleanup entry",
            len(_gitexclude_appended),
        )
    except Exception as exc:
        logger.warning("auto_seed_default_git_exclude failed (continuing): {}", exc)

    cleanup = _load_cleanup_artifact(ctx, phase_name)
    if cleanup is None:
        return _missing_artifact_failure(phase_name)

    try:
        skipped_delete_paths, failed_delete_paths = _apply_cleanup_actions(
            repo_root, cleanup
        )
    except Exception as exc:
        return _cleanup_failed_event(phase_name, exc)

    return _decide_cleanup_outcome(
        phase_name, cleanup, skipped_delete_paths, failed_delete_paths
    )


def _missing_artifact_failure(phase_name: str) -> list[Event]:
    """Build the ``PhaseFailureEvent`` when the cleanup artifact is missing or invalid."""
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


def _cleanup_failed_event(phase_name: str, exc: BaseException) -> list[Event]:
    """Build the ``PhaseFailureEvent`` when ``_apply_cleanup_actions`` raises."""
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


def _decide_cleanup_outcome(
    phase_name: str,
    cleanup: CommitCleanup,
    skipped_delete_paths: list[str],
    failed_delete_paths: list[str] | None = None,
) -> list[Event]:
    """Delegate outcome calculation to the isolated decision module."""
    return decide_cleanup_outcome(
        phase_name,
        cleanup,
        skipped_delete_paths,
        failed_delete_paths,
    )
