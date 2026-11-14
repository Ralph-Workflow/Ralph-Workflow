from __future__ import annotations

import time
from contextvars import ContextVar
from dataclasses import dataclass
from threading import Event
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextvars import Token
    from pathlib import Path

    from ralph.git.rebase.rebase import RebaseResult
    from ralph.pipeline._auto_integrate_reclaim import ReclaimResult
    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStopResolver
    from ralph.pipeline.conflict_resolution.session import ResolutionSession
    from ralph.pipeline.resolution_outcome import StopResolutionOutcome

__all__ = [
    "AgentRunner",
    "Clock",
    "GitOps",
    "create_backup_ref",
    "current_agent_runner",
    "current_clock",
    "current_git_ops",
    "default_agent_runner",
    "default_clock",
    "default_git_ops",
    "delete_backup_ref",
    "fast_forward_target",
    "install_executors",
    "reset_executors",
]


@runtime_checkable
class GitOps(Protocol):
    def rebase_onto(self, root: Path, target: str) -> RebaseResult: ...

    def abort_rebase(self, root: Path) -> None: ...

    def continue_rebase(self, root: Path) -> None: ...

    def abort_merge(self, root: Path) -> bool: ...

    def continue_merge(self, root: Path) -> bool: ...

    def abort_cherry_pick(self, root: Path) -> bool: ...

    def continue_cherry_pick(self, root: Path) -> bool: ...

    def stage_all(self, root: Path) -> bool: ...

    def commit(self, root: Path, *, on_failure: Callable[[str], None] | None = None) -> bool: ...

    def fast_forward_target(
        self,
        root: Path,
        target: str,
        feature_sha: str,
        *,
        reclaim_target_worktree: bool = True,
        on_reclaimed: Callable[[ReclaimResult], None] | None = None,
    ) -> StopResolutionOutcome: ...

    def land_feature(
        self,
        root: Path,
        target: str,
        feature_sha: str,
        *,
        reclaim_target_worktree: bool = True,
        on_reclaimed: Callable[[ReclaimResult], None] | None = None,
    ) -> StopResolutionOutcome: ...

    def create_backup_ref(
        self,
        root: Path,
        pre_feature_sha: str | None,
        owning_worktree: str | None = None,
    ) -> str | None: ...

    def delete_backup_ref(self, root: Path, backup_ref: str | None) -> None: ...

    def reattach_head(self, root: Path, branch: str) -> bool: ...


