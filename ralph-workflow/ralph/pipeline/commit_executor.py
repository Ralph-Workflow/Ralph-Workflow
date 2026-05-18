"""Commit effect execution for the pipeline runner."""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from git import Repo
from loguru import logger

from ralph.config.enums import Verbosity
from ralph.display.artifact_renderer import render_commit_message
from ralph.git.operations import create_commit, stage_all, stage_files
from ralph.mcp.artifacts.commit_message import (
    COMMIT_MESSAGE_ARTIFACT,
    delete_commit_message_artifacts,
    read_commit_message_from_path,
    read_commit_message_payload_from_path,
)
from ralph.mcp.session_plan import build_session_mcp_plan
from ralph.phases.required_artifacts import (
    build_required_artifacts,
    resolve_phase_required_artifact,
)
from ralph.pipeline.effects import CommitEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.legacy_console_display import LegacyConsoleDisplay, get_display_context
from ralph.workspace import FsWorkspace

if TYPE_CHECKING:
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.policy.models import AgentsPolicy, PolicyBundle

_PORCELAIN_STATUS_PREFIX_LEN = 3

if TYPE_CHECKING:

    class _CreateCommitFn(Protocol):
        def __call__(self, repo_root: Path | str, message: str, **kwargs: object) -> str: ...

    class _StageAllFn(Protocol):
        def __call__(self, repo_root: Path | str) -> None: ...

    class _HasCommitWorkFn(Protocol):
        def __call__(self, repo_root: Path) -> bool: ...

    class _RenderCommitMessageFn(Protocol):
        def __call__(self, repo_root: Path, display_context: object) -> None: ...


def execute_commit_effect(
    effect: CommitEffect,
    repo_root: Path,
    display: ParallelDisplay | LegacyConsoleDisplay | None = None,
    **opts: object,
) -> PipelineEvent:
    """Execute a commit effect, creating or skipping a git commit."""
    verbosity = cast("Verbosity", opts.get("verbosity", Verbosity.VERBOSE))
    _raw_create = opts.get("create_commit_fn")
    _create_commit_fn: _CreateCommitFn = cast(
        "_CreateCommitFn", _raw_create if callable(_raw_create) else create_commit
    )
    _raw_stage = opts.get("stage_all_fn")
    _stage_all_fn: _StageAllFn = cast(
        "_StageAllFn", _raw_stage if callable(_raw_stage) else stage_all
    )
    _raw_has_work = opts.get("has_commit_work_fn")
    _has_commit_work_fn: _HasCommitWorkFn = cast(
        "_HasCommitWorkFn", _raw_has_work if callable(_raw_has_work) else _repo_has_commit_work
    )
    try:
        payload = _read_commit_effect_payload(effect)
        message = _read_commit_effect_message(effect)
        if payload is None or not message:
            logger.error("Commit message file is empty: {}", effect.message_file)
            return PipelineEvent.COMMIT_FAILURE
        if payload.get("type") == "skip" or message.strip().lower().startswith("skip:"):
            logger.info("Commit agent requested skip — skipping commit execution")
            cleanup_commit_message_artifacts(repo_root)
            return PipelineEvent.COMMIT_SKIPPED
        if not _has_commit_work_fn(repo_root):
            logger.info("Skipping commit because the worktree is empty")
            cleanup_commit_message_artifacts(repo_root)
            return PipelineEvent.COMMIT_SKIPPED
        _stage_commit_scope(repo_root, payload, _stage_all_fn)
        sha = _create_commit_fn(str(repo_root), message)
        logger.info("Created commit: {}", sha[:8])
        _raw_render = opts.get("render_commit_message_fn")
        _render_commit_fn = cast(
            "_RenderCommitMessageFn",
            _raw_render if callable(_raw_render) else render_commit_message,
        )
        with suppress(Exception):
            _render_commit_fn(repo_root, get_display_context(display))
        if verbosity != Verbosity.QUIET and hasattr(display, "record_artifact_outcome"):
            with suppress(Exception):
                cast("ParallelDisplay", display).record_artifact_outcome(f"sha={sha[:8]}")
        cleanup_commit_message_artifacts(repo_root)
    except Exception as exc:
        logger.error("Commit failed: {}", exc)
        return PipelineEvent.COMMIT_FAILURE
    return PipelineEvent.COMMIT_SUCCESS


def _read_commit_effect_payload(effect: CommitEffect) -> dict[str, object] | None:
    return read_commit_message_payload_from_path(Path(effect.message_file))


def _read_commit_effect_message(effect: CommitEffect) -> str:
    return read_commit_message_from_path(Path(effect.message_file)) or ""


