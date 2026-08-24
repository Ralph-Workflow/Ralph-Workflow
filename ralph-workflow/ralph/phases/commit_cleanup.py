"""Commit cleanup phase handler.

This phase runs before the commit message phase to clean up any files that
should not be committed (binaries, build artifacts, temporary files, etc.).

Cleanup is best-effort housekeeping: declined or failed actions never kill
the run. Identical no-progress attempts are bounded by a phase-owned identity
counter that survives agent re-selection.

The phase PRE-EMPTIVELY UNTRACKS tracked engine-internal files (via
``untrack_engine_internal_files`` from ``ralph.git.commit_cleanup``)
BEFORE loading the artifact.

The phase also auto-seeds the canonical ``.gitignore`` and
``.git/info/exclude`` patterns on every entry. Seeds and untrack are
wrapped in try/except so a helper failure cannot fail the phase.
"""

from __future__ import annotations

import hashlib
import json
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
from ralph.phases._commit_cleanup_actions import CleanupApplyReport, apply_cleanup_actions
from ralph.phases._commit_cleanup_catalog import (
    COMMIT_CLEANUP_IDENTITY_COUNTER,
    DEFAULT_IDENTITY_MAX,
    GENERATED_TEXT_EXTENSIONS,
    LOCKFILE_BASENAMES,
    TEMPORARY_SUFFIXES,
)
from ralph.phases._commit_cleanup_catalog import (
    GENERATED_TEXT_DIRECTORIES as _GENERATED_TEXT_DIRECTORIES,
)
from ralph.phases._commit_cleanup_catalog import (
    GENERATED_TEXT_MARKERS as _GENERATED_TEXT_MARKERS,
)
from ralph.phases._commit_cleanup_catalog import (
    HOUSEKEEPING_BASENAMES as _HOUSEKEEPING_BASENAMES,
)
from ralph.phases._commit_cleanup_catalog import (
    PROTECTED_BASENAMES as _PROTECTED_BASENAMES,
)
from ralph.phases._commit_cleanup_catalog import (
    SOURCE_FILE_GENERATED_MARKERS as _SOURCE_FILE_GENERATED_MARKERS,
)
from ralph.phases._commit_cleanup_catalog import (
    UNSAFE_EXTENSIONS as _UNSAFE_EXTENSIONS,
)
from ralph.phases._commit_cleanup_catalog import (
    UNSAFE_PATH_SEGMENTS as _UNSAFE_PATH_SEGMENTS,
)
from ralph.phases._commit_cleanup_outcome import (
    all_applications_failed,
    build_unapplied_retry_hint,
    decide_cleanup_outcome,
)
from ralph.phases._commit_cleanup_outcome import (
    build_cleanup_retry_hint as _build_cleanup_retry_hint,
)
from ralph.phases.artifacts import (
    PhaseArtifactError,
    load_phase_artifact,
    unwrap_phase_artifact_content,
)
from ralph.phases.required_artifacts import retry_hint_path
from ralph.pipeline.effects import Effect, InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import Event, PhaseFailureEvent, PipelineEvent
from ralph.policy.models._loop_counter_config import LoopCounterConfig
from ralph.policy.models._pipeline_policy import PipelinePolicy
from ralph.recovery.classifier import FailureCategory

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.phases import PhaseContext

COMMIT_CLEANUP_ARTIFACT_PATH = ".agent/artifacts/commit_cleanup.md"
_IDENTITY_STATE_PATH = ".agent/tmp/commit_cleanup_identity.json"


def _close_repo(repo: Repo | None) -> None:
    close = cast("Callable[[], object] | None", getattr(repo, "close", None))
    if callable(close):
        close()


