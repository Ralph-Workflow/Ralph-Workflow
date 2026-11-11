
from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.hardening import COMMIT_PIN_CONFIG_ARGS
from ralph.git.merge import (
    MERGE_STATE_NONE,
    merge_refusal_obstruction,
    merge_state,
    observe_branch_sha,
)
from ralph.git.merge_obstructions import clear_untracked_merge_obstructions
from ralph.git.rebase.rebase import (
    RebaseConflicts,
    RebaseFailed,
    RebaseNoOp,
    rebase_in_progress,
    rebase_onto,
)
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.git.subprocess_runner import run_git
from ralph.pipeline._auto_integrate_rebase_merge_failure import (
    _environmental_rebase_reason,
    _failed_rebase_state,
)
from ralph.pipeline._auto_integrate_rebase_resolve import (
    RebaseRunResult,
    _clear_record_if_no_inflight_op,
    _resolve_conflicted_rebase,
    _resolve_rebase_with_config,
    verify_terminal_state,
)
from ralph.pipeline.auto_integrate_backup_refs import create_stale_resolution_snapshot
from ralph.pipeline.auto_integrate_outcome import (
    record_conflict,
    record_rebase_outcome,
    record_skip,
)
from ralph.pipeline.auto_integrate_record import (
    read_record,
    set_resolving_rebase,
)
from ralph.pipeline.auto_integrate_recovery import post_attempt_verify
from ralph.pipeline.auto_integrate_resolve import (
    RESOLUTION_FAILED,
    endpoint_merge_with_resolution,
)
from ralph.pipeline.conflict_resolution import (
    resolve_rebase_in_progress,
)
from ralph.pipeline.conflict_resolution.abort import abort_rebase_discarding_progress
from ralph.pipeline.integration_decision import (
    DecisionContext,
    DirtAttribution,
    NextAction,
    decide,
)
from ralph.pipeline.integration_observation import (
    DirtShape,
    Observation,
    WorktreeState,
    classify_worktree,
)
from ralph.pipeline.integration_owner import (
    charge_integration_attempt,
    ladder_rung_for_action,
    load_integration_attempts,
)
from ralph.pipeline.rebase_state import RebaseState, StartupRetentionReason

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.conflict_resolution_config import ConflictResolutionConfig
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.git.merge import MergeResult
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.pipeline.integration_actions import (
        AgentRunner,
        GitOps,
    )


SKIP_MERGE_COMMITS_IN_RANGE = "merge_commits_in_range"
SKIP_ROOT_COMMITS_IN_RANGE = "root_commits_in_range"
SKIP_ALL_EMPTY_REPLAY = "all_empty_replay"
SKIP_CASE_FOLDING_COLLISION = "case_folding_collision"
SKIP_RETRY_AFTER_MERGE_COMMIT = "retry_after_merge_commit"

_REASON_MERGE_COMMITS = "branch contains merge commits; routing to endpoint merge"
_REASON_ROOT_COMMITS = "branch contains root commits; routing to endpoint merge"
_REASON_ALL_EMPTY = "branch has no replayable commits; routing to endpoint merge"
_REASON_CASE_COLLISION = "branch range has case-folding path collision; routing to endpoint merge"

REBASE_SKIPPED_FOR_MERGE = "rebase skipped on retry to preserve the integration merge commit"

SKIP_REASON_REGISTRY: dict[str, tuple[str, str]] = {
    SKIP_MERGE_COMMITS_IN_RANGE: (
        "git rev-list --merges <target>..HEAD is non-empty",
        _REASON_MERGE_COMMITS,
    ),
    SKIP_ROOT_COMMITS_IN_RANGE: (
        "git rev-list --max-parents=0 <target>..HEAD is non-empty",
        _REASON_ROOT_COMMITS,
    ),
    SKIP_ALL_EMPTY_REPLAY: (
        "the replay range is empty, or every replayed commit is already upstream",
        _REASON_ALL_EMPTY,
    ),
    SKIP_CASE_FOLDING_COLLISION: (
        "two distinct replay paths fold to the same case-insensitive name",
        _REASON_CASE_COLLISION,
    ),
    SKIP_RETRY_AFTER_MERGE_COMMIT: (
        "the immediately preceding attempt of this seam produced a merge commit",
        REBASE_SKIPPED_FOR_MERGE,
    ),
}

