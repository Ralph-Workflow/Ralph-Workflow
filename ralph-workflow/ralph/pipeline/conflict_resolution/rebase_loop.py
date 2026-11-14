from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.merge import (
    paths_with_conflict_markers,
    stage_paths,
    staged_conflict_marker_paths,
    unmerged_paths,
)
from ralph.git.operation_snapshot import OperationState, observe_operation_snapshot
from ralph.git.rebase.rebase import get_conflicted_files
from ralph.git.rebase.rebase_continuation import (
    ConflictRemainingError,
    NoRebaseInProgressError,
    RebaseContinuationError,
    continue_rebase_at,
    rebase_in_progress_at,
    verify_rebase_completed_at,
)
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.git.subprocess_runner import run_git
from ralph.pipeline._pending_rebase_continue import (
    finish_pending_rebase,
    prepare_pending_rebase,
    retain_pending_rebase_error,
)
from ralph.pipeline._retained_resolution_scope import retained_rebase_paths
from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.deterministic_resolution import (
    resolve_completion_in_place,
    try_deterministic_resolution,
)
from ralph.pipeline.conflict_resolution.graph import (
    TERMINAL_ABANDONED,
    TERMINAL_RESOLVED,
    route_after_stop,
)
from ralph.pipeline.conflict_resolution.ort_residue import remove_ort_residue
from ralph.pipeline.conflict_resolution.progress import (
    RebaseResolutionProgress,
    clear_progress,
    load_progress_for_rebase,
    save_progress,
)
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from ralph.pipeline.conflict_resolution.sight import ConflictKind, classify_conflict_kinds
from ralph.pipeline.conflict_resolution.stray_paths import (
    is_ralph_workspace_path,
    revert_unrequested_paths,
    worktree_dirty_paths,
)
from ralph.pipeline.resolution_outcome import (
    ConfigFailureOutcome,
    NextStrategyOutcome,
    ResolvedOutcome,
    RetryLaterOutcome,
    StopResolutionOutcome,
    UnknownOutcome,
)

if TYPE_CHECKING:
    from ralph.config.gitlink_policy_config import GitlinkPolicyConfig
    from ralph.config.models import UnifiedConfig

_UNKNOWN_SUBJECT = "(subject unavailable)"

_REBASE_ONTO_FILES = ("rebase-merge/onto", "rebase-apply/onto")

_REBASE_ORIG_HEAD_FILES = ("rebase-merge/orig-head", "rebase-apply/orig-head")

_REBASE_PROGRESS_FILES = (
    ("rebase-merge/msgnum", "rebase-merge/end"),
    ("rebase-apply/next", "rebase-apply/last"),
)

__all__ = [
    "RebaseStop",
    "RebaseStopResolver",
    "_resolve_rebase_in_progress",
    "active_rebase_resolution_session",
    "bind_active_rebase_resolution_session",
    "finish_resolved_rebase_stops",
    "record_landed_stop",
    "resolution_session_from_config",
    "resolve_rebase_in_progress",
]


_ACTIVE_REBASE_RESOLUTION_SESSION: ContextVar[ResolutionSession | None] = ContextVar(
    "active_rebase_resolution_session", default=None
)

_try_deterministic_resolution = try_deterministic_resolution
_remove_ort_residue = remove_ort_residue
_is_ralph_workspace_path = is_ralph_workspace_path
_revert_unrequested_paths = revert_unrequested_paths
_worktree_dirty_paths = worktree_dirty_paths


@dataclass(frozen=True)
class RebaseStop:
    sha: str
    subject: str
    conflicted_files: tuple[str, ...]
    stop_index: int
    stop_cap: int
    replay_index: int | None = None
    replay_total: int | None = None


type RebaseStopResolver = Callable[["Path", str, RebaseStop], bool | StopResolutionOutcome]

_ACTIVE_GITLINK_POLICY: ContextVar[GitlinkPolicyConfig | None] = ContextVar(
    "active_gitlink_policy", default=None
)


