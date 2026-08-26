"""Fail-closed integration-resolution invariant for phase dispatch.

A normal pipeline phase may run only after this module proves that neither
persisted integration state nor the live repository indicates an unfinished
rebase or merge.  The conflict resolver itself is deliberately out of graph;
it is the sole recovery executor named by a blocking verdict.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.git.hardening import COMMIT_PIN_CONFIG_ARGS
from ralph.git.merge import MERGE_STATE_NONE, merge_state
from ralph.git.rebase.rebase import rebase_in_progress
from ralph.git.subprocess_runner import run_git
from ralph.pipeline.integration_resolution_status import IntegrationResolutionStatus
from ralph.pipeline.integration_resolution_types import IntegrationResolutionVerdict

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.pipeline.rebase_state import RebaseState

RESOLUTION_DRAIN = "rebase_conflict_resolution"


# Public aliases preserve the original concise predicate vocabulary while the
# enum gives direct callers a typed, stable decision contract.
RESOLVED = IntegrationResolutionStatus.RESOLVED
RECOVERABLE = IntegrationResolutionStatus.RECOVERABLE
EXHAUSTED = IntegrationResolutionStatus.EXHAUSTED


def inspect_integration_resolution(
    root: Path,
    state: RebaseState,
    *,
    porcelain: Callable[[Path], tuple[bool, str]] | None = None,
    rebase_active: Callable[[Path], bool] = rebase_in_progress,
    merge_status: Callable[[Path], str] = merge_state,
) -> IntegrationResolutionVerdict:
    """Return the fail-closed dispatch verdict for ``root`` and ``state``.

    Full porcelain is ground-truth integration evidence. Any staged, tracked,
    or untracked change can conceal an unfinished rebase/merge result, so a
    non-empty output blocks ordinary dispatch. Any failed inspection remains
    unsafe.
    """
    if state.resolution_exhausted:
        return IntegrationResolutionVerdict(
            EXHAUSTED,
            (state.resolution_exhaustion_reason or "conflict resolver exhausted",),
        )

    reasons: list[str] = []
    if state.integration_unresolved:
        reasons.append("persisted integration state is unresolved")
    # Non-repository orchestration contexts (unit seams and initial project
    # setup) have no live Git integration state to inspect. Persisted conflict
    # evidence remains blocking even in these synthetic contexts.
    if porcelain is None and (
        not isinstance(root, Path) or not (root / ".git").exists()
    ):
        return _verdict_from_persisted_reasons(reasons)
    probe = porcelain or _full_porcelain
    try:
        readable, porcelain_output = probe(root)
    except Exception:
        readable = False
    if not readable:
        reasons.append("unable to inspect full git porcelain status")
    elif porcelain_output.strip():
        reasons.append("working tree is not clean")
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
    return _verdict_from_persisted_reasons(reasons)


def _verdict_from_persisted_reasons(reasons: list[str]) -> IntegrationResolutionVerdict:
    """Build the ordinary verdict after all available evidence was collected."""
    if reasons:
        return IntegrationResolutionVerdict(RECOVERABLE, tuple(reasons), RESOLUTION_DRAIN)
    return IntegrationResolutionVerdict(RESOLVED)


def assert_non_resolution_dispatch_allowed(
    phase: str,
    verdict: IntegrationResolutionVerdict,
) -> None:
    """Reject every ordinary phase when the integration invariant blocks it."""
    if phase != RESOLUTION_DRAIN and not verdict.dispatch_allowed:
        detail = "; ".join(reason for reason in verdict.reasons if isinstance(reason, str))
        detail = detail or verdict.status
        raise RuntimeError(
            f"cannot dispatch {phase!r}: integration resolution is {verdict.status}: {detail}"
        )


def _full_porcelain(root: Path) -> tuple[bool, str]:
    result = run_git(
        (*COMMIT_PIN_CONFIG_ARGS, "status", "--porcelain"),
        cwd=root,
        label="git-integration-resolution-status",
    )
    return result.returncode == 0, result.stdout