__all__ = [
    "MERGE_STATE_NONE",
    "REBASE_SKIPPED_FOR_MERGE",
    "SKIP_ALL_EMPTY_REPLAY",
    "SKIP_CASE_FOLDING_COLLISION",
    "SKIP_MERGE_COMMITS_IN_RANGE",
    "SKIP_REASON_REGISTRY",
    "SKIP_RETRY_AFTER_MERGE_COMMIT",
    "SKIP_ROOT_COMMITS_IN_RANGE",
    "RebaseRunResult",
    "_resolve_rebase_with_config",
    "is_registered_skip_reason",
    "merge_state",
    "read_record",
    "rebase_in_progress",
    "registered_skip_slugs",
    "resolve_rebase_in_progress",
    "run_rebase_or_merge",
    "set_resolving_rebase",
    "skip_reason_description",
    "skip_reason_observation",
    "skip_reason_slug",
    "skip_reason_text",
    "with_skip_reason",
]


def registered_skip_slugs() -> frozenset[str]:
    return frozenset(SKIP_REASON_REGISTRY)


def skip_reason_observation(slug: str) -> str:
    return _registered_spec(slug)[0]


def skip_reason_description(slug: str) -> str:
    return _registered_spec(slug)[1]


def skip_reason_text(slug: str) -> str:
    return f"{slug}: {skip_reason_description(slug)}"


def skip_reason_slug(text: str) -> str | None:
    for part in text.split("; "):
        head, separator, _tail = part.partition(": ")
        if separator and head in SKIP_REASON_REGISTRY:
            return head
    return None


def is_registered_skip_reason(text: str) -> bool:
    return skip_reason_slug(text) is not None


def with_skip_reason(record: RebaseState, skip_reason: str | None) -> RebaseState:
    if skip_reason is None:
        return record
    existing = record.last_reason or ""
    if skip_reason in existing:
        return record
    combined = f"{existing}; {skip_reason}" if existing else skip_reason
    return record.model_copy(update={"last_reason": combined})


def _registered_spec(slug: str) -> tuple[str, str]:
    spec = SKIP_REASON_REGISTRY.get(slug)
    if spec is None:
        raise ValueError(
            f"unregistered auto-rebase skip reason: {slug!r}; "
            f"the closed set is {sorted(SKIP_REASON_REGISTRY)}"
        )
    return spec


def _environmental_rebase_result(
    root: Path,
    target: str,
    failure: RebaseFailed,
    reason: str,
    git_ops: GitOps | None,
) -> RebaseRunResult:
    _abort_rebase_after_conflict(root, git_ops=git_ops)
    state = record_skip(
        reason=reason,
        target=target,
        failure_cause=ResolutionFailureCause.CONTEXT_FAILURE,
    )
    if rebase_in_progress(root):
        post_attempt_verify(root, expected_head_sha=None, owns_resolution=False)
        state = state.set_recovery_record_retained(StartupRetentionReason.FAILED_ABORT)
    else:
        _verify_terminal_probe(root, expected_head_sha=None, owns_resolution=False)
    return RebaseRunResult(
        rebase_outcome=failure,
        merge_attempted=False,
        merge_outcome=None,
        short_circuit=state,
    )