def _resolution_succeeded(result: bool | StopResolutionOutcome) -> bool:
    if isinstance(result, bool):
        return result
    from ralph.pipeline.resolution_outcome import ResolvedOutcome

    return isinstance(result, ResolvedOutcome)


def resolution_session_from_config(config: UnifiedConfig) -> ResolutionSession:
    limits = config.conflict_resolution
    return ResolutionSession(
        inactivity_timeout_seconds=limits.inactivity_timeout_seconds,
        max_rounds_per_stop=limits.max_rounds_per_stop,
        max_rebase_conflict_stops=limits.max_rebase_conflict_stops,
        max_fallback_agents=limits.max_fallback_agents,
        gitlink_policy=limits.gitlink_policy,
    )


@contextmanager
def bind_active_rebase_resolution_session(session: ResolutionSession) -> Iterator[None]:
    token = _ACTIVE_REBASE_RESOLUTION_SESSION.set(session)
    try:
        yield
    finally:
        _ACTIVE_REBASE_RESOLUTION_SESSION.reset(token)


def active_rebase_resolution_session() -> ResolutionSession | None:
    return _ACTIVE_REBASE_RESOLUTION_SESSION.get()


def _resolve_rebase_in_progress(
    root: Path,
    target: str,
    resolver: RebaseStopResolver,
    *,
    stop_cap: int = 10,
    session: object | None = None,
) -> bool:
    effective_session = session if isinstance(session, ResolutionSession) else None
    try:
        effective_cap = _configured_stop_cap(effective_session, stop_cap)
        if effective_session is None:
            return _resolve_stops(root, target, resolver, effective_cap)
        policy_token = _ACTIVE_GITLINK_POLICY.set(effective_session.gitlink_policy)
        try:
            with bind_active_rebase_resolution_session(effective_session):
                return _resolve_stops(root, target, resolver, effective_cap)
        finally:
            _ACTIVE_GITLINK_POLICY.reset(policy_token)
    except Exception as exc:
        logger.warning(
            "conflict_resolution: rebase resolution loop failed for '{}': {}",
            target,
            exc,
        )
        return False


resolve_rebase_in_progress = _resolve_rebase_in_progress


def _configured_stop_cap(session: ResolutionSession | None, fallback: int) -> int:
    raw: object = getattr(session, "max_rebase_conflict_stops", fallback)
    return raw if isinstance(raw, int) and raw > 0 else fallback


def _resolve_stops(
    root: Path,
    target: str,
    resolver: RebaseStopResolver,
    stop_cap: int,
) -> bool:
    base = _rebase_base_sha(root) or target
    entry_landed = frozenset(_landed_shas_at_entry(root))
    skipped_entry_shas: set[str] = set()
    stops_spent = 0
    stops_seen = 0
    resolved_shas: list[str] = []
    while rebase_in_progress_at(root):
        skipped_before = len(skipped_entry_shas)
        stops_seen += 1
        if not _resolve_one_stop(
            root,
            target,
            resolver,
            stops_seen,
            stop_cap,
            entry_landed,
            skipped_entry_shas,
            resolved_shas,
        ):
            return False
        if len(skipped_entry_shas) > skipped_before:
            continue
        head_after = _rev_parse_rebase_head(root)
        if head_after and resolved_shas and head_after != resolved_shas[-1]:
            stops_spent = 0
        else:
            stops_spent += 1
        route = route_after_stop(stops_spent, not rebase_in_progress_at(root), stop_cap)
        if route == TERMINAL_RESOLVED:
            break
        if route == TERMINAL_ABANDONED:
            logger.warning(
                "conflict_resolution: rebase still conflicting after {} stop(s); "
                "abandoning in favour of the endpoint merge",
                stops_seen,
            )
            return False

    return verify_rebase_completed_at(root, base)


resolve_rebase_in_progress = _resolve_rebase_in_progress