@runtime_checkable
class _Clock(Protocol):
    def now(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


Clock = _Clock


@runtime_checkable
class _AgentRunner(Protocol):
    def resolve_rebase(
        self,
        root: Path,
        target: str,
        resolver: RebaseStopResolver,
        *,
        session: ResolutionSession | None = None,
    ) -> bool: ...


AgentRunner = _AgentRunner


class _ProductionGitOps:
    def rebase_onto(self, root: Path, target: str) -> RebaseResult:
        from ralph.git.rebase.rebase import rebase_onto

        return rebase_onto(target, repo_root=root)

    def abort_rebase(self, root: Path) -> None:
        from ralph.pipeline.conflict_resolution.abort import abort_rebase_discarding_progress

        abort_rebase_discarding_progress(root)

    def continue_rebase(self, root: Path) -> None:
        from ralph.git.rebase.rebase_continuation import continue_rebase_at

        continue_rebase_at(root)

    def abort_merge(self, root: Path) -> bool:
        from ralph.git.merge import abort_merge

        return abort_merge(root)

    def continue_merge(self, root: Path) -> bool:
        from ralph.git.hardening import COMMIT_PIN_CONFIG_ARGS
        from ralph.git.merge import MERGE_STATE_IN_PROGRESS, merge_state
        from ralph.git.subprocess_runner import run_git

        if merge_state(root) != MERGE_STATE_IN_PROGRESS:
            return False
        result = run_git(
            (*COMMIT_PIN_CONFIG_ARGS, "merge", "--continue"),
            cwd=root,
            label="integration:merge-continue",
        )
        return result.returncode == 0

    def abort_cherry_pick(self, root: Path) -> bool:
        from ralph.git.subprocess_runner import run_git

        result = run_git(
            ("cherry-pick", "--quit"),
            cwd=root,
            label="integration:cherry-pick-abort",
        )
        return result.returncode == 0

    def continue_cherry_pick(self, root: Path) -> bool:
        from ralph.git.subprocess_runner import run_git

        result = run_git(
            ("cherry-pick", "--continue"),
            cwd=root,
            label="integration:cherry-pick-continue",
        )
        return result.returncode == 0

    def stage_all(self, root: Path) -> bool:
        from ralph.git.subprocess_runner import run_git

        result = run_git(("add", "--all"), cwd=root, label="integration:stage-all")
        return result.returncode == 0

    def commit(self, root: Path, *, on_failure: Callable[[str], None] | None = None) -> bool:
        from ralph.git.merge import commit_merge_in_progress

        return commit_merge_in_progress(root, on_failure=on_failure)

    def fast_forward_target(
        self,
        root: Path,
        target: str,
        feature_sha: str,
        *,
        reclaim_target_worktree: bool = True,
        on_reclaimed: Callable[[ReclaimResult], None] | None = None,
    ) -> StopResolutionOutcome:
        from ralph.pipeline.auto_integrate_ff import fast_forward_target
        from ralph.pipeline.resolution_outcome import stop_resolution_outcome_reason

        outcome = fast_forward_target(
            root,
            target,
            feature_sha,
            reclaim_target_worktree=reclaim_target_worktree,
            on_reclaimed=on_reclaimed,
        )
        if hasattr(outcome, "succeeded") and bool(outcome.succeeded):
            return True, ""
        return False, stop_resolution_outcome_reason(outcome)

    def land_feature(
        self,
        root: Path,
        target: str,
        feature_sha: str,
        *,
        reclaim_target_worktree: bool = True,
        on_reclaimed: Callable[[ReclaimResult], None] | None = None,
    ) -> StopResolutionOutcome:
        from ralph.pipeline.auto_integrate_context import current_branch_or_detached_marker
        from ralph.pipeline.resolution_outcome import ResolvedOutcome

        feature_branch = current_branch_or_detached_marker(root)
        outcome = self.fast_forward_target(
            root,
            target,
            feature_sha,
            reclaim_target_worktree=reclaim_target_worktree,
            on_reclaimed=on_reclaimed,
        )
        landed = isinstance(outcome, ResolvedOutcome)
        if landed and feature_branch:
            self.reattach_head(root, feature_branch)
        return outcome

    def create_backup_ref(
        self,
        root: Path,
        pre_feature_sha: str | None,
        owning_worktree: str | None = None,
    ) -> str | None:
        from ralph.pipeline.auto_integrate_backup_refs import create_rebase_backup_ref

        return create_rebase_backup_ref(root, pre_feature_sha, owning_worktree)

    def delete_backup_ref(self, root: Path, backup_ref: str | None) -> None:
        from ralph.pipeline.auto_integrate_backup_refs import delete_rebase_backup_ref

        delete_rebase_backup_ref(root, backup_ref)

    def reattach_head(self, root: Path, branch: str) -> bool:
        from ralph.pipeline._auto_integrate_recovery_git_state import reattach_head_to_branch
        from ralph.pipeline.auto_integrate_context import current_branch_or_detached_marker

        if not branch or current_branch_or_detached_marker(root) is not None:
            return False
        reattach_head_to_branch(root, branch)
        return True


class _SystemClock:
    def __init__(self) -> None:
        self._wait_event = Event()

    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        self._wait_event.wait(seconds)


_DEFAULT_GIT_OPS = _ProductionGitOps()
_DEFAULT_CLOCK = _SystemClock()


def default_git_ops() -> GitOps:
    return _DEFAULT_GIT_OPS


def default_agent_runner() -> AgentRunner:
    from ralph.pipeline._stop_resolution_entry import production_rebase_runner

    return production_rebase_runner()


def default_clock() -> Clock:
    return current_clock()


@dataclass(frozen=True)
class _Executors:
    git_ops: GitOps
    agent_runner: AgentRunner
    clock: Clock


_EXECUTORS: ContextVar[_Executors | None] = ContextVar(
    "ralph_integration_executors",
    default=None,
)


def install_executors(
    git_ops: GitOps | None = None,
    agent_runner: AgentRunner | None = None,
    clock: Clock | None = None,
) -> Token[_Executors | None]:
    current = _EXECUTORS.get()
    executors = _Executors(
        git_ops=(
            git_ops
            if git_ops is not None
            else (current.git_ops if current is not None else default_git_ops())
        ),
        agent_runner=(
            agent_runner
            if agent_runner is not None
            else (current.agent_runner if current is not None else default_agent_runner())
        ),
        clock=(
            clock
            if clock is not None
            else (current.clock if current is not None else _DEFAULT_CLOCK)
        ),
    )
    return _EXECUTORS.set(executors)


def reset_executors(token: Token[_Executors | None]) -> None:
    _EXECUTORS.reset(token)


def current_git_ops() -> GitOps:
    executors = _EXECUTORS.get()
    return executors.git_ops if executors is not None else default_git_ops()


def current_agent_runner() -> AgentRunner:
    executors = _EXECUTORS.get()
    return executors.agent_runner if executors is not None else default_agent_runner()


def current_clock() -> Clock:
    executors = _EXECUTORS.get()
    return executors.clock if executors is not None else _DEFAULT_CLOCK


def create_backup_ref(
    root: Path,
    pre_feature_sha: str | None,
    owning_worktree: str | None = None,
) -> str | None:
    return current_git_ops().create_backup_ref(root, pre_feature_sha, owning_worktree)


def delete_backup_ref(root: Path, backup_ref: str | None) -> None:
    current_git_ops().delete_backup_ref(root, backup_ref)


def fast_forward_target(
    root: Path,
    target: str,
    feature_sha: str,
    *,
    reclaim_target_worktree: bool = True,
    on_reclaimed: Callable[[ReclaimResult], None] | None = None,
) -> StopResolutionOutcome:
    return current_git_ops().fast_forward_target(
        root,
        target,
        feature_sha,
        reclaim_target_worktree=reclaim_target_worktree,
        on_reclaimed=on_reclaimed,
    )
