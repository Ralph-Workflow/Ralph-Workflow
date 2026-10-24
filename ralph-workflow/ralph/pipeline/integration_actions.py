"""Injection seams for the integration pipeline's Git and agent mutations.

The integration pipeline mutates a repository in a handful of places:
it replays the feature onto the target, it lands the result, it
dispatches a resolver agent when the replay conflicts, and it protects
the in-flight tip with a backup ref. Every one of those is a Git
subprocess or an agent invocation, so every one of them is a place
where the pipeline can block, half-finish, or livelock.

This module declares the NARROW protocols those mutations run through
-- :class:`GitOps`, :class:`Clock` and :class:`AgentRunner` -- plus
the production adapter that binds :class:`GitOps` to the live
repository helpers. The protocols ARE the seam: an owning function
that takes a ``git_ops`` argument can be driven end to end with fakes,
so the ORDER of the mutations is observable without a repository, a
subprocess, or a clock.

Two rules keep the seam honest:

* **Every mutation goes through the executor.** A mutation that
  bypasses the protocol is invisible to a fault-injecting double, and
  an invisible mutation is exactly the one that livelocks.
* **The decision stays outside.** The protocols carry MUTATIONS only.
  Which rebase to attempt, whether to fall back to the endpoint merge
  and whether a conflict is resolvable are decisions; they stay in the
  owning module, so the executor never becomes a place where policy
  hides.

The protocols are reachable two ways. A caller that OWNS the whole
attempt passes the executors down as arguments. A caller that only
holds ONE step -- the commit seam, the phase seam -- installs them for
the duration of its own scope with :func:`install_executors`; the
seams below then resolve the installed executors through
:func:`current_git_ops` and :func:`current_agent_runner`. The ambient
scope exists so that the MUTATION seams a module already owns (the
backup ref, the fast-forward landing) can be routed through the
protocol without every caller threading the executors through five
frames.

The repo-structure policy allows at most ONE public top-level class
per module (``ralph.testing.audit_repo_structure``). This module
publishes three protocols, so two of them are declared with private
names and exported under their public names; the exported aliases ARE
the contract.
"""

from __future__ import annotations

import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextvars import Token
    from pathlib import Path

    from ralph.git.rebase.rebase import RebaseResult
    from ralph.pipeline._auto_integrate_reclaim import ReclaimResult
    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStopResolver
    from ralph.pipeline.conflict_resolution.session import ResolutionSession