def run_rebase_or_merge(
    root: Path,
    target: str,
    conflict_resolver: ConflictResolver | None,
    *,
    prefer_merge: bool = False,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    display: ParallelDisplay | None = None,
    conflict_resolution_config: ConflictResolutionConfig | None = None,
    git_ops: GitOps | None = None,
    agent_runner: AgentRunner | None = None,
) -> RebaseRunResult:
    if prefer_merge:
        retry_reason = skip_reason_text(SKIP_RETRY_AFTER_MERGE_COMMIT)
        return replace(
            _endpoint_merge_result(
                root,
                target,
                RebaseNoOp(retry_reason),
                conflict_resolver,
            ),
            skip_reason=retry_reason,
        )
    routing_reason = _range_routing_probe(root, target)
    if routing_reason is not None:
        logger.info("auto_integrate: {} (target '{}')", routing_reason, target)
        return replace(
            _endpoint_merge_result(
                root,
                target,
                RebaseNoOp(routing_reason),
                conflict_resolver,
            ),
            skip_reason=routing_reason,
        )
    clear_untracked_merge_obstructions(root, target)
    rebase_outcome = (
        git_ops.rebase_onto(root, target)
        if git_ops is not None
        else rebase_onto(target, repo_root=root)
    )
    if isinstance(rebase_outcome, RebaseFailed):
        environmental_reason = _environmental_rebase_reason(rebase_outcome)
        if environmental_reason is not None:
            return _environmental_rebase_result(
                root,
                target,
                rebase_outcome,
                environmental_reason,
                git_ops,
            )
    if not isinstance(rebase_outcome, (RebaseConflicts, RebaseFailed)):
        return RebaseRunResult(
            rebase_outcome=rebase_outcome,
            merge_attempted=False,
            merge_outcome=None,
            short_circuit=None,
        )

    if isinstance(rebase_outcome, RebaseConflicts) and rebase_stop_resolver is not None:
        resolved = _resolve_conflicted_rebase(
            root,
            target,
            rebase_stop_resolver,
            display,
            conflict_resolution_config,
            agent_runner,
        )
        if resolved is not None:
            return resolved

    return _fallback_to_endpoint_merge(root, target, rebase_outcome, conflict_resolver, git_ops)


def range_routing_reason(root: Path, target: str) -> str | None:
    total = _rev_list_count(root, target, extra_args=())
    if total is None:
        return None
    if total == 0:
        return skip_reason_text(SKIP_ALL_EMPTY_REPLAY)
    return _range_topology_reason(root, target, total)


def _range_topology_reason(root: Path, target: str, total: int) -> str | None:
    merges = _rev_list_count(root, target, extra_args=("--merges",))
    if merges is not None and merges > 0:
        return skip_reason_text(SKIP_MERGE_COMMITS_IN_RANGE)
    roots = _rev_list_count(root, target, extra_args=("--max-parents=0",))
    if roots is not None and roots > 0:
        return skip_reason_text(SKIP_ROOT_COMMITS_IN_RANGE)
    if _all_empty_replay(root, target, total):
        return skip_reason_text(SKIP_ALL_EMPTY_REPLAY)
    if _range_has_case_collision(root, target):
        return skip_reason_text(SKIP_CASE_FOLDING_COLLISION)
    return None


_DEFAULT_RANGE_ROUTING = range_routing_reason
_range_routing_reason = range_routing_reason


def _range_routing_probe(root: Path, target: str) -> str | None:
    fn = (
        range_routing_reason
        if range_routing_reason is not _DEFAULT_RANGE_ROUTING
        else (
            _range_routing_reason
            if _range_routing_reason is not _DEFAULT_RANGE_ROUTING
            else _DEFAULT_RANGE_ROUTING
        )
    )
    return fn(root, target)


def _range_has_case_collision(root: Path, target: str) -> bool:
    result = run_git(
        ("diff", "--name-only", "-z", f"{target}..HEAD"),
        cwd=root,
        label="auto-integrate:case-collision-paths",
    )
    if result.returncode != 0:
        return False
    seen: set[str] = set()
    for path in (item for item in result.stdout.split("\0") if item):
        folded = path.casefold()
        if folded in seen:
            return True
        seen.add(folded)
    return False


def _rev_list_count(root: Path, target: str, *, extra_args: tuple[str, ...]) -> int | None:
    result = run_git(
        ("rev-list", "--count", *extra_args, f"{target}..HEAD"),
        cwd=root,
        label="auto-integrate:rev-list-count",
    )
    if result.returncode != 0:
        logger.warning(
            "auto_integrate: rev-list {} for {}..HEAD exited {}: {}; "
            "treating topology as unknown (will run the rebase)",
            extra_args,
            target,
            result.returncode,
            (result.stderr or result.stdout).strip()[:200],
        )
        return None
    try:
        return int(result.stdout.strip())
    except ValueError:
        logger.warning(
            "auto_integrate: rev-list {} for {}..HEAD returned non-integer "
            "stdout {!r}; treating topology as unknown",
            extra_args,
            target,
            result.stdout.strip()[:200],
        )
        return None


def _all_empty_replay(root: Path, target: str, feature_count: int) -> bool:
    if feature_count == 0:
        return False
    cherry_count = _rev_list_count(root, target, extra_args=("--cherry-pick", "--no-merges"))
    if cherry_count is None:
        return False
    return cherry_count == 0


