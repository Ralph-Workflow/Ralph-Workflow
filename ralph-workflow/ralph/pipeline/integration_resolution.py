"""Fail-closed integration-resolution invariant for phase dispatch.

A normal pipeline phase may run only after this module proves that neither
persisted integration state nor the live repository indicates an unfinished
rebase or merge.  The conflict resolver itself is deliberately out of graph;
it is the sole recovery executor named by a blocking verdict.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

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

# ``XY<space>PATH`` -- the shortest meaningful porcelain v1 entry is a
# two-letter status code, a separator, and at least one path character.
_PORCELAIN_MIN_ENTRY_LEN = 4

# Porcelain v1 status codes that denote an unmerged index entry whose two
# halves are not individually ``U``.
_BOTH_SIDES_UNMERGED_CODES = frozenset({"AA", "DD"})


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

    Ground-truth integration evidence is an UNMERGED index entry, an
    in-progress rebase, or an in-progress merge. Ordinary staged, modified,
    or untracked changes are not: they are the normal working state of a
    development phase, which writes files and only commits at the commit
    seam. Treating any non-empty porcelain as blocking deadlocked every run
    whose worktree held uncommitted work -- the recovery executor named by
    the resulting verdict defers on exactly that condition -- so the
    porcelain probe now reports only unmerged paths. Any failed inspection
    remains unsafe.
    """
    persisted = persisted_integration_resolution_verdict(state)
    reasons: list[str] = []
    # Non-repository orchestration contexts (unit seams and initial project
    # setup) have no live Git integration state to inspect. Persisted conflict
    # evidence remains blocking even in these synthetic contexts.
    inspectable = not (
        porcelain is None and (not isinstance(root, Path) or not (root / ".git").exists())
    )
    if not inspectable:
        return persisted if persisted is not None else _verdict_from_persisted_reasons(reasons)
    reasons = _live_integration_reasons(
        root, porcelain=porcelain, rebase_active=rebase_active, merge_status=merge_status
    )
    if persisted is not None:
        if reasons:
            return persisted
        # The record describes a repository that no longer exists. Nothing
        # is unmerged, no rebase or merge is in progress, and the probes
        # that would have said "unreadable" did not. Letting the record
        # outrank that is how one exhausted conflict -- or one stale
        # checkpoint file -- blocked every later run against a clean tree,
        # with no way out but editing the record by hand.
        logger.info(
            "integration resolution: persisted {} evidence describes a repository that is "
            "now clean ({}); the live tree decides",
            persisted.status,
            "; ".join(persisted.reasons) or "no reason recorded",
        )
        return IntegrationResolutionVerdict(RESOLVED)
    return _verdict_from_persisted_reasons(reasons)


def _live_integration_reasons(
    root: Path,
    *,
    porcelain: Callable[[Path], tuple[bool, str]] | None,
    rebase_active: Callable[[Path], bool],
    merge_status: Callable[[Path], str],
) -> list[str]:
    """Collect ground-truth integration evidence; unreadable counts as evidence."""
    reasons: list[str] = []
    probe = porcelain or _full_porcelain
    try:
        readable, porcelain_output = probe(root)
    except Exception:
        readable = False
    if not readable:
        reasons.append("unable to inspect full git porcelain status")
    else:
        unmerged = unmerged_porcelain_paths(porcelain_output)
        if unmerged:
            reasons.append(
                "unmerged paths remain from an unfinished rebase or merge: "
                + ", ".join(unmerged)
            )
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
    return reasons


def unmerged_porcelain_paths(porcelain_output: str) -> tuple[str, ...]:
    """Return the unmerged (conflicted) paths in ``git status --porcelain`` output.

    Porcelain v1 records an unfinished rebase or merge as an unmerged index
    entry: either half of the two-letter status code is ``U``, or the code is
    ``AA`` (both added) or ``DD`` (both deleted). Every other code -- ``M``,
    ``A``, ``D``, ``R``, ``C``, ``??`` -- describes ordinary uncommitted work
    and carries no integration evidence.
    """
    unmerged: list[str] = []
    for raw_line in porcelain_output.splitlines():
        if len(raw_line) < _PORCELAIN_MIN_ENTRY_LEN:
            continue
        code = raw_line[:2]
        if code[0] == "U" or code[1] == "U" or code in _BOTH_SIDES_UNMERGED_CODES:
            unmerged.append(raw_line[3:].strip() or raw_line.strip())
    return tuple(unmerged)


def persisted_integration_resolution_verdict(
    state: RebaseState,
) -> IntegrationResolutionVerdict | None:
    """Return the blocking verdict supported by durable state alone."""
    if state.resolution_exhausted:
        return IntegrationResolutionVerdict(
            EXHAUSTED,
            (state.resolution_exhaustion_reason or "conflict resolver exhausted",),
        )
    if state.integration_unresolved:
        return IntegrationResolutionVerdict(
            RECOVERABLE,
            ("persisted integration state is unresolved",),
            RESOLUTION_DRAIN,
        )
    return None


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