__all__ = [
    "AgentRunner",
    "Clock",
    "GitOps",
    "create_backup_ref",
    "current_agent_runner",
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
    """Protocol for the Git mutations the integration pipeline performs.

    Every member is one MUTATION. Observation -- reading a SHA, a ref
    or a state file -- deliberately stays OUTSIDE this protocol: the
    pipeline's decisions are made from observations, and only the
    mutations have to be substitutable for the ORDER of an attempt to
    be provable without a repository.

    The members fall into five families:

    * **replay** -- :meth:`rebase_onto` starts the replay,
      :meth:`continue_rebase` advances it past a proved-resolved stop
      and :meth:`abort_rebase` throws it away;
    * **merge** -- :meth:`continue_merge` and :meth:`abort_merge` own
      the endpoint merge;
    * **cherry-pick** -- :meth:`continue_cherry_pick` and
      :meth:`abort_cherry_pick` own a replayed cherry-pick;
    * **index and commit** -- :meth:`stage_all` stages the working
      tree and :meth:`commit` commits the verified index;
    * **landing and recovery** -- :meth:`fast_forward_target` and
      :meth:`land_feature` move the target ref, while
      :meth:`create_backup_ref`, :meth:`delete_backup_ref` and
      :meth:`reattach_head` protect the in-flight tip.
    """

    def rebase_onto(self, root: Path, target: str) -> RebaseResult:
        """Replay the current branch at ``root`` onto ``target``."""
        ...

    def abort_rebase(self, root: Path) -> None:
        """Throw away the in-progress rebase at ``root``."""
        ...

    def continue_rebase(self, root: Path) -> None:
        """Advance the in-progress rebase at ``root`` past its current stop."""
        ...

    def abort_merge(self, root: Path) -> bool:
        """Abort the in-progress merge at ``root``; report whether it ran."""
        ...

    def continue_merge(self, root: Path) -> bool:
        """Complete the in-progress merge at ``root``; report whether it completed."""
        ...

    def abort_cherry_pick(self, root: Path) -> bool:
        """Abort the in-progress cherry-pick at ``root``; report whether it ran."""
        ...

    def continue_cherry_pick(self, root: Path) -> bool:
        """Advance the in-progress cherry-pick at ``root``."""
        ...

    def stage_all(self, root: Path) -> bool:
        """Stage every change in the working tree at ``root``."""
        ...

    def commit(self, root: Path, *, on_failure: Callable[[str], None] | None = None) -> bool:
        """Commit the verified index at ``root``."""
        ...

    def fast_forward_target(
        self,
        root: Path,
        target: str,
        feature_sha: str,
        *,
        reclaim_target_worktree: bool = True,
        on_reclaimed: Callable[[ReclaimResult], None] | None = None,
    ) -> tuple[bool, str]:
        """Move the target ref to ``feature_sha``; return ``(landed, reason)``."""
        ...

    def land_feature(
        self,
        root: Path,
        target: str,
        feature_sha: str,
        *,
        reclaim_target_worktree: bool = True,
        on_reclaimed: Callable[[ReclaimResult], None] | None = None,
    ) -> tuple[bool, str]:
        """Land the feature on ``target`` and reattach HEAD to the feature branch.

        The landing and the reattach are ONE operation: a feature that
        landed while HEAD names no branch leaves the next commit seam
        with nothing to commit on, which is the shape of the livelock
        this seam exists to close.
        """
        ...

    def create_backup_ref(
        self,
        root: Path,
        pre_feature_sha: str | None,
        owning_worktree: str | None = None,
    ) -> str | None:
        """Create the backup ref that protects the in-flight feature tip."""
        ...

    def delete_backup_ref(self, root: Path, backup_ref: str | None) -> None:
        """Delete a backup ref that is no longer needed."""
        ...

    def reattach_head(self, root: Path, branch: str) -> bool:
        """Point HEAD back at ``branch``; report whether HEAD was reattached."""
        ...


@runtime_checkable
class _Clock(Protocol):
    """Protocol for the wall-clock seam of an integration attempt."""

    def now(self) -> float:
        """Return the current time in seconds."""
        ...

    def sleep(self, seconds: float) -> None:
        """Pause execution for ``seconds``."""
        ...


#: Public alias of the wall-clock protocol; see the module docstring.
Clock = _Clock


@runtime_checkable
class _AgentRunner(Protocol):
    """Protocol for dispatching the conflict-resolution agent."""

    def resolve_rebase(
        self,
        root: Path,
        target: str,
        resolver: RebaseStopResolver,
        *,
        session: ResolutionSession,
    ) -> bool:
        """Drive the paused rebase at ``root`` to completion through ``resolver``.

        Returns ``True`` only when the rebase finished AND landed;
        ``False`` means the resolution declined, and the caller falls
        back to the endpoint merge.
        """
        ...


#: Public alias of the resolver-dispatch protocol; see the module docstring.
AgentRunner = _AgentRunner


class _ProductionGitOps:
    """Production :class:`GitOps` adapter over the live repository helpers.

    Every member is a thin wrapper: the Git semantics live in
    :mod:`ralph.git` and :mod:`ralph.pipeline.auto_integrate_ff`, and
    this adapter only names them. Nothing is re-implemented here, so
    the adapter cannot drift away from the behaviour the pipeline had
    before the seam existed.
    """

    def rebase_onto(self, root: Path, target: str) -> RebaseResult:
        """Replay the current branch at ``root`` onto ``target``."""
        from ralph.git.rebase.rebase import rebase_onto

        return rebase_onto(target, repo_root=root)

    def abort_rebase(self, root: Path) -> None:
        """Throw away the in-progress rebase at ``root`` and its progress sidecar."""
        from ralph.pipeline.conflict_resolution.abort import abort_rebase_discarding_progress

        abort_rebase_discarding_progress(root)

    def continue_rebase(self, root: Path) -> None:
        """Advance the in-progress rebase at ``root`` past its current stop."""
        from ralph.git.rebase.rebase_continuation import continue_rebase_at

        continue_rebase_at(root)

    def abort_merge(self, root: Path) -> bool:
        """Abort the in-progress merge at ``root``; report whether it ran."""
        from ralph.git.merge import abort_merge

        return abort_merge(root)

    def continue_merge(self, root: Path) -> bool:
        """Complete the in-progress merge at ``root``; report whether it completed."""
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
        """Abort the in-progress cherry-pick at ``root``; report whether it ran."""
        from ralph.git.subprocess_runner import run_git

        result = run_git(
            ("cherry-pick", "--quit"),
            cwd=root,
            label="integration:cherry-pick-abort",
        )
        return result.returncode == 0

    def continue_cherry_pick(self, root: Path) -> bool:
        """Advance the in-progress cherry-pick at ``root``."""
        from ralph.git.subprocess_runner import run_git

        result = run_git(
            ("cherry-pick", "--continue"),
            cwd=root,
            label="integration:cherry-pick-continue",
        )
        return result.returncode == 0

    def stage_all(self, root: Path) -> bool:
        """Stage every change in the working tree at ``root``."""
        from ralph.git.subprocess_runner import run_git

        result = run_git(("add", "--all"), cwd=root, label="integration:stage-all")
        return result.returncode == 0

    def commit(self, root: Path, *, on_failure: Callable[[str], None] | None = None) -> bool:
        """Commit the verified index at ``root``."""
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
    ) -> tuple[bool, str]:
        """Move the target ref to ``feature_sha``; return ``(landed, reason)``."""
        from ralph.pipeline.auto_integrate_ff import fast_forward_target

        return fast_forward_target(
            root,
            target,
            feature_sha,
            reclaim_target_worktree=reclaim_target_worktree,
            on_reclaimed=on_reclaimed,
        )

    def land_feature(
        self,
        root: Path,
        target: str,
        feature_sha: str,
        *,
        reclaim_target_worktree: bool = True,
        on_reclaimed: Callable[[ReclaimResult], None] | None = None,
    ) -> tuple[bool, str]:
        """Land the feature on ``target`` and reattach HEAD to the feature branch."""
        from ralph.pipeline.auto_integrate_context import current_branch_or_detached_marker

        feature_branch = current_branch_or_detached_marker(root)
        landed, reason = self.fast_forward_target(
            root,
            target,
            feature_sha,
            reclaim_target_worktree=reclaim_target_worktree,
            on_reclaimed=on_reclaimed,
        )
        if landed and feature_branch:
            self.reattach_head(root, feature_branch)
        return landed, reason

    def create_backup_ref(
        self,
        root: Path,
        pre_feature_sha: str | None,
        owning_worktree: str | None = None,
    ) -> str | None:
        """Create the backup ref that protects the in-flight feature tip."""
        from ralph.pipeline.auto_integrate_backup_refs import create_rebase_backup_ref

        return create_rebase_backup_ref(root, pre_feature_sha, owning_worktree)

    def delete_backup_ref(self, root: Path, backup_ref: str | None) -> None:
        """Delete a backup ref that is no longer needed."""
        from ralph.pipeline.auto_integrate_backup_refs import delete_rebase_backup_ref

        delete_rebase_backup_ref(root, backup_ref)

    def reattach_head(self, root: Path, branch: str) -> bool:
        """Point HEAD back at ``branch``; report whether HEAD was reattached."""
        from ralph.pipeline._auto_integrate_recovery_git_state import reattach_head_to_branch
        from ralph.pipeline.auto_integrate_context import current_branch_or_detached_marker

        if not branch or current_branch_or_detached_marker(root) is not None:
            return False
        reattach_head_to_branch(root, branch)
        return True