def _fallback_to_endpoint_merge(
    root: Path,
    target: str,
    rebase_outcome: RebaseConflicts | RebaseFailed,
    conflict_resolver: ConflictResolver | None,
    git_ops: GitOps | None = None,
) -> RebaseRunResult:
    _abort_rebase_after_conflict(root, git_ops=git_ops)
    if rebase_in_progress(root):
        post_attempt_verify(
            root, expected_head_sha=None, owns_resolution=conflict_resolver is not None
        )
        return RebaseRunResult(
            rebase_outcome=rebase_outcome,
            merge_attempted=False,
            merge_outcome=None,
            short_circuit=(
                _failed_rebase_state(rebase_outcome, target)
                if isinstance(rebase_outcome, RebaseFailed)
                else record_conflict(
                    reason="rebase in-progress after abort",
                    target=target,
                    failure_cause=ResolutionFailureCause.CONTEXT_FAILURE,
                )
            ),
        )
    _verify_terminal_probe(
        root, expected_head_sha=None, owns_resolution=conflict_resolver is not None
    )

    return _endpoint_merge_result(root, target, rebase_outcome, conflict_resolver)


_DEFAULT_VERIFY_TERMINAL = verify_terminal_state
_verify_terminal_state = verify_terminal_state


def _verify_terminal_probe(
    root: Path,
    *,
    expected_head_sha: str | None,
    owns_resolution: bool,
) -> None:
    fn = (
        verify_terminal_state
        if verify_terminal_state is not _DEFAULT_VERIFY_TERMINAL
        else (
            _verify_terminal_state
            if _verify_terminal_state is not _DEFAULT_VERIFY_TERMINAL
            else _DEFAULT_VERIFY_TERMINAL
        )
    )
    fn(root, expected_head_sha=expected_head_sha, owns_resolution=owns_resolution)


def _restore_blocked_paths(root: Path, paths: tuple[str, ...]) -> bool:
    if not paths:
        return True
    result = run_git(
        (
            *COMMIT_PIN_CONFIG_ARGS,
            "restore",
            "--worktree",
            "--staged",
            "--source=HEAD",
            "--",
            *paths,
        ),
        cwd=root,
        label="auto-integrate:refusal-restore",
    )
    return result.returncode == 0


def _preserve_refusal_paths(root: Path) -> tuple[str | None, str | None]:
    try:
        head_sha, query_ok = observe_branch_sha(root, "HEAD")
        if not query_ok or not head_sha:
            return None, "HEAD unreadable; snapshot refused"
        backup_ref, _feature_sha = create_stale_resolution_snapshot(root, None, head_sha)
    except Exception as exc:
        return None, str(exc) or type(exc).__name__
    return backup_ref, None


def _reobserve_corrected_state(root: Path, target: str) -> tuple[Observation, DirtShape] | None:
    from ralph.pipeline.integration_director import IntegrationDirector

    observation = IntegrationDirector(root, target=target).observe()
    worktree = observation.worktree
    if worktree is WorktreeState.UNKNOWN:
        return None
    if worktree in (WorktreeState.CLEAN, WorktreeState.UNTRACKED):
        return replace(observation, worktree=WorktreeState.TRACKED_CHANGES), (
            DirtShape.UNSTAGED_MODIFICATION
        )
    return observation, classify_worktree(worktree).dirt_shape


def _refusal_bridge_reason(
    merge_result: MergeResult,
    action: NextAction,
    observation: Observation,
    paths: tuple[str, ...],
    preserved_ref: str | None,
    restore_ok: bool | None,
    preservation_error: str | None,
) -> str:
    parts = [
        f"endpoint merge refused ({merge_result.reason})",
        f"decided {action.value} from re-observed worktree={observation.worktree.value}",
    ]
    if preservation_error is not None:
        parts.append(f"preservation failed: {preservation_error}")
    elif preserved_ref is not None:
        parts.append(f"preserved {len(paths)} path(s) ({', '.join(paths)}) at {preserved_ref}")
        parts.append(
            "restored baseline"
            if restore_ok
            else "restore FAILED; the refusal will recur against the bounded budget"
        )
    parts.append("retrying rebase-first")
    return "; ".join(parts)