def _path_exists_in_head(repo_root: Path, relative_path: str) -> bool:
    """Return True when ``relative_path`` already exists in HEAD."""
    repo: Repo | None = None
    try:
        repo = Repo(repo_root, search_parent_directories=False)
        try:
            repo.git.cat_file("-e", f"HEAD:{relative_path}")
            return True
        except Exception:
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
    """Return True when ``path`` looks like a generated artifact, not authored content."""
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

    The check order matters:
    1. The agent-internal fast path (FIRST statement in the function body).
    2. Protected basenames win over suffix-based rules.
    3. Housekeeping basenames win over the unsafe-extension fall-through.
    4. Paths with parent-traversal segments or absolute paths are rejected.
    """
    if is_agent_internal_path(path):
        return True
    candidate = Path(path)
    path_lower = path.lower()
    suffix = candidate.suffix.lower()
    if _is_protected_path(repo_root, candidate, path_lower):
        return False
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
    if candidate.name in LOCKFILE_BASENAMES:
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
    if suffix in TEMPORARY_SUFFIXES:
        return not _path_exists_in_head(repo_root, str(candidate))
    if suffix in GENERATED_TEXT_EXTENSIONS:
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
    """Apply actions and return (declined, failed-delete) for existing tests."""
    report = _apply_cleanup_report(repo_root, cleanup)
    return report.declined_delete_paths, report.failed_delete_paths


def _apply_cleanup_report(
    repo_root: Path,
    cleanup: CommitCleanup,
) -> CleanupApplyReport:
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
    """Load and validate the commit_cleanup artifact."""
    if not ctx.workspace.exists(COMMIT_CLEANUP_ARTIFACT_PATH):
        logger.warning(
            "{}: missing commit_cleanup artifact at {}",
            phase_name,
            COMMIT_CLEANUP_ARTIFACT_PATH,
        )
        return None

    try:
        raw_artifact = load_phase_artifact(
            ctx.workspace,
            COMMIT_CLEANUP_ARTIFACT_PATH,
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
    """Handle the commit cleanup phase."""
    if isinstance(effect, PreparePromptEffect):
        return [PipelineEvent.PROMPT_PREPARED]
    if not isinstance(effect, InvokeAgentEffect):
        return []

    phase_name = effect.phase
    workspace_resolution_error: BaseException | None = None
    try:
        repo_root_str = ctx.workspace.absolute_path(".")
        repo_root = Path(repo_root_str)
        ensure_git_initialized(repo_root_str)
    except Exception as exc:
        workspace_resolution_error = exc

    if workspace_resolution_error is not None:
        return _maybe_succeed_after_identity_bound(
            ctx,
            phase_name,
            fingerprint="workspace-root",
            events=[
                PhaseFailureEvent(
                    phase=phase_name,
                    reason=f"Failed to resolve workspace root: {workspace_resolution_error}",
                    recoverable=True,
                    retry_in_session=True,
                    failure_category=FailureCategory.ARTIFACT_VALIDATION,
                )
            ],
        )

    try:
        untracked = untrack_engine_internal_files(repo_root, is_agent_internal_path)
        logger.info("Pre-emptively untracked {} engine-internal file(s)", len(untracked))
    except Exception as exc:
        logger.warning("untrack_engine_internal_files failed (continuing): {}", exc)

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

    artifact_digest = _artifact_digest(ctx)
    cleanup = _load_cleanup_artifact(ctx, phase_name)
    events: list[Event]
    if cleanup is None:
        events = _maybe_succeed_after_identity_bound(
            ctx,
            phase_name,
            fingerprint=f"missing-or-invalid:{artifact_digest}",
            events=_missing_artifact_failure(phase_name),
        )
    else:
        try:
            report = _apply_cleanup_report(repo_root, cleanup)
        except Exception as exc:
            _consume_cleanup_artifact(ctx)
            events = _maybe_succeed_after_identity_bound(
                ctx,
                phase_name,
                fingerprint=f"apply-raise:{artifact_digest}",
                events=_cleanup_failed_event(phase_name, exc),
            )
        else:
            _persist_unapplied_hint(ctx, phase_name, report)
            _consume_cleanup_artifact(ctx)
            outcome = decide_cleanup_outcome(phase_name, cleanup, report)
            if all_applications_failed(report):
                events = _maybe_succeed_after_identity_bound(
                    ctx,
                    phase_name,
                    fingerprint=_report_fingerprint(artifact_digest, report),
                    events=outcome,
                )
            else:
                events = outcome
    return events


def _consume_cleanup_artifact(ctx: PhaseContext) -> None:
    """Drop a graded well-formed leftover so the next attempt needs a new submit."""
    try:
        if ctx.workspace.exists(COMMIT_CLEANUP_ARTIFACT_PATH):
            ctx.workspace.delete(COMMIT_CLEANUP_ARTIFACT_PATH)
    except Exception as exc:
        logger.warning("Failed to consume commit-cleanup leftover artifact: {}", exc)


def _maybe_succeed_after_identity_bound(
    ctx: PhaseContext,
    phase_name: str,
    *,
    fingerprint: str,
    events: list[Event],
) -> list[Event]:
    """Cap identical recoverable housekeeping failures, then proceed."""
    if _charge_identity(ctx, phase_name, fingerprint):
        logger.warning(
            "{}: identical no-progress bound reached; proceeding to commit-message generation",
            phase_name,
        )
        return [PipelineEvent.AGENT_SUCCESS]
    return events


def _artifact_digest(ctx: PhaseContext) -> str:
    """Fingerprint the leftover or newly submitted cleanup artifact bytes."""
    if not ctx.workspace.exists(COMMIT_CLEANUP_ARTIFACT_PATH):
        return "missing"
    try:
        raw = ctx.workspace.read(COMMIT_CLEANUP_ARTIFACT_PATH)
    except Exception:
        return "unreadable"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _report_fingerprint(artifact_digest: str, report: CleanupApplyReport) -> str:
    """Identity two attempts that produced the same outcome for the same paths."""
    payload = {
        "digest": artifact_digest,
        "declined": sorted(report.declined_delete_paths),
        "failed_deletes": sorted(report.failed_delete_paths),
        "failed_ignore": sorted(report.failed_gitignore_patterns),
        "failed_exclude": sorted(report.failed_exclude_patterns),
        "applied": report.applied_count,
    }
    encoded = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _identity_max(ctx: PhaseContext) -> int:
    """Return the policy cap for identical no-progress cleanup attempts."""
    policy: object = ctx.pipeline_policy
    if isinstance(policy, PipelinePolicy):
        cfg = policy.loop_counters.get(COMMIT_CLEANUP_IDENTITY_COUNTER)
        parsed = DEFAULT_IDENTITY_MAX if cfg is None else cfg.default_max
    else:
        parsed = _identity_max_from_untyped_policy(policy)
    if parsed <= 0:
        return DEFAULT_IDENTITY_MAX
    return parsed


def _identity_max_from_untyped_policy(policy: object) -> int:
    """Read the identity cap from test doubles that are not PipelinePolicy."""
    counters = cast("object", getattr(policy, "loop_counters", None))
    if not isinstance(counters, dict):
        return DEFAULT_IDENTITY_MAX
    cfg_obj = cast("object", counters.get(COMMIT_CLEANUP_IDENTITY_COUNTER))
    if cfg_obj is None:
        return DEFAULT_IDENTITY_MAX
    if isinstance(cfg_obj, LoopCounterConfig):
        return cfg_obj.default_max
    raw = cast("object", getattr(cfg_obj, "default_max", DEFAULT_IDENTITY_MAX))
    if isinstance(raw, bool) or not isinstance(raw, int):
        return DEFAULT_IDENTITY_MAX
    return raw


def _charge_identity(ctx: PhaseContext, phase_name: str, fingerprint: str) -> bool:
    """Increment the phase-owned identity counter; return True when the cap is reached."""
    prior = _read_identity_state(ctx)
    if prior is not None and prior[0] == phase_name and prior[1] == fingerprint:
        count = prior[2] + 1
    else:
        count = 1
    _write_identity_state(ctx, phase_name, fingerprint, count)
    return count >= _identity_max(ctx)


def _read_identity_state(ctx: PhaseContext) -> tuple[str, str, int] | None:
    if not ctx.workspace.exists(_IDENTITY_STATE_PATH):
        return None
    try:
        loaded: object = json.loads(ctx.workspace.read(_IDENTITY_STATE_PATH))
    except Exception:
        return None
    if not isinstance(loaded, dict):
        return None
    phase = loaded.get("phase")
    fingerprint = loaded.get("fingerprint")
    count = loaded.get("count")
    if not isinstance(phase, str) or not isinstance(fingerprint, str):
        return None
    if isinstance(count, bool) or not isinstance(count, int):
        return None
    return (phase, fingerprint, count)


def _write_identity_state(
    ctx: PhaseContext, phase_name: str, fingerprint: str, count: int
) -> None:
    payload = {"phase": phase_name, "fingerprint": fingerprint, "count": count}
    try:
        ctx.workspace.write(_IDENTITY_STATE_PATH, json.dumps(payload))
    except Exception as exc:
        logger.warning("Failed to persist commit-cleanup identity state: {}", exc)


def _persist_unapplied_hint(
    ctx: PhaseContext,
    phase_name: str,
    report: CleanupApplyReport,
) -> None:
    """Write declined and apply-failed decisions for the next prompt."""
    hint = build_unapplied_retry_hint(report)
    if not hint:
        return
    try:
        ctx.workspace.write(retry_hint_path(phase_name), hint)
    except Exception as exc:
        logger.warning("Failed to persist commit-cleanup retry hint: {}", exc)


def _missing_artifact_failure(phase_name: str) -> list[Event]:
    """Build the ``PhaseFailureEvent`` when the cleanup artifact is missing or invalid."""
    return [
        PhaseFailureEvent(
            phase=phase_name,
            reason=(
                f"Missing or invalid commit_cleanup artifact at {COMMIT_CLEANUP_ARTIFACT_PATH}"
            ),
            recoverable=True,
            retry_in_session=True,
            failure_category=FailureCategory.ARTIFACT_VALIDATION,
        )
    ]


def _cleanup_failed_event(phase_name: str, exc: BaseException) -> list[Event]:
    """Build the ``PhaseFailureEvent`` when apply raises."""
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


