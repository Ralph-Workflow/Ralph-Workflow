"""How a failed rebase is classified for the auto-integration engine.

A rebase fails for two very different reasons: the CONTENT of the replay
conflicted with the target, or the ENVIRONMENT (a lock, a full disk, a
killed process) refused the operation. Only the second kind is
environmental, and telling the two apart is what lets the engine record
an operator-actionable reason instead of presenting an infrastructure
failure as a content conflict.

This module owns that classification and the durable skip state it
records. The engine that drives the attempt -- and every decision that
follows from it -- stays in
:mod:`ralph.pipeline.auto_integrate_rebase_merge`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.git.rebase._rebase_kind import RebaseKind
from ralph.pipeline.auto_integrate_outcome import record_skip
from ralph.pipeline.rebase_state import ResolutionFailureCause

if TYPE_CHECKING:
    from ralph.git.rebase.rebase import RebaseFailed
    from ralph.pipeline.rebase_state import RebaseState

#: The rebase failure kinds that describe the ENVIRONMENT rather than the
#: content: a concurrent operation, a failed reference update, a corrupt
#: object store, or an environment-level failure.
_ENVIRONMENTAL_REBASE_KINDS = frozenset(
    {
        RebaseKind.CONCURRENT_OPERATION,
        RebaseKind.ENVIRONMENT_FAILURE,
        RebaseKind.REFERENCE_UPDATE_FAILED,
        RebaseKind.REPOSITORY_CORRUPT,
    }
)

#: The stderr markers that classify an UNKNOWN rebase failure as an
#: environment problem rather than a content conflict.
_ENVIRONMENTAL_IO_MARKERS = (
    "disk full",
    "filesystem",
    "i/o error",
    "io error",
    "no space left",
    "permission denied",
    "read-only",
    "too many open files",
    "worktree directory was deleted",
)


def _rebase_failure_detail(failure: RebaseFailed) -> str:
    """Return the current observation carried by a classified rebase failure."""
    metadata = failure.kind.metadata
    for key in ("reason", "details", "operation"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return failure.kind.kind.value


def _environmental_rebase_reason(failure: RebaseFailed) -> str | None:
    """Return an environmental reason, or ``None`` for another failed rebase."""
    kind = failure.kind.kind
    detail = _rebase_failure_detail(failure)
    if kind in _ENVIRONMENTAL_REBASE_KINDS:
        category = kind.value
    elif kind is RebaseKind.UNKNOWN:
        lowered = detail.lower()
        if "timed out" in lowered or "timeout" in lowered:
            category = "timeout"
        elif "killed" in lowered or "sigkill" in lowered:
            category = "killed"
        elif "lock" in lowered or "concurrent" in lowered:
            category = "concurrent_operation"
        elif any(marker in lowered for marker in _ENVIRONMENTAL_IO_MARKERS):
            category = "io_error"
        else:
            return None
    else:
        return None
    return f"rebase failed for an environmental reason ({category}): {detail}"


def _failed_rebase_reason(failure: RebaseFailed) -> str:
    """Return the operator-visible reason for any non-conflict rebase failure."""
    environmental = _environmental_rebase_reason(failure)
    if environmental is not None:
        return environmental
    return f"rebase failed ({failure.kind.kind.value}): {_rebase_failure_detail(failure)}"


def _failed_rebase_state(failure: RebaseFailed, target: str) -> RebaseState:
    """Record a failed rebase without presenting it as a content conflict."""
    return record_skip(
        reason=_failed_rebase_reason(failure),
        target=target,
        failure_cause=ResolutionFailureCause.CONTEXT_FAILURE,
    )