def _record_resolver_outcome_on_session(
    outcome: object,
    active_session: ResolutionSession | None,
) -> None:
    if isinstance(outcome, bool) or active_session is None:
        return
    if isinstance(outcome, ResolutionOutcome):
        if outcome.failure_cause != ResolutionFailureCause.NONE:
            active_session.last_failure_cause = outcome.failure_cause
        if outcome.reason is not None:
            raw_reason: object = outcome.reason
            if isinstance(raw_reason, ResolutionTerminationReason):
                active_session.terminal_reason = raw_reason
            elif isinstance(raw_reason, str) and not active_session.exhaustion_reason:
                active_session.exhaustion_reason = raw_reason
    elif isinstance(outcome, NextStrategyOutcome):
        active_session.terminal_reason = ResolutionTerminationReason.CANDIDATE_EXHAUSTED
        active_session.exhaustion_reason = outcome.strategy
    elif isinstance(outcome, ConfigFailureOutcome):
        active_session.last_failure_cause = ResolutionFailureCause.CONFIG_FAILURE
        active_session.exhaustion_reason = str(outcome.parse_error)
    elif isinstance(outcome, UnknownOutcome):
        active_session.last_failure_cause = ResolutionFailureCause.AGENT_FAILURE
        active_session.exhaustion_reason = str(outcome.error)
    elif isinstance(outcome, RetryLaterOutcome) and outcome.on_change:
        active_session.exhaustion_reason = outcome.on_change


def _resolve_one_stop(
    root: Path,
    target: str,
    resolver: RebaseStopResolver,
    stop_index: int,
    stop_cap: int,
    entry_landed: frozenset[str],
    skipped_entry_shas: set[str],
    resolved_shas: list[str] | None = None,
) -> bool:
    stop = _read_stop(root, stop_index, stop_cap)
    if stop is None:
        return _continue_already_staged_stop(root, stop_index)
    if resolved_shas is not None:
        resolved_shas.append(stop.sha)
    if stop.sha in entry_landed and stop.sha not in skipped_entry_shas:
        skipped_entry_shas.add(stop.sha)
        logger.info(
            "conflict_resolution: resumed from sidecar; skipping already-landed stop {}",
            stop.sha,
        )
        return _continue_past(root, stop)
    before = _worktree_dirty_paths(root)
    if before is None:
        return False
    if _try_deterministic_resolution(
        root,
        stop,
        gitlink_policy=_ACTIVE_GITLINK_POLICY.get(),
    ):
        if _stage_and_prove(root, stop) and _continue_past(root, stop):
            return True
        logger.info(
            "conflict_resolution: deterministic resolution of stop {} did not prove out; "
            "handing the stop to the resolver",
            stop.stop_index,
        )
    outcome = resolver(root, target, stop)
    _record_resolver_outcome_on_session(outcome, active_rebase_resolution_session())
    if not _resolution_succeeded(outcome):
        logger.info(
            "conflict_resolution: rebase stop {} ({}) was not resolved",
            stop.stop_index,
            stop.sha,
        )
        return False
    return (
        _touched_nothing_unexpected(root, stop, before)
        and _stage_and_prove(root, stop)
        and _remove_ort_residue(root, stop.conflicted_files)
        and _continue_past(root, stop)
    )


def _continue_already_staged_stop(root: Path, stop_index: int) -> bool:
    if not _stop_is_genuinely_staged(root, stop_index):
        return False
    logger.info(
        "conflict_resolution: stop {} is staged and its files are marker-free; "
        "continuing the rebase",
        stop_index,
    )
    try:
        prepare_pending_rebase(root)
        continue_rebase_at(root)
    except NoRebaseInProgressError:
        finish_pending_rebase(root)
        return True
    except (ConflictRemainingError, RebaseContinuationError) as exc:
        retain_pending_rebase_error(root, exc)
        logger.warning(
            "conflict_resolution: could not continue the already-staged stop {}: {}",
            stop_index,
            exc,
        )
        return False
    finish_pending_rebase(root)
    return True


