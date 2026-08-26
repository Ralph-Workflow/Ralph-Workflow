"""Fail-closed integration-resolution invariant for phase dispatch.

A normal pipeline phase may run only after this module proves that neither
persisted integration state nor the live repository indicates an unfinished
rebase or merge.  The conflict resolver itself is deliberately out of graph;
it is the sole recovery executor named by a blocking verdict.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from ralph.git.hardening import COMMIT_PIN_CONFIG_ARGS
from ralph.git.merge import MERGE_STATE_NONE, merge_state
from ralph.git.rebase.rebase import rebase_in_progress
from ralph.git.subprocess_runner import run_git

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.pipeline.rebase_state import RebaseState

RESOLUTION_DRAIN = "rebase_conflict_resolution"


class IntegrationResolutionStatus(StrEnum):
    """Dispatch-safe classification of integration state."""

    RESOLVED = "resolved"
    RECOVERABLE = "recoverable"
    EXHAUSTED = "exhausted"


@dataclass(frozen=True)
class IntegrationResolutionVerdict:
    """Evidence-backed answer to whether an ordinary phase may dispatch."""

    status: IntegrationResolutionStatus
    reasons: tuple[str, ...] = ()
    recovery_executor: str | None = None

    @property
    def dispatch_allowed(self) -> bool:
        """Whether a non-resolution phase is safe to dispatch."""
        return self.status is IntegrationResolutionStatus.RESOLVED


class IntegrationResolutionBlockedError(RuntimeError):
    """Raised when an ordinary phase attempts to bypass the invariant."""


def inspect_integration_resolution(
    root: Path,
    state: RebaseState,
    *,
    porcelain: Callable[[Path], tuple[bool, str]] | None = None,
    rebase_active: Callable[[Path], bool] = rebase_in_progress,
    merge_status: Callable[[Path], str] = merge_state,
) -> IntegrationResolutionVerdict:
    """Return the fail-closed dispatch verdict for ``root`` and ``state``.

    Full porcelain output intentionally includes untracked paths: every file
    is evidence that the worktree observed by a subsequently dispatched agent
    differs from the integration result. Any failed inspection is unsafe.
    """
    # Non-repository orchestration contexts (unit seams and initial project
    # setup) have no live Git integration state to inspect. The production
    # dispatch path is a repository, while this preserves existing synthetic
    # runner seams without manufacturing a Git failure.
    if porcelain is None and not (root / ".git").exists():
        return IntegrationResolutionVerdict(IntegrationResolutionStatus.RESOLVED)

    if state.resolution_exhausted:
        return IntegrationResolutionVerdict(
            IntegrationResolutionStatus.EXHAUSTED,
            (state.resolution_exhaustion_reason or "conflict resolver exhausted",),
        )

    reasons: list[str] = []
    if state.integration_unresolved:
        reasons.append("persisted integration state is unresolved")
    probe = porcelain or _full_porcelain
    try:
        readable, output = probe(root)
    except Exception:
        readable, output = False, ""
    if not readable:
        reasons.append("unable to inspect full git porcelain status")
    elif output.strip():
        reasons.append("worktree is not porcelain-clean")
    try:
        if rebase_active(root):
            reasons.append("rebase is in progress")
    except Exception:
        reasons.append("unable to inspect rebase state")
    try:
        if merge_status(root) != MERGE_STATE_NONE:
            reasons.append("merge is in progress or merge state is unreadable")
    except Exception:
        reasons.append("unable to inspect merge state")
    if reasons:
        return IntegrationResolutionVerdict(
            IntegrationResolutionStatus.RECOVERABLE,
            tuple(reasons),
            RESOLUTION_DRAIN,
        )
    return IntegrationResolutionVerdict(IntegrationResolutionStatus.RESOLVED)


def assert_non_resolution_dispatch_allowed(
    phase: str,
    verdict: IntegrationResolutionVerdict,
) -> None:
    """Reject every ordinary phase when the integration invariant blocks it."""
    if phase != RESOLUTION_DRAIN and not verdict.dispatch_allowed:
        detail = "; ".join(reason for reason in verdict.reasons if isinstance(reason, str))
        detail = detail or verdict.status.value
        raise IntegrationResolutionBlockedError(
            f"cannot dispatch {phase!r}: integration resolution is {verdict.status.value}: {detail}"
        )


def _full_porcelain(root: Path) -> tuple[bool, str]:
    result = run_git(
        (*COMMIT_PIN_CONFIG_ARGS, "status", "--porcelain"),
        cwd=root,
        label="git-integration-resolution-status",
    )
    return result.returncode == 0, result.stdout