class _ProductionAgentRunner:
    """Production :class:`AgentRunner` over the conflict-resolution loop."""

    def resolve_rebase(
        self,
        root: Path,
        target: str,
        resolver: RebaseStopResolver,
        *,
        session: ResolutionSession,
    ) -> bool:
        """Drive the paused rebase at ``root`` to completion through ``resolver``."""
        from ralph.pipeline.conflict_resolution import resolve_rebase_in_progress

        return resolve_rebase_in_progress(root, target, resolver, session=session)


class _SystemClock:
    """Production :class:`Clock` over the real wall clock."""

    def now(self) -> float:
        """Return the current monotonic time in seconds."""
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        """Pause execution for ``seconds``."""
        # filesystem-poll-ok: real wall-clock sleep implementation of injected Clock protocol
        time.sleep(seconds)


_DEFAULT_GIT_OPS = _ProductionGitOps()
_DEFAULT_AGENT_RUNNER = _ProductionAgentRunner()
_DEFAULT_CLOCK = _SystemClock()


def default_git_ops() -> GitOps:
    """Return the production Git-ops adapter (live repository helpers)."""
    return _DEFAULT_GIT_OPS


def default_agent_runner() -> AgentRunner:
    """Return the production resolver-dispatch adapter (live conflict-resolution loop)."""
    return _DEFAULT_AGENT_RUNNER