def _stage_commit_scope(
    repo_root: Path,
    payload: dict[str, object],
    stage_all_fn: _StageAllFn,
) -> None:
    include_paths = _commit_include_paths(repo_root, payload)
    if include_paths is None:
        stage_all_fn(str(repo_root))
        return
    stage_files(str(repo_root), include_paths)


def _commit_include_paths(repo_root: Path, payload: dict[str, object]) -> list[str] | None:
    raw_files = payload.get("files")
    if isinstance(raw_files, list):
        return [path for path in raw_files if isinstance(path, str)]
    raw_excluded = payload.get("excluded_files")
    if not isinstance(raw_excluded, list):
        return None
    excluded = {
        path.strip()
        for item in raw_excluded
        if isinstance(item, dict)
        and isinstance((path := item.get("path")), str)
        and path.strip()
    }
    changed = _changed_commit_paths(repo_root)
    return [path for path in changed if path not in excluded]


def _changed_commit_paths(repo_root: Path) -> list[str]:
    status_output = cast("str", Repo(repo_root).git.status("--porcelain"))
    status_lines = status_output.splitlines()
    changed: list[str] = []
    for line in status_lines:
        if len(line) <= _PORCELAIN_STATUS_PREFIX_LEN:
            continue
        path_part = line[_PORCELAIN_STATUS_PREFIX_LEN:]
        if " -> " in path_part:
            _, _, path_part = path_part.partition(" -> ")
        path = path_part.strip()
        if path and path not in changed:
            changed.append(path)
    return changed


def _repo_has_commit_work(repo_root: Path) -> bool:
    return Repo(repo_root).is_dirty(untracked_files=True)


def cleanup_commit_message_artifacts(repo_root: Path) -> None:
    """Remove commit message artifacts left by a prior commit phase."""
    delete_commit_message_artifacts(repo_root)


def should_early_skip_commit(workspace_root: Path) -> bool:
    """Return True iff the worktree is clean and the commit phase should be skipped early.

    Fails open (returns False) when git state cannot be inspected so the pipeline
    falls back to the late-skip guard in execute_commit_effect().
    """
    try:
        return not _repo_has_commit_work(workspace_root)
    except Exception:
        return False


def commit_effect(workspace_root: Path) -> CommitEffect:
    """Build a CommitEffect pointing at the standard commit message artifact path."""
    return CommitEffect(message_file=str(workspace_root / COMMIT_MESSAGE_ARTIFACT))


def clear_phase_output_artifacts(
    workspace: FsWorkspace,
    phase: str,
    **opts: object,
) -> None:
    """Remove stale per-phase artifacts before invoking an agent.

    Planning artifacts are an exception: fresh-vs-preserve invalidation is
    owned by prompt materialization, which has the semantic context to
    distinguish fresh planning from loopback, retry, and resume. Clearing plan
    outputs again here reintroduces a second, less-informed authority and can
    delete the live plan handoff on non-fresh planning entries.
    """
    drain = cast("str | None", opts.get("drain"))
    policy_bundle = cast("PolicyBundle | None", opts.get("policy_bundle"))
    effective_drain = drain or phase
    required_artifact = (
        resolve_phase_required_artifact(
            policy_bundle.pipeline,
            policy_bundle.artifacts,
            phase=phase,
            drain=effective_drain,
        )
        if policy_bundle is not None
        else None
    )
    if required_artifact is not None and required_artifact.artifact_type == "plan":
        return
    for path in phase_output_artifact_paths(phase, drain=drain, policy_bundle=policy_bundle):
        workspace.remove(path)


def phase_output_artifact_paths(
    phase: str, *, drain: str | None = None, policy_bundle: PolicyBundle | None = None
) -> tuple[str, ...]:
    """Return paths of all output artifacts produced by a phase."""
    paths: list[str] = []
    effective_drain = drain or phase
    ra = (
        build_required_artifacts(policy_bundle.artifacts).get(effective_drain)
        if policy_bundle is not None
        else None
    )
    if ra is not None:
        paths.append(ra.json_path)
        if ra.markdown_path is not None:
            paths.append(ra.markdown_path)
    if policy_bundle is not None:
        phase_def = policy_bundle.pipeline.phases.get(phase)
        if phase_def is not None:
            if phase_def.parallelization is not None:
                paths.append(".agent/artifacts/parallel_development_summary.json")
            if phase_def.role == "commit" and ra is None:
                paths.append(COMMIT_MESSAGE_ARTIFACT)
    return tuple(paths)


def default_mcp_capabilities_for_phase(
    phase: str,
    *,
    agents_policy: AgentsPolicy | None = None,
) -> set[str]:
    """Return the default MCP capability set for a given phase."""
    return set(
        build_session_mcp_plan(
            transport=None,
            drain=phase,
            workspace_path=None,
            agents_policy=agents_policy,
        ).capabilities
    )


repo_has_commit_work = _repo_has_commit_work