def _stop_is_genuinely_staged(root: Path, stop_index: int) -> bool:
    if not rebase_in_progress_at(root):
        return False
    if not _rev_parse_rebase_head(root):
        return False
    if [path for path in unmerged_paths(root) if path != "<unmerged-path-query-failed>"]:
        return False
    marked: list[str] = [
        *paths_with_conflict_markers(root, _staged_paths(root)),
        *staged_conflict_marker_paths(root),
    ]
    if marked:
        logger.warning(
            "conflict_resolution: stop {} is staged but conflict markers survive in {}; "
            "not continuing the rebase",
            stop_index,
            ", ".join(marked),
        )
        return False
    return True


def _staged_paths(root: Path) -> list[str]:
    result = run_git(
        ("diff", "--cached", "--name-only", "-z"),
        cwd=root,
        label="git-staged-paths",
    )
    if result.returncode != 0:
        return []
    return [entry for entry in result.stdout.split("\0") if entry]


def _rebase_base_sha(root: Path) -> str | None:
    return _read_rebase_state_sha(root, _REBASE_ONTO_FILES, label="git-rebase-onto-path")


def _rebase_orig_head_sha(root: Path) -> str | None:
    return _read_rebase_state_sha(root, _REBASE_ORIG_HEAD_FILES, label="git-rebase-orig-head-path")


def current_rebase_identity(root: Path) -> tuple[str | None, str | None]:
    return _rebase_orig_head_sha(root), _rebase_base_sha(root)


def _read_rebase_state_sha(root: Path, relatives: tuple[str, ...], *, label: str) -> str | None:
    gitdir: Path | None = None
    try:
        from ralph.git.operations import _resolve_gitdir_paths

        gitdir, _ = _resolve_gitdir_paths(root)
    except (OSError, ValueError) as exc:
        logger.debug("direct rebase state gitdir lookup failed: {}", exc)
        gitdir = None
    for relative in relatives:
        state_file: Path | None = None
        if gitdir is not None:
            candidate = gitdir / relative
            if candidate.is_file():
                state_file = candidate
            else:
                continue
        if state_file is None:
            result = run_git(("rev-parse", "--git-path", relative), cwd=root, label=label)
            if result.returncode != 0:
                return None
            state_file = Path(result.stdout.strip())
            if not state_file.is_absolute():
                state_file = root / state_file
        try:
            sha = state_file.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if sha:
            return sha
    return None


def _read_replay_progress(
    root: Path, *, read_text: Callable[[Path], str] | None = None
) -> tuple[int, int] | None:
    reader = _read_rebase_state_text if read_text is None else read_text
    for current_file, total_file in _REBASE_PROGRESS_FILES:
        current = _read_rebase_state_int(root, current_file, read_text=reader)
        total = _read_rebase_state_int(root, total_file, read_text=reader)
        if current is None or total is None:
            continue
        if total >= 1 and 1 <= current <= total:
            return current, total
    return None


def _read_rebase_state_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _read_rebase_state_int(
    root: Path, relative: str, *, read_text: Callable[[Path], str] = _read_rebase_state_text
) -> int | None:
    state_path: Path | None = None
    try:
        from ralph.git.operations import _resolve_gitdir_paths

        gitdir, _ = _resolve_gitdir_paths(root)
        if gitdir is not None:
            candidate = gitdir / relative
            if candidate.is_file():
                state_path = candidate
    except (OSError, ValueError) as exc:
        logger.debug("direct rebase state int lookup failed: {}", exc)
    if state_path is None:
        result = run_git(
            ("rev-parse", "--git-path", relative),
            cwd=root,
            label="git-rebase-progress-path",
        )
        if result.returncode != 0:
            return None
        parsed = Path(result.stdout.strip())
        state_path = parsed if parsed.is_absolute() else root / parsed
    try:
        return int(read_text(state_path).strip())
    except (OSError, ValueError):
        return None


