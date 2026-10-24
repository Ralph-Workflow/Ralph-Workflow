"""Simulated Git and process environment for integration fault-model testing.

Provides fake implementations of GitOps, AgentRunner, and Clock protocols
with fault-injection support for timeouts, crashes, locks, remote divergence,
and conflict conditions.
"""

from __future__ import annotations

from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.git.rebase.rebase import RebaseFailed, RebaseResult, RebaseSuccess
from ralph.git.rebase.rebase_conflicts import RebaseConflicts
from ralph.git.rebase.rebase_kinds import classify_rebase_error

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.pipeline._auto_integrate_reclaim import ReclaimResult
    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStopResolver
    from ralph.pipeline.conflict_resolution.session import ResolutionSession
    from ralph.pipeline.integration_actions import AgentRunner, Clock, GitOps


class FaultKind(Enum):
    """Named failure modes from the integration adversity catalogue."""

    NONE = auto()
    GIT_TIMEOUT = auto()
    LOCK_CONTENTION = auto()
    CRASH_BEFORE_STEP = auto()
    CRASH_AFTER_STEP = auto()
    REMOTE_OUTAGE = auto()
    CONCURRENT_TARGET_ADVANCE = auto()
    REAL_CONFLICT = auto()
    STALE_LOCK = auto()


class FakeClock:
    """Monotonic fake clock for deterministic, fast-forwarded timing."""

    def __init__(self, initial_time: float = 1000.0) -> None:
        self._time = initial_time

    def now(self) -> float:
        return self._time

    def sleep(self, seconds: float) -> None:
        self._time += max(0.0, seconds)


class SimulatedGitEnvironment:
    """In-memory state and fault-injection controller for integration."""

    def __init__(
        self,
        *,
        target: str = "main",
        feature_branch: str = "feature",
        target_sha: str = "target-v1",
        feature_sha: str = "feature-v1",
        remote_sha: str | None = None,
    ) -> None:
        self.target = target
        self.feature_branch = feature_branch
        self.target_sha = target_sha
        self.feature_sha = feature_sha
        self.remote_sha = remote_sha
        self.head_branch = feature_branch
        self.head_sha = feature_sha

        self.backup_refs: dict[str, str] = {}
        self.locks: set[str] = set()
        self.agent_active = False

        self.calls: list[str] = []
        self.dispatches: list[str] = []
        self.active_faults: list[FaultKind] = []
        self.clock = FakeClock()

    def set_faults(self, faults: list[FaultKind]) -> None:
        self.active_faults = list(faults)

    def pop_fault(self, kind: FaultKind) -> bool:
        if kind in self.active_faults:
            self.active_faults.remove(kind)
            return True
        return False

    def is_converged(self) -> bool:
        """True when the feature is contained in the target and remote is in sync."""
        feature_landed = self.feature_sha in self.target_sha or self.target_sha == self.feature_sha
        if self.remote_sha is not None:
            return feature_landed and self.remote_sha == self.target_sha
        return feature_landed

    def has_work_loss(self) -> bool:
        """True if the feature commit was discarded and has no backup ref."""
        in_target = self.feature_sha in self.target_sha or self.target_sha == self.feature_sha
        in_head = self.feature_sha == self.head_sha
        in_backups = any(self.feature_sha in v for v in self.backup_refs.values())
        return not (in_target or in_head or in_backups)


