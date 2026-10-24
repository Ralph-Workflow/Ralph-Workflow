"""GitOps test implementation driven by SimulatedGitEnvironment.

Every mutation the integration pipeline performs runs through this double,
so every fault kind is consumed by the phase that models it:

* the REPLAY phase (``rebase_onto``) consumes the faults a Git command hits
  while it replays the feature onto the target;
* the LANDING phase (``fast_forward_target`` / ``land_feature``) consumes
  the faults a Git command hits while it moves the target ref;
* the OBSERVATION phase (the environment's own ``observe``) consumes the
  faults that show up while the repository is read;
* the REMOTE phase (the environment's own ``publish_remote``) consumes the
  remote and network faults.

A consumed fault always produces a TYPED outcome and the user-facing reason
registered for it in :mod:`tests._fake_git_fault`; the double never parses
message text to decide what happened.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.git.rebase.rebase import RebaseFailed, RebaseResult, RebaseSuccess
from ralph.git.rebase.rebase_conflicts import RebaseConflicts
from ralph.git.rebase.rebase_kinds import classify_rebase_error
from tests._fake_git_fault import FaultKind, classification_for

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.pipeline._auto_integrate_reclaim import ReclaimResult
    from tests._fake_git_env import SimulatedGitEnvironment


class _SimulatedCrashError(RuntimeError):
    """Raised when the simulated environment models Ralph dying mid-step.

    A crash is the ONE outcome the pipeline cannot absorb inside a step:
    the step dies, the work that was already committed stays committed, and
    the next observation is what has to make progress.
    """


class FakeGitOps:
    """GitOps implementation driven by SimulatedGitEnvironment."""

    def __init__(self, env: SimulatedGitEnvironment) -> None:
        self.env = env

    def rebase_onto(self, root: Path, target: str) -> RebaseResult:
        self.env.calls.append("rebase_onto")
        self.env.observe()
        fault = self.env.take_fault("rebase", match_any=True)
        if fault is not None:
            classification = classification_for(fault)
            if classification.effect == "crash_before":
                self._crash(classification.reason)
            if fault is FaultKind.REAL_CONFLICT:
                # The ONE fault an agent can change: the rebase stopped on a
                # real content conflict.
                return RebaseConflicts(files=["shared.txt"])
            if classification.effect in ("refuse", "fail"):
                return RebaseFailed(kind=classify_rebase_error(classification.reason, ""))

        # Successful rebase
        self.env.head_sha = f"{self.env.feature_sha}-rebased-onto-{self.env.target_sha}"
        self.env.feature_sha = self.env.head_sha
        if fault is not None and classification_for(fault).effect == "crash_after":
            self._crash(classification_for(fault).reason)
        return RebaseSuccess()

    def abort_rebase(self, root: Path) -> None:
        self.env.calls.append("abort_rebase")
        fault = self.env.take_fault("rebase")
        if fault is not None and classification_for(fault).effect == "crash_before":
            self._crash(classification_for(fault).reason)

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
        self.env.observe()
        fault = self.env.take_fault("landing", match_any=True)
        if fault is not None:
            classification = classification_for(fault)
            if classification.effect == "crash_before":
                self._crash(classification.reason)
            if classification.effect == "concurrent_advance":
                # Another worktree landed first: the target moved under us.
                self.env.target_sha = f"{self.env.target_sha}-concurrent"
                return False, classification.reason
            if classification.effect in ("refuse", "fail"):
                return False, classification.reason
        if self.env.agent_active:
            # No worktree mutation while an agent runs in that worktree.
            return False, classification_for(FaultKind.AGENT_ACTIVE).reason

        self.env.target_sha = feature_sha
        # The simulated remote tier: the local landing publishes to
        # ``<remote>/<target>`` unless the remote refuses the push.
        self.env.publish_remote()
        if fault is not None and classification_for(fault).effect == "crash_after":
            self._crash(classification_for(fault).reason)
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

    def _crash(self, reason: str) -> None:
        """Model Ralph dying at this step, leaving a stale lock behind."""
        self.env.leave_stale_lock()
        raise _SimulatedCrashError(reason)