def _read_stop(root: Path, stop_index: int, stop_cap: int) -> RebaseStop | None:
    sha = _rev_parse_rebase_head(root)
    if not sha:
        logger.warning(
            "conflict_resolution: could not read REBASE_HEAD for the paused "
            "rebase; declining to resolve"
        )
        return None
    conflicted = retained_rebase_paths(
        root,
        sha,
        tuple(get_conflicted_files(repo_root=root) or staged_conflict_marker_paths(root)),
    )
    if "<staged-marker-query-failed>" in conflicted:
        return None
    if not conflicted:
        logger.warning(
            "conflict_resolution: rebase is paused with no conflicted path; declining to resolve"
        )
        return None
    progress = _read_replay_progress(root)
    return RebaseStop(
        sha=sha,
        subject=_rebase_head_subject(root),
        conflicted_files=conflicted,
        stop_index=stop_index,
        stop_cap=stop_cap,
        replay_index=None if progress is None else progress[0],
        replay_total=None if progress is None else progress[1],
    )


def _touched_nothing_unexpected(
    root: Path,
    stop: RebaseStop,
    before: frozenset[str],
) -> bool:
    after = _worktree_dirty_paths(root)
    if after is None:
        return False
    unexpected = sorted(
        path
        for path in after - before - frozenset(stop.conflicted_files)
        if not _is_ralph_workspace_path(path)
    )
    if not unexpected:
        return True
    unexpected_paths = tuple(unexpected)
    if _revert_unrequested_paths(root, unexpected_paths):
        logger.warning(
            "conflict_resolution: resolver edited unrequested path(s) at stop {}: {}; "
            "reverted those paths and kept the in-scope resolution",
            stop.stop_index,
            unexpected,
        )
        return True
    logger.warning(
        "conflict_resolution: resolver edited unrequested path(s) at stop {}: {}; "
        "rejecting the resolution",
        stop.stop_index,
        unexpected,
    )
    return False


def _landed_shas_at_entry(root: Path) -> tuple[str, ...]:
    feature_sha, target_sha = current_rebase_identity(root)
    progress = load_progress_for_rebase(root, feature_sha=feature_sha, target_sha=target_sha)
    if progress is None:
        return ()
    if progress.landed_shas and _is_at_the_first_replay(root):
        logger.warning(
            "conflict_resolution: discarding a progress record claiming {} landed stop(s) "
            "while the replay is back at its first commit; the earlier attempt was aborted",
            len(progress.landed_shas),
        )
        clear_progress(root)
        return ()
    return tuple(progress.landed_shas)


def _is_at_the_first_replay(root: Path) -> bool:
    position = _read_replay_progress(root)
    return position is not None and position[0] == 1


def _stage_and_prove(root: Path, stop: RebaseStop) -> bool:
    _stage_observed_resolutions(root, stop.conflicted_files)
    observed_before = unmerged_paths(root)
    if "<unmerged-path-query-failed>" in observed_before:
        return False
    requested = set(stop.conflicted_files)
    candidate_pool = observed_before if observed_before else stop.conflicted_files
    paths = [
        path
        for path in candidate_pool
        if path in requested
        and (
            not (root / path).exists()
            or ((root / path).is_file() and (root / path).stat().st_size > 0)
        )
        and path not in paths_with_conflict_markers(root, (path,))
    ]
    if not stage_paths(root, paths):
        logger.warning(
            "conflict_resolution: failed to stage resolved paths for stop {}: {}",
            stop.stop_index,
            paths,
        )
        return False
    marked: list[str] = [
        *paths_with_conflict_markers(root, paths),
        *staged_conflict_marker_paths(root),
    ]
    if marked:
        logger.warning(
            "conflict_resolution: conflict markers survive at stop {}: {}",
            stop.stop_index,
            ", ".join(sorted(set(marked))),
        )
        return False
    observed_after = unmerged_paths(root)
    if "<unmerged-path-query-failed>" in observed_after:
        return False
    if observed_after:
        logger.warning(
            "conflict_resolution: unmerged paths remain at stop {}: {}",
            stop.stop_index,
            observed_after,
        )
        return False
    return True


