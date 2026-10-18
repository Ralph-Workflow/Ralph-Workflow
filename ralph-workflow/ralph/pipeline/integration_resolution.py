"""Fail-closed integration-resolution invariant for phase dispatch.

A normal pipeline phase may run only after this module proves that neither
persisted integration state nor the live repository indicates an unfinished
rebase or merge.  The conflict resolver itself is deliberately out of graph;
it is the sole recovery executor named by a blocking verdict.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.hardening import COMMIT_PIN_CONFIG_ARGS
from ralph.git.merge import MERGE_STATE_NONE, merge_state
from ralph.git.rebase.rebase import rebase_in_progress
from ralph.git.subprocess_runner import run_git
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.pipeline.integration_dispatch_blocked_error import IntegrationDispatchBlockedError
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


@dataclass(frozen=True)
class TreeReadinessAssessment:
    """Readiness assessment distinguishing development dispatch from integration readiness."""

    development_dispatch_allowed: bool
    integration_ready: bool
    blocking_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class _AssessedIntegrationResolutionVerdict(IntegrationResolutionVerdict):
    """Integration resolution verdict carrying structured tree readiness."""

    development_dispatch_allowed: bool = True
    integration_ready: bool = True
    blocking_reasons: tuple[str, ...] = ()


def _inspect_porcelain_uncommitted_state(porcelain_output: str) -> tuple[bool, bool]:
    """Return (has_staged, has_modified_tracked) from porcelain v1 lines."""
    has_staged = False
    has_modified_tracked = False
    for raw_line in porcelain_output.splitlines():
        if len(raw_line) < _PORCELAIN_MIN_ENTRY_LEN:
            continue
        code = raw_line[:2]
        x, y = code[0], code[1]
        if (x, y) == ("?", "?") or (x, y) == ("!", "!"):
            continue
        if x in ("M", "A", "D", "R", "C", "T"):
            has_staged = True
        if y in ("M", "D", "T"):
            has_modified_tracked = True
    return has_staged, has_modified_tracked


def _check_live_dispatch_reasons(
    porcelain_output: str,
    root: Path,
    *,
    rebase_active: Callable[[Path], bool],
    merge_status: Callable[[Path], str],
) -> list[str]:
    """Collect dispatch blocking reasons (unmerged, rebase, merge)."""
    dispatch_reasons: list[str] = []
    unmerged = unmerged_porcelain_paths(porcelain_output)
    if unmerged:
        dispatch_reasons.append(
            "unmerged paths remain from an unfinished rebase or merge: " + ", ".join(unmerged)
        )
    try:
        if rebase_active(root):
            dispatch_reasons.append("rebase is in progress")
    except Exception:
        dispatch_reasons.append("unable to inspect rebase state")
    try:
        if merge_status(root) != MERGE_STATE_NONE:
            dispatch_reasons.append("merge is in progress or merge state is unreadable")
    except Exception:
        dispatch_reasons.append("unable to inspect merge state")
    return dispatch_reasons


def assess_tree_readiness(
    root: Path,
    state: RebaseState | None = None,
    *,
    porcelain: Callable[[Path], tuple[bool, str]] | None = None,
    rebase_active: Callable[[Path], bool] = rebase_in_progress,
    merge_status: Callable[[Path], str] = merge_state,
) -> TreeReadinessAssessment:
    """Assess live repository readiness for development dispatch vs auto-integration.

    Development dispatch allows ordinary uncommitted work (modified, staged,
    untracked) because development phases edit and stage files before
    committing them.

    Auto-integration requires the tree to be clean of tracked changes (no
    staged work, no modified tracked files), no unmerged conflicts, no active
    rebase or merge, and no retained durable integration ownership. Untracked
    files do not block integration.
    """
    ownership = retained_integration_reason(root)
    if ownership is not None:
        return TreeReadinessAssessment(
            development_dispatch_allowed=False,
            integration_ready=False,
            blocking_reasons=(ownership,),
        )
    inspectable = not (
        porcelain is None and (not isinstance(root, Path) or not (root / ".git").exists())
    )
    if not inspectable:
        if state is not None:
            persisted = persisted_integration_resolution_verdict(state)
            if persisted is not None:
                return TreeReadinessAssessment(
                    development_dispatch_allowed=persisted.dispatch_allowed,
                    integration_ready=False,
                    blocking_reasons=persisted.reasons,
                )
        return TreeReadinessAssessment(
            development_dispatch_allowed=True,
            integration_ready=True,
            blocking_reasons=(),
        )

    probe = porcelain or _full_porcelain
    try:
        readable, porcelain_output = probe(root)
    except Exception:
        readable = False
        porcelain_output = ""

    if not readable:
        return TreeReadinessAssessment(
            development_dispatch_allowed=False,
            integration_ready=False,
            blocking_reasons=("unable to inspect full git porcelain status",),
        )

    dispatch_reasons = _check_live_dispatch_reasons(
        porcelain_output, root, rebase_active=rebase_active, merge_status=merge_status
    )
    if dispatch_reasons:
        return TreeReadinessAssessment(
            development_dispatch_allowed=False,
            integration_ready=False,
            blocking_reasons=tuple(dispatch_reasons),
        )

    has_staged, has_modified_tracked = _inspect_porcelain_uncommitted_state(porcelain_output)
    integration_blocking: list[str] = []
    if has_staged:
        integration_blocking.append("staged uncommitted work blocks integration")
    if has_modified_tracked:
        integration_blocking.append(
            "worktree not clean; uncommitted tracked changes deferred catch-up integration"
        )

    return TreeReadinessAssessment(
        development_dispatch_allowed=True,
        integration_ready=not bool(integration_blocking),
        blocking_reasons=tuple(integration_blocking),
    )


def _clean_assessed_verdict() -> _AssessedIntegrationResolutionVerdict:
    return _AssessedIntegrationResolutionVerdict(
        RESOLVED,
        (),
        None,
        development_dispatch_allowed=True,
        integration_ready=True,
        blocking_reasons=(),
    )


def _log_persisted_override(
    persisted: IntegrationResolutionVerdict, readiness: TreeReadinessAssessment
) -> None:
    if readiness.integration_ready:
        logger.info(
            "integration resolution: persisted {} evidence describes a repository that is "
            "now clean ({}); the live tree decides",
            persisted.status,
            "; ".join(persisted.reasons) or "no reason recorded",
        )
    else:
        logger.info(
            "integration resolution: persisted {} evidence describes a repository with "
            "uncommitted work ({}); development dispatch allowed, live tree decides",
            persisted.status,
            "; ".join(readiness.blocking_reasons),
        )


def inspect_integration_resolution(
    root: Path,
    state: RebaseState,
    *,
    porcelain: Callable[[Path], tuple[bool, str]] | None = None,
    rebase_active: Callable[[Path], bool] = rebase_in_progress,
    merge_status: Callable[[Path], str] = merge_state,
) -> _AssessedIntegrationResolutionVerdict:
    """Return the fail-closed dispatch verdict for ``root`` and ``state``.

    Durable integration ownership blocks dispatch even on a clean tree.
    Without ownership, Git evidence is an UNMERGED index entry, an
    in-progress rebase, or an in-progress merge. Ordinary staged, modified,
    or untracked changes are not: they are the normal working state of a
    development phase, which writes files and only commits at the commit
    seam. Treating any non-empty porcelain as blocking deadlocked every run
    whose worktree held uncommitted work -- the recovery executor named by
    the resulting verdict defers on exactly that condition -- so the
    porcelain probe now reports only unmerged paths. Any failed inspection
    remains unsafe.
    """
    ownership = retained_integration_reason(root)
    if ownership is not None:
        return _AssessedIntegrationResolutionVerdict(
            RECOVERABLE,
            (ownership,),
            RESOLUTION_DRAIN,
            development_dispatch_allowed=False,
            integration_ready=False,
            blocking_reasons=(ownership,),
        )
    persisted = persisted_integration_resolution_verdict(state)
    inspectable = not (
        porcelain is None and (not isinstance(root, Path) or not (root / ".git").exists())
    )
    if not inspectable:
        if persisted is not None:
            return _AssessedIntegrationResolutionVerdict(
                persisted.status,
                persisted.reasons,
                persisted.recovery_executor,
                development_dispatch_allowed=persisted.dispatch_allowed,
                integration_ready=False,
                blocking_reasons=persisted.reasons,
            )
        return _clean_assessed_verdict()

    readiness = assess_tree_readiness(
        root,
        state=state,
        porcelain=porcelain,
        rebase_active=rebase_active,
        merge_status=merge_status,
    )
    if not readiness.development_dispatch_allowed:
        status = persisted.status if persisted is not None else RECOVERABLE
        reasons = persisted.reasons if persisted is not None else readiness.blocking_reasons
        executor = persisted.recovery_executor if persisted is not None else RESOLUTION_DRAIN
        return _AssessedIntegrationResolutionVerdict(
            status,
            reasons,
            executor,
            development_dispatch_allowed=False,
            integration_ready=False,
            blocking_reasons=readiness.blocking_reasons,
        )

    if persisted is not None:
        _log_persisted_override(persisted, readiness)

    return _AssessedIntegrationResolutionVerdict(
        RESOLVED,
        (),
        None,
        development_dispatch_allowed=True,
        integration_ready=readiness.integration_ready,
        blocking_reasons=readiness.blocking_reasons,
    )


def retained_integration_reason(
    root: object,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> str | None:
    """A durable receipt owns dispatch until recovery proves and clears it."""
    from ralph.pipeline.auto_integrate_record import (
        RecordReadOutcomeDisposition,
        read_record_outcome,
        record_path,
    )

    if not isinstance(root, Path):
        return None
    try:
        outcome = read_record_outcome(
            root,
            backend=backend,
            busy_clock=time.monotonic,
        )
        if outcome.disposition in (
            RecordReadOutcomeDisposition.PRESENT,
            RecordReadOutcomeDisposition.UNREADABLE,
            RecordReadOutcomeDisposition.BUSY,
        ):
            return (
                "durable integration record retained; recovery must finish before ordinary dispatch"
            )
        record_path(root).lstat()
    except FileNotFoundError:
        return None
    except OSError:
        return "durable integration ownership unreadable; recovery required"
    return "durable integration record retained; recovery must finish before ordinary dispatch"


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
                "unmerged paths remain from an unfinished rebase or merge: " + ", ".join(unmerged)
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


def _verdict_from_persisted_reasons(reasons: list[str]) -> _AssessedIntegrationResolutionVerdict:
    """Build the ordinary verdict after all available evidence was collected."""
    if reasons:
        return _AssessedIntegrationResolutionVerdict(
            RECOVERABLE,
            tuple(reasons),
            RESOLUTION_DRAIN,
            development_dispatch_allowed=False,
            integration_ready=False,
            blocking_reasons=tuple(reasons),
        )
    return _AssessedIntegrationResolutionVerdict(
        RESOLVED,
        (),
        None,
        development_dispatch_allowed=True,
        integration_ready=True,
        blocking_reasons=(),
    )


def assert_non_resolution_dispatch_allowed(
    phase: str,
    verdict: IntegrationResolutionVerdict,
) -> None:
    """Reject every ordinary phase when the integration invariant blocks it."""
    if phase != RESOLUTION_DRAIN and not verdict.dispatch_allowed:
        raise IntegrationDispatchBlockedError(phase, verdict)


def _full_porcelain(root: Path) -> tuple[bool, str]:
    result = run_git(
        (*COMMIT_PIN_CONFIG_ARGS, "status", "--porcelain"),
        cwd=root,
        label="git-integration-resolution-status",
    )
    return result.returncode == 0, result.stdout
