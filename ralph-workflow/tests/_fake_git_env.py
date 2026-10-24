"""Simulated Git and process environment for integration fault-model testing.

Provides fake implementations of GitOps, AgentRunner, and Clock protocols
with fault-injection support for timeouts, crashes, locks, remote divergence,
and conflict conditions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests._fake_git_agent_runner import FakeAgentRunner
from tests._fake_git_clock import FakeClock
from tests._fake_git_fault import FaultKind
from tests._fake_git_ops import FakeGitOps

if TYPE_CHECKING:
    from ralph.pipeline.integration_actions import AgentRunner, Clock, GitOps


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


__all__ = [
    "FakeAgentRunner",
    "FakeClock",
    "FakeGitOps",
    "FaultKind",
    "SimulatedGitEnvironment",
    "make_fake_environment",
]