def finish_resolved_rebase_stops(root: Path, target: str) -> bool:
    snapshot = observe_operation_snapshot(root, target=target)
    if snapshot.operation_state is not OperationState.REBASE_PAUSED:
        return False
    succeeded = False
    while True:
        if not snapshot.unmerged_query_ok:
            break
        if not _stage_observed_resolutions(root, snapshot.unmerged_paths):
            break
        if snapshot.unmerged_paths:
            snapshot = observe_operation_snapshot(root, target=target)
            if not snapshot.unmerged_query_ok or snapshot.unmerged_paths:
                break
        if snapshot.staged_conflict_marker_paths:
            break
        try:
            continue_rebase_at(root, skip_empty=True)
        except NoRebaseInProgressError:
            succeeded = True
            break
        except (ConflictRemainingError, RebaseContinuationError):
            snapshot = observe_operation_snapshot(root, target=target)
            if snapshot.operation_state is OperationState.REBASE_PAUSED and snapshot.unmerged_paths:
                continue
            break
        snapshot = observe_operation_snapshot(root, target=target)
        if snapshot.operation_state is not OperationState.REBASE_PAUSED:
            succeeded = True
            break
    return succeeded


def _stage_observed_resolutions(root: Path, paths: tuple[str, ...]) -> bool:
    if not paths:
        return True
    kinds = classify_conflict_kinds(root, paths)
    resolve_completion_in_place(root, paths, kinds)
    stageable = tuple(
        path
        for path in paths
        if kinds.get(path) is ConflictKind.TEXT
        and (root / path).is_file()
        and (root / path).stat().st_size > 0
        and path not in paths_with_conflict_markers(root, (path,))
    )
    return not stageable or stage_paths(root, stageable)


def _continue_past(root: Path, stop: RebaseStop) -> bool:
    try:
        prepare_pending_rebase(root)
        continue_rebase_at(root, skip_empty=True)
    except NoRebaseInProgressError:
        finish_pending_rebase(root)
        record_landed_stop(root, stop)
        return True
    except (ConflictRemainingError, RebaseContinuationError) as exc:
        if _advanced_to_a_new_stop(root, stop):
            logger.info(
                "conflict_resolution: stop {} landed; the rebase stopped again on the next commit",
                stop.stop_index,
            )
            finish_pending_rebase(root)
            record_landed_stop(root, stop)
            return True
        retain_pending_rebase_error(root, exc)
        logger.warning(
            "conflict_resolution: could not continue the rebase past stop {}: {}",
            stop.stop_index,
            exc,
        )
        return False
    finish_pending_rebase(root)
    record_landed_stop(root, stop)
    return True


def record_landed_stop(root: Path, stop: RebaseStop) -> None:
    feature_sha, target_sha = current_rebase_identity(root)
    if feature_sha is None or target_sha is None:
        clear_progress(root)
        return
    progress = (
        load_progress_for_rebase(root, feature_sha=feature_sha, target_sha=target_sha)
        or RebaseResolutionProgress()
    )
    progress.record_landed(stop.sha)
    progress.remaining_paths = list(stop.conflicted_files)
    progress.feature_sha = feature_sha
    progress.target_sha = target_sha
    save_progress(root, progress)


def _advanced_to_a_new_stop(root: Path, stop: RebaseStop) -> bool:
    if not rebase_in_progress_at(root):
        return False
    current = _rev_parse_rebase_head(root)
    return bool(current) and current != stop.sha


def _rev_parse_rebase_head(root: Path) -> str:
    result = run_git(
        ("rev-parse", "REBASE_HEAD"),
        cwd=root,
        label="git-rebase-head-sha",
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _rebase_head_subject(root: Path) -> str:
    result = run_git(
        ("log", "-1", "--format=%s", "REBASE_HEAD"),
        cwd=root,
        label="git-rebase-head-subject",
    )
    if result.returncode != 0:
        return _UNKNOWN_SUBJECT
    return result.stdout.strip() or _UNKNOWN_SUBJECT