class FakeGitOps:
    """GitOps implementation driven by SimulatedGitEnvironment."""

    def __init__(self, env: SimulatedGitEnvironment) -> None:
        self.env = env

    def rebase_onto(self, root: Path, target: str) -> RebaseResult:
        self.env.calls.append("rebase_onto")
        if self.env.pop_fault(FaultKind.GIT_TIMEOUT):
            return RebaseFailed(kind=classify_rebase_error("git rebase timed out", ""))
        if self.env.pop_fault(FaultKind.LOCK_CONTENTION):
            return RebaseFailed(
                kind=classify_rebase_error("Unable to create .git/index.lock: File exists", "")
            )
        if self.env.pop_fault(FaultKind.CRASH_BEFORE_STEP):
            raise RuntimeError("Crashed before rebase_onto")
        if self.env.pop_fault(FaultKind.REAL_CONFLICT):
            return RebaseConflicts(files=["shared.txt"])

        # Successful rebase
        self.env.head_sha = f"{self.env.feature_sha}-rebased-onto-{self.env.target_sha}"
        self.env.feature_sha = self.env.head_sha
        if self.env.pop_fault(FaultKind.CRASH_AFTER_STEP):
            raise RuntimeError("Crashed after rebase_onto")
        return RebaseSuccess()

    def abort_rebase(self, root: Path) -> None:
        self.env.calls.append("abort_rebase")
        if self.env.pop_fault(FaultKind.CRASH_BEFORE_STEP):
            raise RuntimeError("Crashed before abort_rebase")

    def continue_rebase(self, root: Path) -> None:
        self.env.calls.append("continue_rebase")

    def abort_merge(self, root: Path) -> bool:
        self.env.calls.append("abort_merge")
        return True

    def continue_merge(self, root: Path) -> bool:
        self.env.calls.append("continue_merge")
        return True

    def abort_cherry_pick(self, root: Path) -> bool:
        self.env.calls.append("abort_cherry_pick")
        return True

    def continue_cherry_pick(self, root: Path) -> bool:
        self.env.calls.append("continue_cherry_pick")
        return True

    def stage_all(self, root: Path) -> bool:
        self.env.calls.append("stage_all")
        return True

    def commit(
        self,
        root: Path,
        *,
        on_failure: Callable[[str], None] | None = None,
    ) -> bool:
        self.env.calls.append("commit")
        return True

    def fast_forward_target(
        self,
        root: Path,
        target: str,
        feature_sha: str,
        *,
        reclaim_target_worktree: bool = True,
        on_reclaimed: Callable[[ReclaimResult], None] | None = None,
    ) -> tuple[bool, str]:
        self.env.calls.append("fast_forward_target")
        if self.env.agent_active:
            return False, "refused: agent is currently active in worktree"
        if self.env.pop_fault(FaultKind.CONCURRENT_TARGET_ADVANCE):
            self.env.target_sha = f"{self.env.target_sha}-concurrent"
            return False, "fast-forward rejected: target advanced concurrently"
        if self.env.pop_fault(FaultKind.LOCK_CONTENTION):
            return False, "cannot lock target ref: lock held"
        if self.env.pop_fault(FaultKind.GIT_TIMEOUT):
            return False, "fast-forward command timed out after 120.0s"
        if self.env.pop_fault(FaultKind.CRASH_BEFORE_STEP):
            raise RuntimeError("Crashed before fast_forward_target")

        self.env.target_sha = feature_sha
        if self.env.pop_fault(FaultKind.CRASH_AFTER_STEP):
            raise RuntimeError("Crashed after fast_forward_target")
        return True, ""

    def land_feature(
        self,
        root: Path,
        target: str,
        feature_sha: str,
        *,
        reclaim_target_worktree: bool = True,
        on_reclaimed: Callable[[ReclaimResult], None] | None = None,
    ) -> tuple[bool, str]:
        self.env.calls.append("land_feature")
        return self.fast_forward_target(
            root,
            target,
            feature_sha,
            reclaim_target_worktree=reclaim_target_worktree,
            on_reclaimed=on_reclaimed,
        )

    def create_backup_ref(
        self,
        root: Path,
        pre_feature_sha: str | None,
        owning_worktree: str | None = None,
    ) -> str | None:
        self.env.calls.append("create_backup_ref")
        sha = pre_feature_sha or self.env.head_sha
        ref_name = f"refs/ralph-backup/{self.env.feature_branch}-{sha[:8]}"
        self.env.backup_refs[ref_name] = sha
        return ref_name

    def delete_backup_ref(self, root: Path, backup_ref: str | None) -> None:
        self.env.calls.append("delete_backup_ref")
        if backup_ref and backup_ref in self.env.backup_refs:
            del self.env.backup_refs[backup_ref]

    def reattach_head(self, root: Path, branch: str) -> bool:
        self.env.calls.append("reattach_head")
        self.env.head_branch = branch
        return True


class FakeAgentRunner:
    """AgentRunner implementation driven by SimulatedGitEnvironment."""

    def __init__(self, env: SimulatedGitEnvironment, *, can_resolve: bool = True) -> None:
        self.env = env
        self.can_resolve = can_resolve

    def resolve_rebase(
        self,
        root: Path,
        target: str,
        resolver: RebaseStopResolver,
        *,
        session: ResolutionSession,
    ) -> bool:
        self.env.dispatches.append("resolve_rebase")
        if not self.can_resolve:
            return False
        # Resolving in place produces a resolved feature tip
        self.env.feature_sha = f"{self.env.feature_sha}-resolved"
        self.env.head_sha = self.env.feature_sha
        return True


def make_fake_environment(
    *,
    target: str = "main",
    feature_branch: str = "feature",
    remote_sha: str | None = None,
) -> tuple[SimulatedGitEnvironment, GitOps, AgentRunner, Clock]:
    """Helper to construct the environment and matching protocol implementations."""
    env = SimulatedGitEnvironment(
        target=target,
        feature_branch=feature_branch,
        remote_sha=remote_sha,
    )
    git_ops: GitOps = FakeGitOps(env)
    agent_runner: AgentRunner = FakeAgentRunner(env)
    clock: Clock = env.clock
    return env, git_ops, agent_runner, clock
