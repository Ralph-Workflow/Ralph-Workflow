"""Simulated Git, remote and process environment for integration fault testing.

Provides fake implementations of GitOps, AgentRunner, and Clock protocols
with fault-injection support for timeouts, crashes, locks, remote divergence,
and conflict conditions.

The environment is the SIMULATED side of the adversity catalogue: it holds
the repository state (target, feature tip, HEAD, backup refs, locks), the
simulated remote (``<remote>/<target>``), and the fault controller. Every
fault kind is consumed by one phase of the environment, and every consumed
fault records a typed outcome plus the user-facing reason registered for it
in :mod:`tests._fake_git_fault`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests._fake_git_agent_runner import FakeAgentRunner
from tests._fake_git_clock import FakeClock
from tests._fake_git_fault import (
    FaultKind,
    classification_for,
    registered_outcome,
)
from tests._fake_git_ops import FakeGitOps

if TYPE_CHECKING:
    from ralph.pipeline.integration_actions import AgentRunner, Clock, GitOps
    from tests._fake_git_fault import _FaultOutcome

#: The owner state of a lock in the simulated repository. A ``stale`` lock
#: was left behind by a dead process (a killed Ralph child) and is
#: recoverable; a ``live`` lock has a live foreign holder and is NEVER
#: removed -- the mutation that needs it is refused with a reason instead.
STALE_OWNER = "stale"
LIVE_OWNER = "live"

#: The faults that move the ELAPSED time without moving the monotonic
#: clock the deadlines are read from: the wall clock jumps, or the host
#: is suspended and resumed.
_ELAPSED_TIME_JUMPS: tuple[FaultKind, ...] = (
    FaultKind.WALL_CLOCK_JUMP,
    FaultKind.SYSTEM_SUSPEND_RESUME,
)

#: The catalogue SUB-ROWS the simulated environment can now inject, mapped
#: to the fault kind that names each of them. The key is the catalogue
#: text of the sub-row, so the coverage of the failure-mode catalogue is
#: reviewable against the request without reading the tests.
SIMULATED_CATALOGUE_ROWS: dict[str, FaultKind] = {
    "A.3 empty or corrupt loose object": FaultKind.CORRUPT_LOOSE_OBJECT,
    "A.3 half-written rebase-merge/, MERGE_HEAD and ORIG_HEAD": (
        FaultKind.HALF_WRITTEN_OPERATION_STATE
    ),
    "A.5 fsmonitor hung": FaultKind.FSMONITOR_HUNG,
    "B.1 quota exceeded": FaultKind.QUOTA_EXCEEDED,
    "B.1 slow disk": FaultKind.SLOW_DISK,
    "B.2 system suspend/resume": FaultKind.SYSTEM_SUSPEND_RESUME,
    "B.3 SIGINT or SIGTERM arriving inside a critical section": (
        FaultKind.SIGNAL_IN_CRITICAL_SECTION
    ),
    "B.4 worktree moved or prunable": FaultKind.WORKTREE_MOVED_OR_PRUNABLE,
    "B.4 sparse checkout": FaultKind.SPARSE_CHECKOUT,
    "B.4 submodules": FaultKind.SUBMODULES_PRESENT,
    "B.4 case-insensitive filesystem": FaultKind.CASE_INSENSITIVE_FILESYSTEM,
    "C.1 connection reset": FaultKind.REMOTE_CONNECTION_RESET,
    "C.1 401/403": FaultKind.REMOTE_FORBIDDEN,
    "C.1 slow but alive fetch": FaultKind.REMOTE_SLOW_FETCH,
    "C.1 LFS failure": FaultKind.REMOTE_LFS_FAILURE,
    "C.2 push rejected: protected branch": FaultKind.REMOTE_PUSH_PROTECTED_BRANCH,
    "C.2 push rejected: server-side hook": FaultKind.REMOTE_PUSH_HOOK_REJECTED,
    "C.2 push rejected: size limit": FaultKind.REMOTE_PUSH_SIZE_LIMIT,
    "C.2 remote branch deleted": FaultKind.REMOTE_BRANCH_DELETED,
    "C.2 remote renamed or repointed": FaultKind.REMOTE_REPOINTED,
    "C.2 remote repository replaced": FaultKind.REMOTE_REPLACED,
    "C.3 remote reachable for push but not for fetch": FaultKind.REMOTE_PUSH_ONLY,
    "D.1 the other worktree is dirty": FaultKind.OTHER_WORKTREE_DIRTY,
    "D.1 the other worktree is mid-operation": FaultKind.OTHER_WORKTREE_MID_OPERATION,
    "D.2 branch unborn": FaultKind.BRANCH_UNBORN,
    "D.2 branch deleted": FaultKind.BRANCH_DELETED,
    "D.2 HEAD pointing at a ref that does not exist": FaultKind.HEAD_REF_MISSING,
    "D.2 feature branch renamed": FaultKind.FEATURE_BRANCH_RENAMED,
    "D.3 record locked by a dead process": FaultKind.RECORD_LOCKED_BY_DEAD_PROCESS,
    "D.3 record on a different filesystem than the worktree": FaultKind.RECORD_OTHER_FILESYSTEM,
    "E.1 agent output touches paths outside the conflict": FaultKind.AGENT_OUTSIDE_CONFLICT_PATHS,
}


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
        remote_enabled: bool = False,
    ) -> None:
        self.target = target
        self.feature_branch = feature_branch
        self.target_sha = target_sha
        self.feature_sha = feature_sha
        self.remote_sha = remote_sha
        self.remote_enabled = remote_enabled
        self.head_branch: str | None = feature_branch
        self.head_sha = feature_sha

        self.backup_refs: dict[str, str] = {}
        self.locks: dict[str, str] = {}
        self.agent_active = False

        self.calls: list[str] = []
        self.dispatches: list[str] = []
        self.active_faults: list[FaultKind] = []
        self.outcomes: list[_FaultOutcome] = []
        self.clock = FakeClock()
        self.last_remote_reason: str | None = None
        self.last_shutdown_reason: str | None = None

    def set_faults(self, faults: list[FaultKind]) -> None:
        """Replace the active faults with ``faults``."""
        self.active_faults = list(faults)

    def pop_fault(self, kind: FaultKind) -> bool:
        """Remove ``kind`` from the active faults; report whether it was active."""
        if kind in self.active_faults:
            self.active_faults.remove(kind)
            return True
        return False

    def take_fault(self, phase: str, *, match_any: bool = False) -> FaultKind | None:
        """Consume the first active fault of ``phase`` and record its outcome.

        Args:
            phase: The phase that is asking for a fault.
            match_any: Whether a fault registered for the ``any`` phase may
                be consumed too. The step mutations pass ``True`` so a
                crash-before/after-every-step fault lands on a real step;
                the observation passes ``False`` so a crash never hides
                inside an observation.

        Returns:
            The consumed fault kind, or ``None`` when no fault of that phase
            is active.
        """
        for kind in list(self.active_faults):
            classification = classification_for(kind)
            if classification.phase == phase or (match_any and classification.phase == "any"):
                self.active_faults.remove(kind)
                self.record_outcome(kind)
                return kind
        return None

    def record_outcome(self, kind: FaultKind) -> _FaultOutcome:
        """Record the typed outcome registered for ``kind`` and return it."""
        outcome = registered_outcome(kind)
        self.outcomes.append(outcome)
        return outcome

    def fired(self, kind: FaultKind) -> bool:
        """Report whether ``kind`` was consumed by the simulated environment."""
        return self.outcome_for(kind) is not None

    def outcome_kind_for(self, kind: FaultKind) -> str | None:
        """Return the typed outcome recorded for ``kind``, if it fired."""
        outcome = self.outcome_for(kind)
        return outcome.outcome if outcome is not None else None

    def outcome_for(self, kind: FaultKind) -> _FaultOutcome | None:
        """Return the recorded outcome of ``kind``, or ``None`` when it never fired."""
        for outcome in self.outcomes:
            if outcome.kind is kind:
                return outcome
        return None

    def reason_for(self, kind: FaultKind) -> str | None:
        """Return the user-facing reason recorded for ``kind``, if it fired."""
        outcome = self.outcome_for(kind)
        return outcome.reason if outcome is not None else None

    def observe(self) -> None:
        """Run one observation of the simulated repository.

        The observation consumes an observation-phase fault and then
        recovers every lock whose owner is dead. A lock whose owner is
        still alive is never removed -- the mutation that needs it is
        refused with a reason instead. This is the property criterion 8(c)
        asks for: a stale lock left by a killed Ralph child must not block
        the next observation.
        """
        fault = self.take_fault("observation")
        if fault is not None:
            classification = classification_for(fault)
            if classification.effect == "recover":
                self._apply_recovery(fault)
            if classification.effect == "defer" and fault in _ELAPSED_TIME_JUMPS:
                # The wall clock jumps, or the host is suspended and
                # resumed; the monotonic clock the deadlines are read from
                # does not. The simulated clock moves so the jump is
                # visible, without any real waiting.
                self.clock.sleep(3600.0)
        self.recover_stale_locks()

    def recover_stale_locks(self) -> list[str]:
        """Remove every lock whose owner is dead; return the removed names.

        A lock with a live foreign holder is left untouched: the policy is
        to distinguish by evidence, never by age alone.
        """
        recovered = [name for name, owner in self.locks.items() if owner == STALE_OWNER]
        for name in recovered:
            del self.locks[name]
        return recovered

    def leave_stale_lock(self, name: str = "index.lock") -> None:
        """Model a killed Ralph child that left ``name`` behind."""
        self.locks[name] = STALE_OWNER

    def hold_live_lock(self, name: str = "index.lock") -> None:
        """Model a live foreign process that holds ``name``."""
        self.locks[name] = LIVE_OWNER

    def publish_remote(self) -> str | None:
        """Publish the local target to the simulated remote.

        Returns:
            The reason the remote did not take the push, or ``None`` when
            the remote now matches the local target.
        """
        if not self.remote_enabled:
            return None
        fault = self.take_fault("remote")
        if fault is not None:
            reason = classification_for(fault).reason
            self.last_remote_reason = reason
            return reason
        self.remote_sha = self.target_sha
        self.last_remote_reason = None
        return None

    def shutdown(self) -> str | None:
        """Model the shutdown of a run; return the reason it did not complete."""
        fault = self.take_fault("shutdown")
        if fault is None:
            return None
        reason = classification_for(fault).reason
        self.last_shutdown_reason = reason
        return reason

    def is_converged(self) -> bool:
        """True when the feature is contained in the target and the remote matches.

        With a simulated remote configured, convergence means the local
        target AND ``<remote>/<target>`` hold the same commit -- the
        convergence half of the postcondition, not just the local half.
        """
        feature_landed = self._contains(self.target_sha, self.feature_sha)
        if self.remote_enabled or self.remote_sha is not None:
            return feature_landed and self.remote_sha == self.target_sha
        return feature_landed

    def has_work_loss(self) -> bool:
        """True if the feature tip is in no ref at all.

        The tip is lost when it is detached from HEAD, from the target and
        from every backup ref: nothing points at the work any more.
        """
        preserved = (
            self._contains(self.head_sha, self.feature_sha)
            or self._contains(self.target_sha, self.feature_sha)
            or any(self._contains(sha, self.feature_sha) for sha in self.backup_refs.values())
        )
        return not preserved

    def _apply_recovery(self, kind: FaultKind) -> None:
        """Apply the recovery a fault kind models."""
        if kind is FaultKind.STALE_LOCK:
            # A killed Ralph child left a lock behind: the observation
            # finds it, proves the owner is dead and removes it.
            self.leave_stale_lock()
        elif kind is FaultKind.REF_LOCK_LEFT_BEHIND:
            # The same for a ref lock: the observation proves the owner
            # is dead and removes it.
            self.leave_stale_lock("packed-refs.lock")
        elif kind is FaultKind.HEAD_DETACHED:
            # Ralph's own move of HEAD is never tampering: the observation
            # reattaches HEAD to the feature branch.
            self.head_branch = self.feature_branch

    @staticmethod
    def _contains(container: str, tip: str) -> bool:
        """True when ``container`` holds ``tip`` (equal, or built on top of it)."""
        return container == tip or container.startswith(f"{tip}-")


def make_fake_environment(
    *,
    target: str = "main",
    feature_branch: str = "feature",
    remote_sha: str | None = None,
    remote_enabled: bool = False,
) -> tuple[SimulatedGitEnvironment, GitOps, AgentRunner, Clock]:
    """Helper to construct the environment and matching protocol implementations."""
    env = SimulatedGitEnvironment(
        target=target,
        feature_branch=feature_branch,
        remote_sha=remote_sha,
        remote_enabled=remote_enabled,
    )
    git_ops: GitOps = FakeGitOps(env)
    agent_runner: AgentRunner = FakeAgentRunner(env)
    clock: Clock = env.clock
    return env, git_ops, agent_runner, clock


__all__ = [
    "LIVE_OWNER",
    "SIMULATED_CATALOGUE_ROWS",
    "STALE_OWNER",
    "FakeAgentRunner",
    "FakeClock",
    "FakeGitOps",
    "FaultKind",
    "SimulatedGitEnvironment",
    "make_fake_environment",
]