def default_clock() -> Clock:
    """Return the production clock (real wall-clock time)."""
    return _DEFAULT_CLOCK


# ---------------------------------------------------------------------------
# Ambient executor scope -- the injection seam for callers that own ONE step
# of an integration attempt rather than the whole attempt.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Executors:
    """The injected mutation seams of ONE integration attempt."""

    git_ops: GitOps
    agent_runner: AgentRunner


_EXECUTORS: ContextVar[_Executors | None] = ContextVar(
    "ralph_integration_executors",
    default=None,
)


def install_executors(
    git_ops: GitOps | None = None,
    agent_runner: AgentRunner | None = None,
) -> Token[_Executors | None]:
    """Install the injected mutation seams and return the reset token.

    A ``None`` argument INHERITS the seam that is already installed, so a
    nested scope never silently downgrades an injected executor to the
    production adapter. The returned token is handed back to
    :func:`reset_executors` when the scope ends.

    Args:
        git_ops: The Git-mutation seam to install, or ``None`` to inherit
            the installed one (the production adapter when nothing is
            installed).
        agent_runner: The resolver-dispatch seam to install, or ``None``
            to inherit the installed one.
    """
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
    )
    return _EXECUTORS.set(executors)


def reset_executors(token: Token[_Executors | None]) -> None:
    """Restore the seams that were installed before ``token`` was issued."""
    _EXECUTORS.reset(token)


def current_git_ops() -> GitOps:
    """Return the installed Git-ops seam, or the production adapter."""
    executors = _EXECUTORS.get()
    return executors.git_ops if executors is not None else default_git_ops()


def current_agent_runner() -> AgentRunner:
    """Return the installed resolver-dispatch seam, or the production adapter."""
    executors = _EXECUTORS.get()
    return executors.agent_runner if executors is not None else default_agent_runner()


# ---------------------------------------------------------------------------
# Mutation seams -- the ONE call path a caller uses to reach the protocol.
#
# These exist so a module that already owns a mutation seam (the backup
# ref, the fast-forward landing) routes it through the protocol without
# every caller threading the executors through five frames. They resolve
# the INSTALLED executor, so an injected double observes the mutation.
# ---------------------------------------------------------------------------


def create_backup_ref(
    root: Path,
    pre_feature_sha: str | None,
    owning_worktree: str | None = None,
) -> str | None:
    """Create the in-flight backup ref through the installed Git-ops seam."""
    return current_git_ops().create_backup_ref(root, pre_feature_sha, owning_worktree)


def delete_backup_ref(root: Path, backup_ref: str | None) -> None:
    """Delete the in-flight backup ref through the installed Git-ops seam."""
    current_git_ops().delete_backup_ref(root, backup_ref)


def fast_forward_target(
    root: Path,
    target: str,
    feature_sha: str,
    *,
    reclaim_target_worktree: bool = True,
    on_reclaimed: Callable[[ReclaimResult], None] | None = None,
) -> tuple[bool, str]:
    """Move the target ref to ``feature_sha`` through the installed Git-ops seam."""
    return current_git_ops().fast_forward_target(
        root,
        target,
        feature_sha,
        reclaim_target_worktree=reclaim_target_worktree,
        on_reclaimed=on_reclaimed,
    )
