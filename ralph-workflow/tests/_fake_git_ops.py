"""GitOps test implementation driven by SimulatedGitEnvironment."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.git.rebase.rebase import RebaseFailed, RebaseResult, RebaseSuccess
from ralph.git.rebase.rebase_conflicts import RebaseConflicts
from ralph.git.rebase.rebase_kinds import classify_rebase_error
from tests._fake_git_fault import FaultKind

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.pipeline._auto_integrate_reclaim import ReclaimResult
    from tests._fake_git_env import SimulatedGitEnvironment


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