def _bridge_merge_refusal(
    root: Path,
    target: str,
    rebase_outcome: RebaseConflicts | RebaseFailed | RebaseNoOp,
    merge_result: MergeResult,
) -> RebaseRunResult | None:
    paths = merge_refusal_obstruction(merge_result)
    if paths is None:
        if not merge_result.blocked_paths:
            return None
        paths = merge_result.blocked_paths
    if merge_state(root) != MERGE_STATE_NONE:
        return None
    corrected = _reobserve_corrected_state(root, target)
    if corrected is None:
        return None
    observation, shape = corrected
    action = decide(
        DecisionContext(
            observation=observation,
            attempts=load_integration_attempts(root),
            dirt_shape=shape,
            dirt_attribution=DirtAttribution.UNOWNED,
        )
    )
    preserved_ref: str | None = None
    restore_ok: bool | None = None
    preservation_error: str | None = None
    if action is NextAction.SAVE_AND_RESTORE:
        preserved_ref, preservation_error = _preserve_refusal_paths(root)
        if preserved_ref is not None:
            restore_ok = _restore_blocked_paths(root, paths)
    elif action is not NextAction.WAIT_FOR_ENVIRONMENT and ladder_rung_for_action(action) is None:
        return None
    charge_integration_attempt(root, observation=observation, next_action=action)
    reason = _refusal_bridge_reason(
        merge_result, action, observation, paths, preserved_ref, restore_ok, preservation_error
    )
    logger.warning("auto_integrate: {}", reason)
    return RebaseRunResult(
        rebase_outcome=rebase_outcome,
        merge_attempted=True,
        merge_outcome=merge_result,
        short_circuit=record_skip(
            reason=reason,
            target=target,
            failure_cause=ResolutionFailureCause.CONTEXT_FAILURE,
        ),
    )


def _endpoint_merge_result(
    root: Path,
    target: str,
    rebase_outcome: RebaseConflicts | RebaseFailed | RebaseNoOp,
    conflict_resolver: ConflictResolver | None,
) -> RebaseRunResult:
    merge_result = endpoint_merge_with_resolution(root, target, conflict_resolver)
    if merge_result is None:
        _clear_record_if_no_inflight_op(root)
        return RebaseRunResult(
            rebase_outcome=rebase_outcome,
            merge_attempted=True,
            merge_outcome=None,
            short_circuit=(
                _failed_rebase_state(rebase_outcome, target)
                if isinstance(rebase_outcome, RebaseFailed)
                else record_conflict(
                    reason="rebase conflict followed by merge attempt exception",
                    target=target,
                    failure_cause=ResolutionFailureCause.CONTEXT_FAILURE,
                )
            ),
        )
    bridged = _bridge_merge_refusal(root, target, rebase_outcome, merge_result)
    if bridged is not None:
        return bridged
    if merge_result.outcome == "merge_commit_pending":
        return RebaseRunResult(
            rebase_outcome=rebase_outcome,
            merge_attempted=True,
            merge_outcome=merge_result,
            short_circuit=record_conflict(
                reason=merge_result.reason or "verified merge commit pending",
                target=target,
                failure_cause=merge_result.failure_cause,
            ).set_recovery_record_retained(StartupRetentionReason.FAILED_ABORT),
        )
    if merge_result.outcome in ("conflict", RESOLUTION_FAILED):
        _clear_record_if_no_inflight_op(root)
        return RebaseRunResult(
            rebase_outcome=rebase_outcome,
            merge_attempted=True,
            merge_outcome=merge_result,
            short_circuit=record_rebase_outcome(
                rebase_outcome=rebase_outcome,
                merge_attempted=True,
                merge_outcome=merge_result,
                target=target,
            ),
        )

    return RebaseRunResult(
        rebase_outcome=rebase_outcome,
        merge_attempted=True,
        merge_outcome=merge_result,
        short_circuit=None,
    )


def _abort_rebase_after_conflict(root: Path, git_ops: GitOps | None = None) -> None:
    try:
        if git_ops is not None and type(git_ops).__name__ != "_ProductionGitOps":
            git_ops.abort_rebase(root)
        elif rebase_in_progress(root):
            abort_rebase_discarding_progress(root)
    except Exception as abort_exc:
        logger.warning("auto_integrate: abort_rebase failed: {}", abort_exc)



